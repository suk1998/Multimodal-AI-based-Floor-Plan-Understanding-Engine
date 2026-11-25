"""Offline CPU worker reusing the already installed PP-OCRv5 recognizer."""
import argparse,hashlib,json,os,time
from pathlib import Path
os.environ['PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK']='True'
os.environ['HF_HUB_OFFLINE']='1';os.environ['TRANSFORMERS_OFFLINE']='1';os.environ['CUDA_VISIBLE_DEVICES']=''

def main(directory):
    import cv2
    from paddleocr import TextRecognition
    root=Path(__file__).resolve().parents[1];folder=Path(directory)
    requests=json.loads((folder/'input.json').read_text())
    if len(requests)>24:raise ValueError('Symbol reread batch limit exceeded')
    model=TextRecognition(model_name='korean_PP-OCRv5_mobile_rec',model_dir=root/'poc/models/rec',
                          device='cpu',enable_mkldnn=False,cpu_threads=4)
    output=[]
    for item in requests:
        data=Path(item['image']).read_bytes();sha=hashlib.sha256(data).hexdigest()
        if sha!=item['sha256']:raise ValueError('Symbol crop hash mismatch')
        image=cv2.imread(item['image']);image=cv2.resize(image,None,fx=2,fy=2,interpolation=cv2.INTER_CUBIC)
        tick=time.perf_counter();raw=next(iter(model.predict(image))).json
        if isinstance(raw,str):raw=json.loads(raw)
        raw=raw.get('res',raw);raw.pop('input_path',None)
        output.append({'id':item['id'],'text':raw.get('rec_text',''),'score':raw.get('rec_score'),
                       'crop_sha256':sha,'seconds':time.perf_counter()-tick,'raw_recognition':raw})
    (folder/'output.json').write_text(json.dumps({'status':'COMPLETED','results':output},ensure_ascii=False))

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--directory',required=True)
    main(parser.parse_args().directory)
