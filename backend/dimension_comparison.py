"""Printed dimension-label comparison, independent of room completion.

Reads existing OCR; matches dimension baselines, witnessed intervals and a shared
extension-line context before comparing values. Never infers a physical change.
"""
from collections import defaultdict
from dataclasses import dataclass,asdict
import hashlib,math,re
import cv2
import numpy as np
import pymupdf
from poc.space_grounding_geometry import pdf_to_native_matrix,vector_observations,transform_points
from backend.structural_alignment import transform

VERSION='dimension_label_comparison/1'
CONFIRMATION='DIMENSION_TEXT_WITH_PAIRED_LINE_GEOMETRY'

@dataclass(frozen=True)
class Settings:
    minimum_ocr_confidence:float=.85
    line_merge_tolerance_zoom:float=.35
    correspondence_tolerance_zoom:float=2.
    minimum_context_witnesses:int=2
    minimum_total_witnesses:int=4
    maximum_direction_degrees:float=1.5
    minimum_ink_support:float=.80

def native_box(points):
    p=np.asarray(points,float)
    return [*p.min(axis=0).tolist(),*p.max(axis=0).tolist()]

def read_dimension_geometry(pdf,page):
    """Reuse the existing vector extractor, before its geometry-export pruning."""
    with pymupdf.open(stream=pdf,filetype='pdf') as doc:
        raw=doc[page.page_number-1]
        matrix=np.asarray(pdf_to_native_matrix(raw,page.zoom),float)
        vector=vector_observations(raw,matrix)
        lines=[];terminals=[]
        for s in vector['segments']:
            color=s.get('stroke_color');points=np.asarray(s['points'],float)
            if (not color or max(color)>.46 or (s.get('stroke_opacity') or 0)<.8
                or s.get('dash') not in ('[] 0','[] 0.0',None) or s['stroke_px']<=0):continue
            delta=np.abs(points[1]-points[0]);length=float(np.linalg.norm(delta))
            evidence={'source_primitive_id':s['id'],'path_id':s['path_id'],'layer':s['layer'],
                'stroke_width_native':s['stroke_px'],'pdf_points_local':transform_points(points,np.linalg.inv(matrix))}
            if min(delta)<=.15*page.zoom and length>=page.zoom:
                axis=int(delta[1]>delta[0]);along=sorted(points[:,axis].tolist())
                lines.append(dict(id=s['id'],axis=axis,normal=float(points[:,1-axis].mean()),lo=along[0],hi=along[1],
                                  points_native=points.tolist(),source_evidence=evidence))
            elif .7*page.zoom<=length<=8*page.zoom and .45<=delta[0]/max(delta[1],1e-6)<=2.2:
                terminals.append(dict(id=s['id'],kind='SLASH',center=points.mean(axis=0).tolist(),source_evidence=evidence))
        # Small endpoint circles are intentionally below the existing room-symbol
        # extractor's size range. Preserve them as dimension punctuation only.
        for i,path in enumerate(raw.get_drawings()):
            if len(path['items'])!=4 or any(item[0]!='c' for item in path['items']):continue
            color=path.get('color')
            if not color or max(color)>.46 or (path.get('stroke_opacity') or 0)<.8:continue
            rect=path['rect'];p=np.asarray(transform_points([[rect.x0,rect.y0],[rect.x1,rect.y1]],matrix))
            w,h=np.abs(p[1]-p[0])
            if .5*page.zoom<=min(w,h)<=max(w,h)<=4*page.zoom and .75<w/max(h,1e-6)<1.34:
                terminals.append(dict(id=f'pdf_path:{i}:dimension_terminal',kind='CIRCLE',center=p.mean(axis=0).tolist(),
                    source_evidence={'path_id':f'pdf_path:{i}','pdf_bbox_local':list(rect),'bbox_native':native_box(p)}))
    return dict(source_sha256=page.source_sha256,page=page.page_number,extraction_zoom=page.zoom,
        image_size=[page.width,page.height],lines=lines,terminals=terminals,
        algorithm='existing vector_observations + small observed PDF endpoint markers')

