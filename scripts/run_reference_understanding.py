"""Run actual Reference restoration/reobservation and preserve review artifacts.

Reuses frame-bound PP-OCRv5 observations. Does not use Target, GT or VLM output.
The isolated existing RDF environment is used only after native computation.
"""
import argparse
import copy
import hashlib
import html
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import cv2
import numpy as np
from phase1_pdf import render_page
from backend.reference_restoration import restore_reference
from backend.reference_ontology_adapter import build_region_graphs


def _write_json(path, data):
    with Path(path).open('x', encoding='utf-8') as stream:
        json.dump(data, stream, ensure_ascii=False, allow_nan=False, separators=(',', ':'))


def _dashed(image, points, color, width=2):
    a, b = np.asarray(points, float)
    length = float(np.linalg.norm(b-a))
    if length == 0:
        return
    for offset in np.arange(0, length, 12):
        start = a+(b-a)*offset/length
        end = a+(b-a)*min(length, offset+7)/length
        cv2.line(image, tuple(np.rint(start).astype(int)), tuple(np.rint(end).astype(int)), color, width, cv2.LINE_AA)


def render_review(bundle, source_png, output):
    output = Path(output)
    source = cv2.imdecode(np.frombuffer(source_png, np.uint8), cv2.IMREAD_COLOR)
    views = {}
    for revision in ['before', 'after']:
        image = source.copy()
        for index, case in enumerate(bundle['cases']):
            result = case[revision]
            proposed = result['status'] == 'REGION_PROPOSED'
            color = (190, 45, 160) if revision == 'before' else (110, 150, 0) if proposed else (0, 145, 220)
            if result.get('polygon_native'):
                ring = np.rint(result['polygon_native']).astype(np.int32)
                tint = image.copy()
                cv2.fillPoly(tint, [ring], color)
                image = cv2.addWeighted(image, .88, tint, .12, 0)
                cv2.polylines(image, [ring], True, color, 2, cv2.LINE_AA)
                for edge in result.get('edges', []):
                    for start, end in edge['inferred_intervals']:
                        points = [[start, edge['normal']], [end, edge['normal']]] if edge['axis'] == 'H' else [[edge['normal'], start], [edge['normal'], end]]
                        _dashed(image, points, (0, 105, 245), 3)
            seed = tuple(np.rint(case['label']['seed']).astype(int))
            cv2.circle(image, seed, 4, (0, 80, 220), -1)
            cv2.putText(image, f'R{index:02d}', (seed[0]+7, seed[1]-8), cv2.FONT_HERSHEY_SIMPLEX, .5, (20, 20, 20), 2, cv2.LINE_AA)
        views[revision] = image
        cv2.imwrite(str(output/f'Ref_{revision}_overlay.png'), image)
    rows, cards, crop_manifest = [], [], []
    for index, case in enumerate(bundle['cases']):
        points = [case['label']['seed']]
        for revision in ['before', 'after']:
            points += case[revision].get('polygon_native') or []
        array = np.asarray(points)
        pad = max(40, case['label']['text_height']*3)
        box = [max(0, int(np.floor(array[:, 0].min()-pad))), max(0, int(np.floor(array[:, 1].min()-pad))),
            min(source.shape[1], int(np.ceil(array[:, 0].max()+pad))), min(source.shape[0], int(np.ceil(array[:, 1].max()+pad)))]
        x0, y0, x1, y1 = box
        for name, image in [('source', source), ('before', views['before']), ('after', views['after'])]:
            cv2.imwrite(str(output/f'R{index:02d}_{name}.png'), image[y0:y1, x0:x1])
        crop_manifest.append({'case_id': case['id'], 'display_bbox_native': box,
            'crop_to_native': [[1, 0, x0], [0, 1, y0], [0, 0, 1]], 'purpose': 'DISPLAY_NOT_GT_OR_SPACE_BOUNDARY'})
        before, after = case['before'], case['after']
        def extent(result):
            return '미복원' if not result.get('bbox_native') else str([round(v, 2) for v in result['bbox_native']])
        text = case['label']['name']
        warning = '전체 공간 형상은 미검증입니다. 꺾인 경계·열린 연결·누락 구조를 더 조사해야 할 수 있습니다.'
        status = after['status'] + (' · 범위 추가 검토' if after.get('extent_assessment', '').startswith('UNRESOLVED') else '')
        rows.append('<tr><td><a href="#R'+f'{index:02d}'+'">R'+f'{index:02d}'+' '+html.escape(text)+'</a></td><td>'+html.escape(before['status'])+'</td><td>'+html.escape(status)+'</td><td>'+html.escape(extent(after))+'</td></tr>')
        details = {'before_native_bbox': before.get('bbox_native'), 'after_native_bbox': after.get('bbox_native'),
            'after_reason': after['reason'], 'supported_sides': after.get('supported_sides'),
            'inferred_perimeter_fraction': after.get('inferred_perimeter_fraction'),
            'unresolved_gaps': case.get('unresolved_boundary_interpretations'),
            'gap_assessments': case.get('gap_assessments', []),
            'extent_assessment': after.get('extent_assessment'),
            'reobservation_rejected_line_count': len(case.get('reobservation', {}).get('rejected_lines', [])),
            'full_space_extent_verified': False}
        pictures = ''.join('<figure><figcaption>'+label+'</figcaption><a href="'+f'R{index:02d}_{name}.png'+'"><img src="'+f'R{index:02d}_{name}.png'+'"></a></figure>' for name, label in [('source', '원본'), ('before', '초기 관찰 기반 후보'), ('after', '온톨로지 제약·국소 재관찰 후 후보')])
        local = case.get('reobservation_artifact')
        local_link = '<p><a href="'+html.escape(local)+'">실제로 다시 렌더링한 고해상도 문맥</a></p>' if local else ''
        cards.append((0 if text in ['침실1', '드레스룸', '팬트리'] else 1,
            '<section id="R'+f'{index:02d}'+'"><h2>R'+f'{index:02d}'+' '+html.escape(text)+'</h2><p>'+html.escape(after['status']+' · PROPOSED · '+warning)+'</p><div class="three">'+pictures+'</div>'+local_link+'<details><summary>원본 좌표와 재관찰 근거</summary><pre>'+html.escape(json.dumps(details, ensure_ascii=False, indent=2))+'</pre></details></section>'))
    _write_json(output/'display_crops.json', crop_manifest)
    summary = bundle['summary']
    _write_json(output/'review_summary.json', {'summary': summary, 'source_binding': bundle['source_binding'],
        'scope': bundle['analysis_scope'], 'candidate_count_is_accuracy': False,
        'geometric_restoration_is_architectural_verification': False, 'metrics_without_GT': None})
    page = '<!doctype html><html lang="ko"><meta charset="utf-8"><title>Reference 실제 공간 복원과 재관찰</title><style>body{font:16px/1.6 sans-serif;margin:24px;background:#f4f6f8;color:#202735}section{background:white;padding:18px;margin:20px 0}img{max-width:100%}figure{margin:0}.three{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px}.pair{display:grid;grid-template-columns:1fr 1fr;gap:12px}.notice{padding:18px;background:#fff1d5}pre{white-space:pre-wrap;overflow-wrap:anywhere}td,th{padding:8px;border-bottom:1px solid #ddd;text-align:left}table{width:100%;background:white}@media(max-width:800px){.three,.pair{grid-template-columns:1fr}}</style><h1>실제 Reference Page '+str(bundle['source_binding']['page'])+' 공간 후보·재관찰</h1><p class="notice">실제 사업승인 PDF와 좌표가 검증된 PP-OCRv5 관찰을 사용했습니다. 모든 공간·관계 의미는 PROPOSED입니다. Polygon 후보 수는 실제 정답 공간 수나 정확도가 아닙니다. 주황 점선은 추론 연결이며 물리 벽이 아닙니다. 도면 전체 복원이나 변경 탐지 완료 결과가 아닙니다.</p><p>자홍: 초기 후보 · 청록: 재관찰 후 조건 후보 · 주황: 미해결 후보와 추론 연결 · 빨간 점: OCR 기반 Seed. Crop은 표시 범위이며 Space Polygon이 아닙니다.</p><pre>'+html.escape(json.dumps(summary, ensure_ascii=False, indent=2))+'</pre><p><a href="region_graph_after.json">v0.4 가설 그래프 JSON</a> · <a href="ontology/after.ttl">RDF</a> · <a href="reference_restoration.json">전체 관찰·후보·실패 원문</a> · <a href="ontology/invalidation_plan.json">이전 결과 STALE 계획</a></p><table><thead><tr><th>공간</th><th>초기</th><th>재관찰 후</th><th>Native 후보 범위</th></tr></thead><tbody>'+''.join(rows)+'</tbody></table>'+''.join(card for _, card in sorted(cards, key=lambda pair: pair[0]))+'<section><h2>전체 원본과 재관찰 후 오버레이</h2><div class="pair"><a href="Ref_source.png"><img src="Ref_source.png"></a><a href="Ref_after_overlay.png"><img src="Ref_after_overlay.png"></a></div></section><section><h2>관계 후보와 판단 보류</h2><p>양쪽 공간과 통과 경로는 미검증입니다. 인접·연결 후보를 확정 사실로 표시하지 않습니다.</p><pre>'+html.escape(json.dumps(bundle.get('relation_attempts', []), ensure_ascii=False, indent=2))+'</pre></section></html>'
    (output/'review.html').write_text(page)


