"""Explicit offline replay of a saved Geometry bundle against its original PDF.

This is not a new upload or a GT evaluation. Never overwrites saved results.
"""
import argparse,json,sys,uuid
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from phase1_pdf import render_page
from backend.architectural_spatial import analyze_spatial

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--geometry',required=True);parser.add_argument('--pdf',required=True);parser.add_argument('--output',required=True);a=parser.parse_args()
    out=Path(a.output)
    if out.exists():parser.error('Output exists; choose a new filename to preserve earlier results.')
    geometry=json.loads(Path(a.geometry).read_text());binding={**geometry['source_binding'],'analysis_run_id':uuid.uuid4().hex};pdf=Path(a.pdf).read_bytes();image=render_page(pdf,binding['page'],binding['extraction_zoom'])
    result=analyze_spatial(pdf,image.png,binding,geometry)
    result['execution_provenance']={'mode':'OFFLINE_REPLAY_OF_SAVED_GEOMETRY','input_geometry_run_id':geometry['run_id'],'PDF_and_native_image_validated_against_saved_frame':True,'OCR_or_Geometry_or_VLM_rerun':False,'not_a_new_HTTPS_run':True}
    out.parent.mkdir(parents=True,exist_ok=True);out.write_text(json.dumps(result,ensure_ascii=False,allow_nan=False,separators=(',',':')))
    print(json.dumps({'output':str(out),'status':result['status'],'summary':result['summary'],'runtime_seconds':result['runtime_seconds']},ensure_ascii=False))