def _merge(lines,tolerance):
    """Deduplicate/rejoin observed collinear pieces, preserving all source IDs."""
    bands=[]
    for s in sorted(lines,key=lambda x:x['normal']):
        if not bands or s['normal']-bands[-1][0]['normal']>tolerance:bands.append([])
        bands[-1].append(s)
    out=[]
    for band in bands:
        group=[];end=None
        def save(group):
            return dict(normal=float(np.median([x['normal'] for x in group])),lo=min(x['lo'] for x in group),
                        hi=max(x['hi'] for x in group),refs=[x['id'] for x in group],source_lines=group)
        for s in sorted(band,key=lambda x:x['lo']):
            if group and s['lo']>end+tolerance:out.append(save(group));group=[];end=None
            group.append(s);end=max(end or s['hi'],s['hi'])
        if group:out.append(save(group))
    return out

def _texts(bundle):
    records=[];rejected=[]
    for o in bundle.get('observations',[]):
        if o['observation_type']!='TextRegion':continue
        evidence=o['source_evidence'];raw=evidence.get('normalized_text',evidence.get('text',''))
        if not isinstance(raw,str) or not re.fullmatch(r'(?:[0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)',raw.strip()):continue
        points=o['geometry_native'].get('points_native',[])
        if not points:continue
        coordinates=np.asarray(points,float)
        if coordinates.ndim!=2 or coordinates.shape[1]!=2 or not np.isfinite(coordinates).all():
            rejected.append({'id':o['observation_id'],'reason':'INVALID_OCR_COORDINATES'});continue
        box=native_box(coordinates);w,h=box[2]-box[0],box[3]-box[1]
        conf=o.get('confidence',evidence.get('raw_recognition',{}).get('rec_score'))
        record=dict(id=o['observation_id'],raw_text=raw,value=int(raw.replace(',','')),bbox_native=box,
            polygon_native=points,confidence=conf,frame_id=o['frame_id'],source_role=o['source_role'],
            source_evidence=evidence,ocr_algorithm=evidence.get('algorithm'))
        if w<=0 or h<=0 or w<h or record['value']<=0:rejected.append({**record,'reason':'UNSUPPORTED_ORIENTATION_OR_VALUE'});continue
        records.append(record)
    return records,rejected