def run(pdf_path, page, zoom, ocr_run_path, ocr_json_path, output, ontology_python=None):
    output = Path(output)
    if output.exists():
        raise FileExistsError('Choose a new result directory; existing observations and results are preserved')
    pdf = Path(pdf_path).read_bytes()
    ocr_run = json.loads(Path(ocr_run_path).read_text())
    binding = copy.deepcopy(ocr_run['drawings']['Ref']['binding'])
    if binding['page'] != page or binding['extraction_zoom'] != zoom:
        raise ValueError('OCR page/extraction scale differs from requested Reference')
    binding['source_role'] = 'Ref'
    image = render_page(pdf, page, zoom)
    texts = json.loads(Path(ocr_json_path).read_text())
    result, assets = restore_reference(pdf, image.png, binding, texts)
    before, after = build_region_graphs(result)
    output.mkdir(parents=True, exist_ok=False)
    (output/'Ref_source.png').write_bytes(image.png)
    for index, case in enumerate(result['cases']):
        if case.get('request_id') in assets:
            name = f'reobserve_{index:03d}.png'
            (output/name).write_bytes(assets[case['request_id']])
            case['reobservation_artifact'] = name
    _write_json(output/'reference_restoration.json', result)
    _write_json(output/'region_graph_before.json', before)
    _write_json(output/'region_graph_after.json', after)
    _write_json(output/'reobservation_audit.json', result['reobservation_requests'])
    _write_json(output/'space_candidates.json', result['cases'])
    _write_json(output/'relation_candidates.json', result['relation_attempts'])
    interpreter = Path(ontology_python or ROOT/'integrations/fpue_ontology/.venv/bin/python')
    export = subprocess.run([str(interpreter), '-B', str(ROOT/'scripts/export_reference_ontology.py'),
        '--before', str((output/'region_graph_before.json').resolve()), '--after', str((output/'region_graph_after.json').resolve()),
        '--output-dir', str((output/'ontology').resolve())], cwd=ROOT, capture_output=True, text=True, timeout=120)
    (output/'ontology_export.log').write_text(export.stdout+export.stderr)
    if export.returncode:
        raise RuntimeError('Native result retained, ontology validation/export failed; see '+str(output/'ontology_export.log'))
    render_review(result, image.png, output)
    _write_json(output/'run_manifest.json', {'source_role': 'Ref', 'PDF_sha256': hashlib.sha256(pdf).hexdigest(),
        'OCR_run_sha256': hashlib.sha256(Path(ocr_run_path).read_bytes()).hexdigest(),
        'OCR_observations_sha256': hashlib.sha256(Path(ocr_json_path).read_bytes()).hexdigest(),
        'source_binding': binding, 'existing_OCR_models': ocr_run.get('models'),
        'new_OCR_or_VLM_inference': False, 'actual_PDF_vector_and_raster_reobservation': True,
        'new_GT_created': False, 'Target_or_GT_used_as_understanding_input': False,
        'general_UI_modified': False, 'offline_execution': True})
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pdf', required=True, type=Path)
    parser.add_argument('--page', required=True, type=int)
    parser.add_argument('--zoom', default=2.0, type=float)
    parser.add_argument('--ocr-run', required=True, type=Path)
    parser.add_argument('--ocr-json', required=True, type=Path)
    parser.add_argument('--output-dir', required=True, type=Path)
    args = parser.parse_args()
    result = run(args.pdf, args.page, args.zoom, args.ocr_run, args.ocr_json, args.output_dir)
    print(json.dumps({'output': str(args.output_dir), 'summary': result['summary'], 'runtime_seconds': result['runtime_seconds']}, ensure_ascii=False))


if __name__ == '__main__':
    main()
