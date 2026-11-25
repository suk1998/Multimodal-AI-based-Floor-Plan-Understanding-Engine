"""Replay native observations against their original PDF; no OCR/ROI generation."""
import argparse,json,sys,time,uuid
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from phase1_pdf import render_page
from backend.architectural_geometry import analyze_geometry


def run(pdf_path,parent_path,output):
    output=Path(output)
    if output.exists():raise FileExistsError('Choose a new output directory; old results are preserved')
    saved=json.loads(Path(parent_path).read_text())
    if 'result' in saved:saved=saved['result']
    parent=saved.get('observation_bundle',saved);binding={**parent['source_binding'],'analysis_run_id':uuid.uuid4().hex}
    pdf=Path(pdf_path).read_bytes();image=render_page(pdf,binding['page'],binding['extraction_zoom'])
    print('START',binding['source_role'],binding['core_bbox_native'],flush=True)
    tick=time.perf_counter();result=analyze_geometry(pdf,image.png,binding,parent)
    output.mkdir(parents=True,exist_ok=False);(output/'source.png').write_bytes(image.png)
    (output/'geometry.json').write_text(json.dumps(result,ensure_ascii=False,allow_nan=False))
    (output/'run.json').write_text(json.dumps({'input_observation_run_id':parent['run_id'],'source_binding':binding,'runtime_seconds':time.perf_counter()-tick,'status':result['status'],'candidate_counts':result['audit']['candidate_counts'],'new_OCR_or_VLM_run':False},ensure_ascii=False,indent=2))
    print(json.dumps(result['audit']['candidate_counts']),flush=True)
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--pdf',required=True);p.add_argument('--observation-bundle',required=True);p.add_argument('--output-dir',required=True);a=p.parse_args()
    run(a.pdf,a.observation_bundle,a.output_dir)