def extract_dimensions(bundle,vector,page,settings=Settings()):
    binding=bundle['source_binding'];role=bundle['source_role'];zoom=page.zoom
    if (binding['source_sha256']!=page.source_sha256 or binding['page']!=page.page_number
        or binding['extraction_zoom']!=zoom or [binding['width'],binding['height']]!=[page.width,page.height]
        or binding['image_sha256']!=hashlib.sha256(page.png).hexdigest()
        or vector['source_sha256']!=page.source_sha256 or vector['page']!=page.page_number
        or vector['extraction_zoom']!=zoom or vector['image_size']!=[page.width,page.height]
        or binding.get('source_role')!=role or bundle['frame_id']!=role+':'+binding['image_id']):
        raise ValueError('Dimension evidence belongs to a different PDF/native frame')
    gray=cv2.imdecode(np.frombuffer(page.png,np.uint8),0)
    # Computing distances once avoids reprocessing the image for each witness.
    distance=cv2.distanceTransform((gray>=210).astype(np.uint8),cv2.DIST_L2,cv2.DIST_MASK_PRECISE)
    def supported(points):
        a,b=np.asarray(points,float);length=np.linalg.norm(b-a)
        p=a+np.linspace(0,1,max(3,min(256,round(length))))[:,None]*(b-a);xy=np.rint(p).astype(int)
        if (xy<0).any() or (xy[:,0]>=page.width).any() or (xy[:,1]>=page.height).any():return 0.
        return float(np.mean(distance[xy[:,1],xy[:,0]]<=max(1,.65*zoom)))
    texts,rejected=_texts(bundle);tol=settings.line_merge_tolerance_zoom*zoom
    horizontal=[s for s in vector['lines'] if s['axis']==0]
    vertical=[s for s in vector['lines'] if s['axis']==1]
    baselines=_merge(horizontal,tol)
    dims=[];chains={}
    for t in texts:
        if (t['frame_id']!=bundle['frame_id'] or t['source_role']!=role
            or t['source_evidence'].get('source_sha256')!=binding['source_sha256']
            or t['source_evidence'].get('image_sha256')!=binding['image_sha256']):
            rejected.append({**t,'reason':'OCR_FRAME_MISMATCH'});continue
        x,y,u,v=t['bbox_native'];height=v-y;cx=(x+u)/2
        if x<0 or y<0 or u>page.width or v>page.height:
            rejected.append({**t,'reason':'OCR_OUTSIDE_NATIVE_FRAME'});continue
        if (not isinstance(t['confidence'],(int,float)) or not math.isfinite(t['confidence'])
            or not settings.minimum_ocr_confidence<=t['confidence']<=1):
            rejected.append({**t,'reason':'LOW_OR_MISSING_OCR_CONFIDENCE'});continue
        options=[]
        for chain in baselines:
            normal=chain['normal'];offset=normal-v
            if not (-.12*height<=offset<=.70*height and chain['lo']<=cx<=chain['hi']):continue
            witnesses=[]
            for s in vertical:
                if not (chain['lo']-2*zoom<=s['normal']<=chain['hi']+2*zoom and s['lo']<=normal-.8*height and s['hi']>=normal+.10*height
                        and 1.5*height<=s['hi']-s['lo']<=8*height and s['hi']-normal<=height):continue
                terminals=[a for a in vector['terminals'] if np.linalg.norm(np.array(a['center'])-[s['normal'],normal])<=1.5*zoom]
                if not terminals:continue
                # A perpendicular observed boundary near the origin, outside the
                # dimension row, gives building-geometry context. It is not
                # asserted to be a verified physical wall.
                support=[a for a in horizontal if a['lo']-zoom<=s['normal']<=a['hi']+zoom
                         and 0<=(s['lo']-a['normal'])<=.75*height and a['hi']-a['lo']>=height]
                if not support:continue
                if supported(s['points_native'])<settings.minimum_ink_support:continue
                witnesses.append(dict(position=s['normal'],origin=[s['normal'],s['lo']],
                    end=[s['normal'],s['hi']],refs=[s['id']],terminal_refs=[a['id'] for a in terminals],
                    terminal_kinds=sorted(set(a['kind'] for a in terminals)),context_refs=[a['id'] for a in support]))
            unique=[]
            for w in sorted(witnesses,key=lambda w:w['position']):
                if unique and w['position']-unique[-1]['position']<=tol:
                    unique[-1]['refs']+=w['refs'];unique[-1]['terminal_refs']+=w['terminal_refs'];unique[-1]['context_refs']+=w['context_refs']
                else:unique.append(w)
            if len(unique)<settings.minimum_total_witnesses:continue
            for left,right in zip(unique,unique[1:]):
                lo,hi=left['position'],right['position'];span=hi-lo
                if not lo<=cx<=hi or span<1.2*height or u-x>.90*span or abs(cx-(lo+hi)/2)>min(.20*span,.85*height):continue
                if abs(left['origin'][1]-right['origin'][1])>zoom:continue
                segment=[[lo,normal],[hi,normal]]
                if supported(segment)<settings.minimum_ink_support:continue
                chain_id=hashlib.sha256(str((round(normal,2),chain['refs'])).encode()).hexdigest()[:16]
                chains[chain_id]=dict(id=chain_id,normal=normal,lo=chain['lo'],hi=chain['hi'],refs=chain['refs'],witnesses=unique)
                options.append(dict(text=t,chain_id=chain_id,segment_native=segment,witnesses=[left,right],
                                    text_baseline_offset=offset,ink_support=supported(segment)))
        if len(options)!=1:
            rejected.append({**t,'reason':'AMBIGUOUS_DIMENSION_LINE' if options else 'NO_WITNESSED_DIMENSION_INTERVAL','line_options':len(options)})
        else:dims.append(options[0])
    # Two labels assigned to one slot are ambiguous, never silently picked by
    # text value, distance or confidence.
    groups=defaultdict(list)
    for d in dims:groups[(d['chain_id'],tuple(np.asarray(d['segment_native']).ravel()))].append(d)
    accepted=[]
    for group in groups.values():
        if len(group)==1:accepted+=group
        else:rejected += [{**d['text'],'reason':'MULTIPLE_LABELS_IN_DIMENSION_INTERVAL'} for d in group]
    return dict(source_role=role,frame_id=bundle['frame_id'],source_binding=binding,dimensions=accepted,chains=chains,rejected=rejected)

