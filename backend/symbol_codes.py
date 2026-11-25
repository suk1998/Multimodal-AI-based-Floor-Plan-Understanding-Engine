"""Read one printed type code per observed marker, without assigning a door.

Canonical slash codes are keys for the existing ontology vocabulary. No new
material definitions, physical referents or Ref/Target correspondences are made.
"""
import hashlib,json,math,os,re,subprocess,tempfile
from pathlib import Path
import cv2
import numpy as np
import pymupdf
from integrations.fpue_ontology.vocabulary import normalize_label
from integrations.fpue_ontology.detection_contract import interpret_code

ROOT=Path(__file__).resolve().parents[1]
CODE=re.compile(r'(?P<family>FSD|PW|PD|WD|SD|AW|AG|AD)(?:/?(?P<number>[0-9]{1,3}))?')
MAX_REOBSERVATIONS=12

def marker_reading(rows,symbol):
    x,y,u,v=symbol['bbox'];center=np.asarray(symbol['center']);radius=np.array([(u-x)/2,(v-y)/2])
    if min(radius)<=0:return None
    def inside(row):
        b=row['bbox_native'];p=(np.asarray(b[:2])+b[2:])/2
        return bool(np.linalg.norm((p-center)/radius)<.90)
    contained=[r for r in rows if inside(r)]
    words=[(r,CODE.fullmatch(normalize_label(r.get('normalized',r.get('raw',''))).upper())) for r in contained]
    words=[(r,m) for r,m in words if m]
    if len(words)!=1:return None
    word,parsed=words[0];family=parsed['family'];embedded=parsed['number'];box=word['bbox_native']
    wc=np.array([(box[0]+box[2])/2,(box[1]+box[3])/2]);height=box[3]-box[1]
    numbers=[]
    for r in contained:
        raw=normalize_label(r.get('normalized',r.get('raw','')))
        if not re.fullmatch('[0-9]{1,3}',raw):continue
        b=r['bbox_native'];p=np.array([(b[0]+b[2])/2,(b[1]+b[3])/2])
        # Boxes may overlap slightly. Use the two text centers and common marker,
        # not the previous requirement that their axis-aligned boxes be disjoint.
        if abs(p[0]-wc[0])<=.30*(u-x) and abs(p[1]-wc[1])>=.25*height:
            numbers.append((r,raw))
    values=set(n for _,n in numbers)
    if embedded:values.add(embedded)
    number=next(iter(values)) if len(values)==1 else None
    numeric=next((r for r,n in numbers if n==number),word if embedded==number else None) if number else None
    canonical=family+'/'+number if number is not None else None
    refs=list(dict.fromkeys([word['id']]+[r['id'] for r,_ in numbers]))
    return dict(text=word,number=numeric,code=family+(number or ''),canonical_code=canonical,
        ontology_type_code=canonical,code_family=family,type_number=number,
        reading_status='SUPPORTED' if number is not None else 'AMBIGUOUS' if values else 'PARTIAL',
        reading_reasons=[] if number is not None else ['CONFLICTING_NUMBERS_IN_MARKER'] if values else ['TYPE_NUMBER_NOT_OBSERVED'],
        reading_evidence_refs=refs,number_candidates=[{'id':r['id'],'text':n} for r,n in numbers],
        embedded_number=embedded,physical_referent_verified=False,
        ontology_semantics=interpret_code(canonical) if canonical else None)


def run_number_recognition(items):
    """One bounded offline batch using the installed recognition model only."""
    python=ROOT/'poc/.venv/bin/python';model=ROOT/'poc/models/rec/inference.pdiparams'
    if not python.is_file() or not model.is_file():return {'status':'UNAVAILABLE','reason':'EXISTING_RECOGNIZER_UNAVAILABLE','results':[]}
    with tempfile.TemporaryDirectory(prefix='fpue-symbol-reading-') as directory:
        folder=Path(directory);requests=[]
        for i,item in enumerate(items):
            path=folder/f'{i}.png';path.write_bytes(item['png'])
            requests.append({'id':item['id'],'image':str(path),'sha256':hashlib.sha256(item['png']).hexdigest()})
        (folder/'input.json').write_text(json.dumps(requests))
        try:
            process=subprocess.run([str(python),'-B',str(ROOT/'backend/symbol_number_ocr.py'),'--directory',directory],
                cwd=ROOT,capture_output=True,timeout=25,
                env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1','PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK':'True',
                     'HF_HUB_OFFLINE':'1','TRANSFORMERS_OFFLINE':'1','CUDA_VISIBLE_DEVICES':''})
            if process.returncode or not (folder/'output.json').is_file():
                return {'status':'FAILED','reason':'NUMBER_RECOGNITION_WORKER_FAILED','results':[],
                        'diagnostic':process.stderr.decode(errors='replace')[-2000:]}
            return json.loads((folder/'output.json').read_text())
        except subprocess.TimeoutExpired:return {'status':'FAILED','reason':'NUMBER_RECOGNITION_TIMEOUT','results':[]}


