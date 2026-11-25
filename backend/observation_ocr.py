"""Checkpointed adapter for the existing PP-OCRv5 runner, no model changes."""
import argparse,contextlib,hashlib,json,os,sys,tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT=Path(__file__).resolve().parents[1]
os.environ['PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK']='True'
os.environ['HF_HUB_OFFLINE']='1';os.environ['TRANSFORMERS_OFFLINE']='1'
sys.path.insert(0,str(ROOT))
from backend.observation_audit import normalized_text


def recover(output,input_sha,reason=None):
    """Recover only same-input checkpoints; incomplete recognition is not text."""
    state=json.loads(Path(output).read_text())
    if state['input_crop_sha256']!=input_sha:raise ValueError('OCR checkpoint input mismatch')
    if reason:
        state['reason']=reason
        state['failure_stage']=state.get('active_stage','UNKNOWN')
        for phase in list(state['stages'].values())+list(state.get('model_stages',{}).values()):
            if phase['status']=='RUNNING':phase.update(status='FAILED',reason=reason)
        if state['stages']['detection']['status']=='COMPLETED':
            for detection in state['detection_regions']:
                if detection['recognition_status'] in ['NOT_RUN','RUNNING']:detection.update(recognition_status='FAILED',failure_reason=reason)
    failed=sum(d['recognition_status']!='COMPLETED' for d in state['detection_regions'])
    detection_complete=state['stages']['detection']['status']=='COMPLETED'
    state['status']='COMPLETED' if detection_complete and not failed and not reason else 'PARTIAL' if detection_complete else 'FAILED'
    if detection_complete:
        state['stages']['recognition'].update(status='PARTIAL' if failed else 'COMPLETED',completed=len(state['regions']),failed=failed)
    return state


def run(image_path,output,ocr_runner=None):
    if ocr_runner is None:
        from poc.run import ocr,MODELS
        ocr_runner=ocr
    else:MODELS={'det':['SYNTHETIC_TEST_ONLY'],'rec':['SYNTHETIC_TEST_ONLY']}
    import cv2
    import numpy as np
    data=Path(image_path).read_bytes();image_sha=hashlib.sha256(data).hexdigest()
    state={'status':'RUNNING','regions':[],'detection_regions':[],'models':{k:MODELS[k] for k in ['det','rec']},
        'device':'cpu','input_crop_sha256':image_sha,'raw_detection':{},
        'stages':{'detection':{'status':'NOT_RUN'},'recognition':{'status':'NOT_RUN'}},
        'model_stages':{'detection_model':{'status':'NOT_RUN'},'recognition_model':{'status':'NOT_RUN'}},
        'score_semantics':'uncalibrated model score, not accuracy or spatial grounding'}
    output=Path(output)
    def save():
        tmp=output.with_suffix('.tmp');tmp.write_text(json.dumps(state,ensure_ascii=False,allow_nan=False));tmp.replace(output)
    def stage(name,index=None):
        previous=state.get('active_stage')
        if previous in state['model_stages']:state['model_stages'][previous]['status']='COMPLETED'
        state['active_stage']=name
        if name in state['model_stages']:state['model_stages'][name]['status']='RUNNING'
        else:state['stages'][name]['status']='RUNNING'
        if index is not None and index<len(state['detection_regions']):state['detection_regions'][index]['recognition_status']='RUNNING'
        save()
    def detection(raw):
        state['raw_detection']={k:raw[k] for k in ['dt_polys','dt_scores'] if k in raw}
        state['detection_regions']=[{'id':f'crop:text:{i:04d}','polygon_native':p,'recognition_status':'NOT_RUN',
            'detection_score':raw.get('dt_scores',[None]*len(raw.get('dt_polys',[])))[i]} for i,p in enumerate(raw.get('dt_polys',[]))]
        state['stages']['detection']={'status':'COMPLETED','regions':len(state['detection_regions'])};save()
    def recognition(entry,crop):
        index=int(entry['id'].rsplit(':',1)[-1]);record={k:entry[k] for k in ['id','polygon_native','text','recognition_score','native_to_rectified_crop','crop_rotated_ccw_90','recognition_seconds','raw_recognition']}
        record.update(crop);record['normalized_text']=normalized_text(entry['text']);record['recognition_status']='COMPLETED'
        state['regions'].append(record);state['detection_regions'][index]['recognition_status']='COMPLETED';save()
    def failed(index,reason):
        state['detection_regions'][index].update(recognition_status='FAILED',failure_reason=reason);save()
    save()
    with tempfile.TemporaryDirectory(prefix='fpue-ocr-') as directory:
        folder=Path(directory);inputs=folder/'input';(inputs/'Target').mkdir(parents=True)
        (inputs/'Target/page.png').write_bytes(data)
        raster=cv2.imdecode(np.frombuffer(data,np.uint8),cv2.IMREAD_COLOR)
        height,width=raster.shape[:2]
        binding={'frame_id':'Target:context_crop','image_sha256':image_sha,'width':width,'height':height}
        (inputs/'manifest.json').write_text(json.dumps({'drawings':{'Target':{'binding':binding,'views':[]}}}))
        args=SimpleNamespace(inputs=inputs,det_dir=ROOT/'poc/models/det',rec_dir=ROOT/'poc/models/rec',out=folder/'result',
            on_stage=stage,on_detection=detection,on_recognition=recognition,on_recognition_error=failed,continue_on_recognition_failure=True)
        try:
            with contextlib.redirect_stdout(sys.stderr):ocr_runner(args)
            details_path=folder/'result/run.json'
            if details_path.is_file():
                details=json.loads(details_path.read_text());state['versions']=details['versions'];state['detection']=details['detection']
            save();state=recover(output,image_sha)
        except Exception as error:
            save();state=recover(output,image_sha,'OCR_'+type(error).__name__)
        save()
    return state


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--image',required=True);p.add_argument('--output',required=True);a=p.parse_args();run(a.image,a.output)