def compare_dimensions(pages,bundles,alignment,vectors,settings=Settings()):
    audit={'schema_version':VERSION,'settings':asdict(settings),'status':'DEFERRED','sources':[],
           'matches':[],'unmatched':[],'policy':'Printed dimension labels only; no physical movement/area inference.'}
    if alignment['status'] not in ('PARTIAL','VERIFIED_ROI') or not alignment.get('mapping') or not alignment.get('matrix'):
        audit['reason']='NO_SUPPORTED_SOURCE_TRANSFORM';return [],[],audit
    if len(vectors)!=2 or not all(vectors):
        audit['reason']='PDF_VECTOR_DIMENSION_EVIDENCE_UNAVAILABLE';return [],[],audit
    if [g['source_role'] for g in bundles]!=['Ref','Target']:
        raise ValueError('Independent Ref/Target dimension sources required')
    if alignment.get('sources') and [s['frame_id'] for s in alignment['sources']]!=[g['frame_id'] for g in bundles]:
        raise ValueError('Dimension transform belongs to different source frames')
    if not all(any(o['observation_type']=='TextRegion' for o in g.get('observations',[])) for g in bundles):
        audit['reason']='NO_CURRENT_OCR_OBSERVATIONS';return [],[],audit
    extracted=[extract_dimensions(g,v,p,settings) for g,v,p in zip(bundles,vectors,pages)]
    audit['sources']=extracted
    M=np.asarray(alignment['matrix'],float);tol=max(settings.correspondence_tolerance_zoom*pages[0].zoom,alignment['settings']['normal_tolerance_ref_px'])
    chain_pairs=[]
    for rc in extracted[0]['chains'].values():
        for tc in extracted[1]['chains'].values():
            # No transform is fitted from the changing labels or endpoints.
            base=transform([[tc['lo'],tc['normal']],[tc['hi'],tc['normal']]],M)
            direction=base[1]-base[0]
            if abs(math.degrees(math.atan2(direction[1],direction[0])))>settings.maximum_direction_degrees:continue
            if max(abs(base[:,1]-rc['normal']))>tol:continue
            matches=[]
            for i,rw in enumerate(rc['witnesses']):
                options=[j for j,tw in enumerate(tc['witnesses']) if np.linalg.norm(transform([tw['origin']],M)[0]-rw['origin'])<=tol]
                if len(options)!=1:continue
                j=options[0];reverse=[k for k,w in enumerate(rc['witnesses']) if np.linalg.norm(transform([tc['witnesses'][j]['origin']],M)[0]-w['origin'])<=tol]
                if reverse==[i]:matches.append((i,j))
            if len(matches)>=settings.minimum_total_witnesses:chain_pairs.append((rc,tc,matches))
    # Reciprocal unique chain matching, not a nearest-number match.
    rc_count=defaultdict(int);tc_count=defaultdict(int)
    for r,t,_ in chain_pairs:rc_count[r['id']]+=1;tc_count[t['id']]+=1
    matches=[]
    for rc,tc,witness_pairs in chain_pairs:
        if rc_count[rc['id']]!=1 or tc_count[tc['id']]!=1:continue
        for r in extracted[0]['dimensions']:
            if r['chain_id']!=rc['id']:continue
            possibilities=[]
            for t in extracted[1]['dimensions']:
                if t['chain_id']!=tc['id']:continue
                mapped=transform(t['segment_native'],M)
                if np.max(np.linalg.norm(mapped-r['segment_native'],axis=1))>tol:continue
                endpoints=[w['origin'] for w in r['witnesses']]
                context=[(i,j) for i,j in witness_pairs if all(np.linalg.norm(np.asarray(rc['witnesses'][i]['origin'])-a)>tol for a in endpoints)]
                if len(context)<settings.minimum_context_witnesses:continue
                possibilities.append((t,context))
            if len(possibilities)==1:matches.append((r,possibilities[0][0],rc,tc,possibilities[0][1]))
    target_uses=defaultdict(int)
    for _,t,*_ in matches:target_uses[t['text']['id']]+=1
    entries=[];objects=[];roi=np.float32(alignment['target_roi']['vertices']);frame_ids=[g['frame_id'] for g in bundles]
    for r,t,rc,tc,context in matches:
        if target_uses[t['text']['id']]!=1:continue
        rb,tb=r['text']['bbox_native'],t['text']['bbox_native']
        if any(cv2.pointPolygonTest(roi,tuple(map(float,p)),False)<0 for p in [[tb[0],tb[1]],[tb[2],tb[1]],[tb[2],tb[3]],[tb[0],tb[3]]]):continue
        evidence=dict(validation_status='SUPPORTED',method='RECIPROCAL_DIMENSION_CHAIN_AND_INTERVAL',
            frame_ids=frame_ids,Ref=r,Target=t,Ref_chain=rc,Target_chain=tc,context_witness_pairs=context,
            maximum_endpoint_residual_ref_px=float(np.max(np.linalg.norm(transform(t['segment_native'],M)-r['segment_native'],axis=1))),
            geometry_tolerance_ref_px=tol,alignment_status=alignment['status'],
            alignment_transform_target_to_ref=M.tolist(),coordinate_scope='LOCAL_DIMENSION_GEOMETRY',
            terminal_evidence_not_physical_endpoints=True,physical_change_inferred=False)
        audit['matches'].append(dict(Ref_text_id=r['text']['id'],Target_text_id=t['text']['id'],
                                    before=r['text']['raw_text'],after=t['text']['raw_text'],evidence=evidence))
        if r['text']['value']==t['text']['value']:continue
        ident='D'+hashlib.sha256((r['text']['id']+'|'+t['text']['id']).encode()).hexdigest()[:16]
        refs=[]
        for role,d,chain in [('Ref',r,rc),('Target',t,tc)]:
            refs += [d['text']['id']]+[role+':'+v for v in chain['refs']]
            refs += [role+':'+v for w in d['witnesses'] for v in w['refs']+w['terminal_refs']+w['context_refs']]
            objects.append(dict(id=d['text']['id'],kind='DimensionText',name=d['text']['raw_text'],bbox=d['text']['bbox_native'],
                refs=[d['text']['id']],context_refs=[],hosts=[],spaces=[],parent=None,supported=True,
                native_observations={'text_region':d['text'],'dimension_geometry':d},source_role=role))
        entries.append(dict(correspondence_id=ident,entity_type='DimensionText',ref_ids=[r['text']['id']],target_ids=[t['text']['id']],
            cardinality='1:1',identity_status='SUPPORTED',judgment='CHANGED',domain='ANNOTATION',state='CONFIRMED',
            automatic_confirmation=CONFIRMATION,change_types=['DIMENSION_TEXT_VALUE'],measured_facets=['PRINTED_DIMENSION_TEXT'],
            before=r['text']['raw_text'],after=t['text']['raw_text'],ref_bbox_native=rb,
            verified_target_bbox_native=tb,alignment_fit_parent=False,scope_status='VERIFIED_LOCAL_DIMENSION_GEOMETRY',
            space_name=None,change_description=f'치수 표기가 {r["text"]["raw_text"]}에서 {t["text"]["raw_text"]}으로 변경되었습니다.',
            decision_reason='같은 치수선의 구간, 양끝 보조선과 표식, 별도 주변 보조선·경계 근거로 대응을 확인했습니다. 물리적 벽 이동·면적 변경은 추론하지 않았습니다.',
            evidence_refs=list(dict.fromkeys(refs)),dimension_evidence=evidence,physical_change_inferred=False,
            source_observations={'readings':[r['text']['raw_text'],t['text']['raw_text']],'description':'원본 OCR 치수 표기와 독립 벡터 치수선 근거'},
            measurements={'before_numeric':r['text']['value'],'after_numeric':t['text']['value'],'units':'AS_PRINTED_NOT_ASSUMED',
                          'ref_frame_id':frame_ids[0],'target_frame_id':frame_ids[1]},alternatives=[]))
    audit['status']='COMPLETED_WITH_SUPPORTED_LABEL_MATCHES' if audit['matches'] else 'NO_SUPPORTED_DIMENSION_MATCHES'
    matched={m[k] for m in audit['matches'] for k in ['Ref_text_id','Target_text_id']}
    audit['unmatched']=[{'id':d['text']['id'],'source_role':s['source_role'],'reason':'NO_UNIQUE_CHAIN_INTERVAL_COUNTERPART'} for s in extracted for d in s['dimensions'] if d['text']['id'] not in matched]
    return entries,objects,audit


def is_supported_dimension_change(row):
    return (row.get('domain')=='ANNOTATION' and row.get('entity_type')=='DimensionText'
        and row.get('automatic_confirmation')==CONFIRMATION
        and row.get('change_types')==['DIMENSION_TEXT_VALUE']
        and row.get('dimension_evidence',{}).get('validation_status')=='SUPPORTED'
        and row.get('physical_change_inferred') is False
        and isinstance(row.get('before'),str) and isinstance(row.get('after'),str) and row['before']!=row['after'])