def recover_numbers(page,binding,rows,symbols,scope,prefix,recognizer=run_number_recognition):
    """Reobserve missing numbers from this source only; never overwrite OCR.

    Recognition crop extents are explicitly NOT detected text bounding boxes.
    Two views of the same pixels are consistency checks, not independent GT.
    """
    pending=[]
    for s in symbols:
        x,y,u,v=s['bbox']
        if not scope[0]<=x<u<=scope[2] or not scope[1]<=y<v<=scope[3]:continue
        reading=marker_reading(rows,s)
        if reading and reading['reading_status']=='PARTIAL':pending.append((s,reading))
    audit={'status':'NOT_NEEDED','model':'korean_PP-OCRv5_mobile_rec','new_model_installed':False,
           'source_role':binding['source_role'],'frame_id':binding['frame_id'],'source_sha256':binding['source_sha256'],
           'observations':[],'attempts':[],'omitted_due_to_budget':max(0,len(pending)-MAX_REOBSERVATIONS),
           'same_pixel_views_are_not_independent_validation':True,'physical_referent_verified':False}
    if not pending:return [],audit
    pix=page.get_pixmap(matrix=pymupdf.Matrix(binding['extraction_zoom'],binding['extraction_zoom']),alpha=False)
    png=pix.tobytes('png')
    if hashlib.sha256(png).hexdigest()!=binding['image_sha256']:raise ValueError('Symbol reread native frame mismatch')
    image=cv2.imdecode(np.frombuffer(png,np.uint8),1);items=[];attempts=[]
    for s,reading in pending[:MAX_REOBSERVATIONS]:
        x,y,u,v=s['bbox'];w=u-x;h=v-y;cy=(y+v)/2;b=reading['text']['bbox_native']
        above=(b[1]+b[3])/2>=cy
        attempt={'symbol_id':s['id'],'family':reading['code_family'],'status':'PROPOSED','variants':[]}
        for variant,inset in [('tight',.25),('wide',.20)]:
            y0,y1=(y+.08*h,cy-.02*h) if above else (cy+.02*h,v-.08*h)
            box=[math.floor(x+inset*w),math.floor(y0),math.ceil(u-inset*w),math.ceil(y1)]
            a,b,c,d=box
            if not (0<=a<c<=image.shape[1] and 0<=b<d<=image.shape[0]):continue
            crop=cv2.imencode('.png',image[b:d,a:c])[1].tobytes();key=f'{s["id"]}:{variant}'
            item={'id':key,'png':crop};items.append(item)
            attempt['variants'].append({'id':key,'bbox_native':box,'crop_sha256':hashlib.sha256(crop).hexdigest(),
                'geometry_semantics':'RECOGNITION_CROP_EXTENT_NOT_DETECTED_TEXT_BOX',
                'crop_to_native':[[1,0,a],[0,1,b],[0,0,1]],'recognizer_scale':2,
                'recognizer_to_native':[[.5,0,a],[0,.5,b],[0,0,1]]})
        attempts.append(attempt)
    response=recognizer(items);audit.update(status=response['status'],reason=response.get('reason'),attempts=attempts)
    results={r['id']:r for r in response.get('results',[])};additions=[]
    for attempt in attempts:
        candidates=[]
        for variant in attempt['variants']:
            raw=results.get(variant['id'],{});variant['recognition']=raw
            text=normalize_label(raw.get('text',''));score=raw.get('score')
            if (raw.get('crop_sha256')==variant['crop_sha256'] and re.fullmatch('[0-9]{1,3}',text)
                and isinstance(score,(int,float)) and math.isfinite(score) and .95<=score<=1):candidates.append((text,score,variant))
        if len(candidates)!=2 or len(set(c[0] for c in candidates))!=1:
            attempt.update(status='HOLD',reason='MISSING_LOW_CONFIDENCE_OR_CONFLICTING_LOCAL_READINGS');continue
        text,_,variant=candidates[0];x,y,u,v=variant['bbox_native'];ident=prefix+':number-reread:'+attempt['symbol_id']
        row={'id':ident,'raw':text,'normalized':text,'bbox_native':variant['bbox_native'],
             'polygon_native':[[x,y],[u,y],[u,v],[x,v]],'recognition_score':min(c[1] for c in candidates),
             'source_kind':'OCR_LOCAL_RECOGNITION','source_role':binding['source_role'],'frame_id':binding['frame_id'],
             'source_sha256':binding['source_sha256'],'image_sha256':binding['image_sha256'],
             'page':binding['page'],'extraction_zoom':binding['extraction_zoom'],
             'geometry_semantics':'RECOGNITION_CROP_EXTENT_NOT_DETECTED_TEXT_BOX',
             'algorithm':'existing korean_PP-OCRv5_mobile_rec on opposite marker cell',
             'claim_status':'PROPOSED','verification_status':'PROPOSED','reobservation_evidence':attempt['variants']}
        additions.append(row);attempt.update(status='OBSERVED_UNVERIFIED',text=text,observation_id=ident)
    audit['observations']=additions
    return additions,audit
