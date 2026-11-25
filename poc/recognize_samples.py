"""Recognition-only diagnostic on explicitly selected text crops, not a GT score."""
import argparse
import json
from pathlib import Path
import time
import os
import html

os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")
from run import load_inputs, read, write, unique_dir, verify_model_directory, versions
from PIL import Image
import numpy as np


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs", required=True)
    parser.add_argument("--samples", required=True)
    parser.add_argument("--rec-dir", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    root, manifest = load_inputs(args.inputs)
    samples = read(args.samples)
    verify_model_directory(args.rec_dir, "rec")
    # Validate every sample against native source before running the model.
    for sample in samples["samples"]:
        binding = manifest["drawings"][sample["role"]]["binding"]
        if sample["source_binding"] != {k: binding[k] for k in ["source_sha256", "page", "extraction_zoom", "width", "height"]}:
            raise ValueError("Text sample source binding mismatch")
        x1,y1,x2,y2 = sample["bbox_native_xyxy"]
        if not all(type(v) is int for v in [x1,y1,x2,y2]) or not (0 <= x1 < x2 <= binding["width"] and 0 <= y1 < y2 <= binding["height"]):
            raise ValueError("Invalid native text sample bbox")
    out = unique_dir(args.out)
    from paddleocr import TextRecognition
    model = TextRecognition(model_name="korean_PP-OCRv5_mobile_rec", model_dir=args.rec_dir,
                            device="cpu", enable_mkldnn=False, cpu_threads=4)
    observations = []
    for sample in samples["samples"]:
        crop = Image.open(root / sample["role"] / "page.png").convert("RGB").crop(sample["bbox_native_xyxy"])
        crop.save(out / f'{sample["id"]}.png')
        tick = time.perf_counter()
        raw = next(iter(model.predict(np.array(crop)[:, :, ::-1].copy()))).json
        if isinstance(raw, str): raw = json.loads(raw)
        raw = raw.get("res", raw)
        observations.append({"sample": sample, "raw_recognition": raw, "seconds": time.perf_counter()-tick,
                             "review_status": "UNREVIEWED", "ground_truth": None})
        print(sample["id"], raw.get("rec_text"), raw.get("rec_score"), flush=True)
    write(out / "run.json", {"schema_version":"fpue_poc_recognition_only/1", "versions":versions(),
        "selection": "Researcher selected visible text crop; no detector used in this stage; not user ROI, GT, or symbol interpretation",
        "metrics":None, "observations":observations})
    render_review(out, observations)


def render_review(out, observations):
    escape = lambda x: html.escape(str(x))
    content = ['<!doctype html><meta charset="utf-8"><title>Recognition-only diagnostics</title>',
               '<style>body{font:16px sans-serif;margin:24px}table{border-collapse:collapse}td,th{border:1px solid #aaa;padding:12px}img{height:64px;image-rendering:auto}</style>',
               '<h1>문자 인식만 분리한 진단</h1><p>오류를 확인한 뒤 에이전트가 눈으로 선택한 연구용 문자 Crop입니다. 사용자 ROI·GT·독립 평가가 아닙니다. 문자 인식은 기호 해석이나 실제 창문 연결을 의미하지 않습니다. 정확도 점수 없음.</p>',
               '<table><tr><th>ID</th><th>선택한 문자 Crop</th><th>출력 원문</th><th>모델 점수 (정답 확률 아님)</th></tr>']
    for entry in observations:
        name = entry['sample']['id']
        raw = entry['raw_recognition']
        content.append(f'<tr><td>{escape(name)}</td><td><img src="{escape(name)}.png"></td><td>{escape(raw.get("rec_text"))}</td><td>{escape(raw.get("rec_score"))}</td></tr>')
    content.append('</table>')
    (out / 'review.html').write_text('\n'.join(content), encoding='utf-8')


if __name__ == "__main__":
    main()
