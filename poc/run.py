"""Read-only, local first FPUE experiment. No alignment or change detection.

Run from repository root: poc/.venv/bin/python poc/run.py --help
Outputs are unique run directories; model proposals are never verified facts/GT.
"""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import hashlib
import html
import importlib.metadata
import io
import json
import math
import os
from pathlib import Path
import sys
import time
import traceback

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from phase1_pdf import inspect_pdf, render_page
from PIL import Image, ImageDraw
import numpy as np

MODELS = {
    "det": ("PaddlePaddle/PP-OCRv5_server_det", "ca867c897ecbca8873081573a802ad70d499cb94"),
    "rec": ("PaddlePaddle/korean_PP-OCRv5_mobile_rec", "c02ecaf1f22bfd1c618cce154fd19185b47e663a"),
    "qwen35": ("Qwen/Qwen3.5-9B", "c202236235762e1c871ad0ccb60c8ee5ba337b9a"),
    "qwen3vl": ("Qwen/Qwen3-VL-8B-Instruct", "0c351dd01ed87e9c1b53cbc748cba10e6187ff3b"),
}
PROMPT = """Inspect ONLY this architectural floor plan image. Do not infer a reference/target
comparison. Identify visible spaces, walls, doors, windows, text and symbols and
propose relationships supported by this image. Distinguish a text label/symbol
from the physical space/window it may refer to. Do not assume the closest object
is its referent. Do not invent unreadable text, room outlines, openings or codes.
Return ONLY a compact JSON object with keys:
entities: [{id, type, text: null or verbatim text, bbox_1000: [x1,y1,x2,y2], reason}],
relations: [{subject_id, predicate, object_id, reason}],
uncertainties: [string].
Use at most 8 entities and 6 relations; omit uncertain relationships and describe
uncertainty instead. Types may include Space, Wall, Opening, Symbol, TextRegion.
Door/Window may be proposed but are unverified. bbox_1000 uses the displayed
image extent normalized to 0..1000, top-left origin, x right, y down. Bounding
boxes locate candidates, NOT exact room/wall boundaries. Preserve Korean text.
All answers are proposals; no stability, identity, change or validation claims.
"""


def sha(data):
    return hashlib.sha256(data).hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")


def unique_dir(path):
    path = Path(path)
    path.mkdir(parents=True, exist_ok=False)
    return path


def versions():
    result = {"python": sys.version}
    for name in ["paddleocr", "paddlepaddle", "paddlex", "PyMuPDF", "numpy", "Pillow", "torch", "transformers", "bitsandbytes", "accelerate"]:
        try:
            result[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            pass
    return result


def verify_model_directory(path, key):
    binding = read(Path(path) / "poc_model_binding.json")
    if (binding.get("repo"), binding.get("revision")) != MODELS[key]:
        raise ValueError(f"Model binding mismatch: {key}")
    for filename, size in binding["files"].items():
        if (Path(path) / filename).stat().st_size != size:
            raise ValueError(f"Model artifact size mismatch: {filename}")


def validate_roi(roi, binding):
    if roi.get("schema_version") != 1 or roi.get("role") != "Target" or roi.get("coordinate_system") != "image_native_pixels":
        raise ValueError("Expected Phase 2A Target ROI in image_native_pixels")
    for key in ["source_sha256", "page", "extraction_zoom", "width", "height"]:
        if roi.get("image", {}).get(key) != binding[key]:
            raise ValueError(f"ROI binding mismatch: {key}")
    vertices = roi.get("vertices", [])
    if len(vertices) < 3:
        raise ValueError("ROI needs at least three vertices")
    for point in vertices:
        if len(point) != 2 or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in point):
            raise ValueError("Invalid ROI coordinate")
        if not (0 <= point[0] <= binding["width"] and 0 <= point[1] <= binding["height"]):
            raise ValueError("ROI outside native image")
    return roi


def prepare(args):
    out = unique_dir(args.out)
    sources = {"Ref": args.ref, "Target": args.target}
    manifest = {"schema_version": "fpue_poc_inputs/1", "created_utc": datetime.now(timezone.utc).isoformat(),
                "versions": versions(), "drawings": {}, "ground_truth": None,
                "metrics": None, "status": "INPUTS_PREPARED_NOT_UNDERSTOOD",
                "policy": "Independent observations; no automatic alignment/mapping/change detection; no GT metrics."}
    for role, source in sources.items():
        data = Path(source).read_bytes()
        info = inspect_pdf(data)
        image = render_page(data, args.page, args.zoom)
        native = Image.open(io.BytesIO(image.png)).convert("RGB")
        directory = out / role
        directory.mkdir()
        (directory / "page.png").write_bytes(image.png)
        binding = {"source_path": str(Path(source).resolve()), "source_sha256": info.sha256,
                   "page": args.page, "page_count": info.page_count, "extraction_zoom": args.zoom,
                   "width": image.width, "height": image.height, "image_sha256": sha(image.png),
                   "frame_id": f"{role}:{info.sha256}:{args.page}:{args.zoom}",
                   "coordinate_system": "image_native_pixels"}
        # Deterministic analysis tiles are NOT a user ROI or a Ref ROI mapping.
        w, h = native.size
        overlap = 96
        boxes = [(0, 0, w, h)] + [(x1, y1, x2, y2)
            for y1, y2 in [(0, min(h, h // 2 + overlap)), (max(0, h // 2 - overlap), h)]
            for x1, x2 in [(0, min(w, w // 2 + overlap)), (max(0, w // 2 - overlap), w)]]
        views = []
        for index, box in enumerate(boxes):
            name = "overview" if index == 0 else f"detail_{index}"
            crop = native.crop(box)
            crop.thumbnail((1280, 1280), Image.Resampling.LANCZOS)
            path = directory / f"{name}.png"
            crop.save(path)
            views.append({"id": name, "path": str(path.relative_to(out)), "sha256": sha(path.read_bytes()),
                          "native_bbox_xyxy": list(box), "input_width": crop.width, "input_height": crop.height,
                          "input_to_native": [[(box[2]-box[0])/crop.width, 0, box[0]],
                                              [0, (box[3]-box[1])/crop.height, box[1]], [0, 0, 1]],
                          "purpose": "analysis_context_not_user_roi"})
        manifest["drawings"][role] = {"binding": binding, "views": views}
        if role == "Target" and args.target_roi:
            roi = validate_roi(read(args.target_roi), binding)
            write(directory / "user_roi.json", roi)
            manifest["drawings"][role]["user_roi"] = {"path": "Target/user_roi.json", "sha256": sha(Path(args.target_roi).read_bytes()),
                "use": "preserved context only; no Ref mapping; full-page observations may be outside ROI"}
    write(out / "manifest.json", manifest)
    print(out / "manifest.json", flush=True)


def load_inputs(path):
    root = Path(path)
    manifest = read(root / "manifest.json")
    for role, drawing in manifest["drawings"].items():
        if sha((root / role / "page.png").read_bytes()) != drawing["binding"]["image_sha256"]:
            raise ValueError(f"Modified input raster: {role}")
        for view in drawing["views"]:
            if sha((root / view["path"]).read_bytes()) != view["sha256"]:
                raise ValueError(f"Modified input view: {role}/{view['id']}")
    return root, manifest


def result_json(result):
    value = result.json
    if isinstance(value, str):
        value = json.loads(value)
    return value.get("res", value)


def ocr(args):
    # Optional live adapter hooks. Legacy CLI remains fail-fast and unchanged.
    def hook(name, *values):
        callback=getattr(args,name,None)
        if callback:callback(*values)
    root, manifest = load_inputs(args.inputs)
    verify_model_directory(args.det_dir, "det")
    verify_model_directory(args.rec_dir, "rec")
    out = unique_dir(args.out)
    os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")
    from paddleocr import TextDetection, TextRecognition
    common = {"device": "cpu", "enable_mkldnn": False, "cpu_threads": 4}
    start = time.perf_counter()
    hook('on_stage','detection_model')
    det = TextDetection(model_name="PP-OCRv5_server_det", model_dir=args.det_dir, **common)
    hook('on_stage','recognition_model')
    rec = TextRecognition(model_name="korean_PP-OCRv5_mobile_rec", model_dir=args.rec_dir, **common)
    run = {"schema_version": "fpue_poc_ocr/1", "models": {k: MODELS[k] for k in ["det", "rec"]},
           "versions": versions(), "device": "cpu", "setup_seconds": time.perf_counter()-start,
           "detection": {"limit_side_len": 2400, "limit_type": "max", "thresh": .3, "box_thresh": .6, "unclip_ratio": 1.5},
           "recognition_crop_policy": "detected quadrilateral -> perspective text crop, NOT verified GT crop",
           "score_semantics": "uncalibrated model scores, not correctness probabilities", "drawings": {}, "metrics": None}
    import cv2
    for role, drawing in manifest["drawings"].items():
        directory = out / role
        directory.mkdir()
        image = Image.open(root / role / "page.png").convert("RGB")
        bgr = np.array(image)[:, :, ::-1].copy()
        tick = time.perf_counter()
        hook('on_stage','detection')
        raw = result_json(next(iter(det.predict(bgr, **run["detection"]))))
        write(directory / "detection_raw.json", raw)
        hook('on_detection',raw)
        detection_seconds = time.perf_counter()-tick
        entries = []
        annotated = image.copy()
        draw = ImageDraw.Draw(annotated)
        for index, polygon in enumerate(raw.get("dt_polys", [])):
            hook('on_stage','recognition',index)
            points = np.asarray(polygon, dtype=np.float32).reshape(4, 2)
            width = max(2, int(round(max(np.linalg.norm(points[0]-points[1]), np.linalg.norm(points[2]-points[3])))))
            height = max(2, int(round(max(np.linalg.norm(points[0]-points[3]), np.linalg.norm(points[1]-points[2])))))
            transform = cv2.getPerspectiveTransform(points, np.array([[0,0],[width-1,0],[width-1,height-1],[0,height-1]], dtype=np.float32))
            crop = cv2.warpPerspective(bgr, transform, (width, height), borderMode=cv2.BORDER_REPLICATE)
            rotated = height > width * 1.5
            if rotated:
                crop = cv2.rotate(crop, cv2.ROTATE_90_COUNTERCLOCKWISE)
            crop_path = directory / "crops" / f"text_{index:04d}.png"
            crop_path.parent.mkdir(exist_ok=True)
            Image.fromarray(crop[:, :, ::-1]).save(crop_path)
            tick = time.perf_counter()
            try:
                recognition = result_json(next(iter(rec.predict(crop))))
            except Exception as error:
                hook('on_recognition_error',index,type(error).__name__)
                if getattr(args,'continue_on_recognition_failure',False):continue
                raise
            entry = {"id": f"{role}:text:{index:04d}", "frame_id": drawing["binding"]["frame_id"],
                     "polygon_native": points.tolist(), "native_to_rectified_crop": transform.tolist(),
                     "crop_rotated_ccw_90": rotated, "crop": str(crop_path.relative_to(out)),
                     "text": recognition.get("rec_text", ""), "recognition_score": float(recognition.get("rec_score", 0)),
                     "recognition_seconds": time.perf_counter()-tick,
                     "raw_recognition": recognition, "review_status": "UNREVIEWED", "ground_truth": None}
            entries.append(entry)
            # Include exact rectified/rotation transforms without changing old records.
            if getattr(args,'on_recognition',None):
                rotation=np.array([[0,1,0],[-1,0,width-1],[0,0,1]],float) if rotated else np.eye(3)
                try:inverse=np.linalg.inv(rotation@transform).tolist()
                except np.linalg.LinAlgError:inverse=None
                hook('on_recognition',entry,{'crop_transform_status':'INVERTIBLE' if inverse is not None else 'NON_INVERTIBLE',
                    'recognition_crop_sha256':hashlib.sha256(crop.tobytes()).hexdigest(),
                    'crop_hash_encoding':'raw BGR uint8 bytes','recognition_crop_size':[crop.shape[1],crop.shape[0]],
                    'native_to_recognition_crop':(rotation@transform).tolist(),
                    'recognition_crop_to_native':inverse})
            draw.line([tuple(p) for p in points.tolist()] + [tuple(points[0])], fill="red", width=2)
            draw.text(tuple(points[0]), str(index), fill="blue")
        annotated.save(directory / "detection_overlay.png")
        write(directory / "observations.json", entries)
        run["drawings"][role] = {"binding": drawing["binding"], "detected_text_regions": len(entries),
                                 "detection_seconds": detection_seconds,
                                 "recognition_seconds": sum(e["recognition_seconds"] for e in entries),
                                 "symbol_detection": "NOT_IMPLEMENTED", "grounding": "NOT_RUN"}
        with (directory / "recognition.csv").open("w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=["id", "text", "recognition_score", "polygon_native", "crop", "review_status"])
            writer.writeheader()
            for e in entries:
                writer.writerow({k: json.dumps(e[k], ensure_ascii=False) if k == "polygon_native" else e[k] for k in writer.fieldnames})
        print(role, len(entries), "text regions", flush=True)
    write(out / "run.json", run)


def map_box(box, view):
    if not isinstance(box, list) or len(box) != 4 or any(isinstance(v, bool) or not isinstance(v, (int,float)) or not math.isfinite(v) for v in box):
        raise ValueError("Invalid bbox_1000")
    x1,y1,x2,y2 = box
    if not (0 <= x1 < x2 <= 1000 and 0 <= y1 < y2 <= 1000):
        raise ValueError("Unordered or out-of-frame bbox_1000; not silently clipped")
    a,b,c,d = view["native_bbox_xyxy"]
    return [a+x1*(c-a)/1000, b+y1*(d-b)/1000, a+x2*(c-a)/1000, b+y2*(d-b)/1000]


def parse_proposal(text, view):
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    data = json.loads(cleaned)
    if not isinstance(data, dict) or any(not isinstance(data.get(k), list) for k in ["entities","relations","uncertainties"]):
        raise ValueError("Expected entities/relations/uncertainties lists")
    mapped, issues, ids = [], [], set()
    for entity in data["entities"]:
        try:
            identity = entity["id"]
            if not isinstance(identity, str) or identity in ids:
                raise ValueError("Missing/duplicate entity id")
            bbox = map_box(entity["bbox_1000"], view)
            ids.add(identity)
            mapped.append({**entity, "bbox_native_xyxy": bbox, "verification_status": "PROPOSED", "geometry_status": "VLM_ESTIMATE_NOT_MEASURED"})
        except (KeyError, TypeError, ValueError) as e:
            issues.append({"kind": "invalid_entity", "raw": entity, "reason": str(e)})
    relations = []
    for relation in data["relations"]:
        if isinstance(relation, dict) and relation.get("subject_id") in ids and relation.get("object_id") in ids:
            relations.append({**relation, "verification_status": "PROPOSED"})
        else:
            issues.append({"kind": "dangling_relation", "raw": relation})
    return {"entities": mapped, "relations": relations, "uncertainties": data["uncertainties"], "issues": issues}


def vlm(args):
    root, manifest = load_inputs(args.inputs)
    verify_model_directory(args.model_dir, args.model)
    out = unique_dir(args.out)
    import torch
    from transformers import AutoProcessor, AutoModelForImageTextToText, BitsAndBytesConfig
    if not torch.cuda.is_available():
        write(out / "run.json", {"status": "FAILED", "reason": "CUDA unavailable; no CPU/alternate model success fallback"})
        raise RuntimeError("CUDA unavailable")
    model_id, revision = MODELS[args.model]
    settings = {"schema_version": "fpue_poc_vlm/1", "model": model_id, "revision": revision, "versions": versions(),
                "prompt_sha256": sha(PROMPT.encode()), "quantization": "bitsandbytes NF4 double quant, BF16 compute",
                "max_new_tokens": args.max_new_tokens, "do_sample": False,
                "enable_thinking": False if args.model == "qwen35" else "not_applicable_instruct_model",
                "image_min_pixels": 65536, "image_max_pixels": 1048576, "attention": "sdpa",
                "gpu": torch.cuda.get_device_name(0), "status": "RUNNING", "attempts": [], "metrics": None,
                "evaluation": "No GT; all objects/relations are UNREVIEWED PROPOSED; no geometry validation or stable structures"}
    write(out / "run.json", settings)
    (out / "prompt.txt").write_text(PROMPT, encoding="utf-8")
    tick = time.perf_counter()
    processor = AutoProcessor.from_pretrained(args.model_dir, trust_remote_code=False, min_pixels=65536, max_pixels=1048576)
    model = AutoModelForImageTextToText.from_pretrained(args.model_dir, trust_remote_code=False,
        device_map={"": 0}, dtype=torch.bfloat16, attn_implementation="sdpa",
        quantization_config=BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type="nf4", bnb_4bit_use_double_quant=True, bnb_4bit_compute_dtype=torch.bfloat16))
    model.eval()
    settings["load_seconds"] = time.perf_counter()-tick
    for role, drawing in manifest["drawings"].items():
        for view in drawing["views"]:
            if view["id"] not in args.views:
                continue
            directory = out / role / view["id"]
            directory.mkdir(parents=True)
            attempt = {"role": role, "view": view, "binding": drawing["binding"], "status": "RUNNING",
                       "input_context": "independent single drawing image; no OCR, Ref/Target peer, or GT supplied"}
            try:
                image = Image.open(root / view["path"]).convert("RGB")
                messages = [{"role": "user", "content": [{"type": "image", "image": image}, {"type": "text", "text": PROMPT}]}]
                template_kwargs = {"enable_thinking": False} if args.model == "qwen35" else {}
                batch = processor.apply_chat_template(messages, tokenize=True, add_generation_prompt=True,
                    return_dict=True, return_tensors="pt", **template_kwargs).to("cuda")
                torch.cuda.reset_peak_memory_stats()
                torch.cuda.synchronize()
                tick = time.perf_counter()
                with torch.inference_mode():
                    output = model.generate(**batch, max_new_tokens=args.max_new_tokens, do_sample=False)
                torch.cuda.synchronize()
                attempt["seconds"] = time.perf_counter()-tick
                attempt["peak_vram_allocated_bytes"] = torch.cuda.max_memory_allocated()
                attempt["peak_vram_reserved_bytes"] = torch.cuda.max_memory_reserved()
                attempt["input_tokens"] = batch["input_ids"].shape[1]
                tokens = output[0, batch["input_ids"].shape[1]:]
                attempt["output_tokens"] = len(tokens)
                attempt["generation_limit_reached"] = len(tokens) >= args.max_new_tokens
                text = processor.decode(tokens, skip_special_tokens=True)
                (directory / "raw.txt").write_text(text, encoding="utf-8")
                proposal = parse_proposal(text, view)
                write(directory / "proposals.json", proposal)
                overlay = Image.open(root / role / "page.png").convert("RGB")
                draw = ImageDraw.Draw(overlay)
                for index, entity in enumerate(proposal["entities"]):
                    draw.rectangle(entity["bbox_native_xyxy"], outline="orange", width=3)
                    draw.text(tuple(entity["bbox_native_xyxy"][:2]), f"VLM proposal {index}", fill="red")
                overlay.save(directory / "proposal_overlay.png")
                attempt["status"] = "PROPOSALS_UNREVIEWED" if not attempt["generation_limit_reached"] else "TRUNCATED_UNREVIEWED"
                attempt["entity_count"] = len(proposal["entities"])
                attempt["relation_count"] = len(proposal["relations"])
                del output, batch
            except Exception as e:
                attempt.update(status="FAILED", error=str(e), traceback=traceback.format_exc())
            write(directory / "attempt.json", attempt)
            settings["attempts"].append(attempt)
            write(out / "run.json", settings)
            print(role, view["id"], attempt["status"], attempt.get("seconds"), flush=True)
    settings["status"] = "PROPOSALS_UNREVIEWED" if settings["attempts"] and all(a["status"] == "PROPOSALS_UNREVIEWED" for a in settings["attempts"]) else "PARTIAL_OR_FAILED"
    write(out / "run.json", settings)


def review(args):
    root, manifest = load_inputs(args.inputs)
    escape = lambda x: html.escape(str(x))
    parts = ['<!doctype html><meta charset="utf-8"><title>FPUE PoC — unreviewed observations</title>',
             '<style>body{font:16px sans-serif;margin:24px;background:#eef1f5}section{background:white;padding:16px;margin:16px 0}img.page{width:100%;max-width:1400px}table{border-collapse:collapse;width:100%}td,th{border:1px solid #bbb;padding:6px}td img{max-width:250px;max-height:90px}pre{white-space:pre-wrap;overflow-wrap:anywhere}.warning{background:#fff0bd;padding:16px}</style>',
             '<h1>도면 이해 첫 PoC</h1><p class="warning">연구용 미검토 관찰입니다. 문자 검출은 건축 기호 검출이 아닙니다. VLM 좌표·관계는 가설이며 안정 구조·정렬·변경·정답으로 확정하지 않았습니다. GT 및 정확도 점수 없음. 브라우저 확대 또는 이미지 새 탭 열기로 원본을 확인하세요.</p>']
    destination = Path(args.out)
    def link(path):
        return escape(os.path.relpath(Path(path), destination.parent))
    for role, drawing in manifest["drawings"].items():
        parts += [f'<section><h2>{role} Page {drawing["binding"]["page"]}</h2><p>{escape(drawing["binding"])}</p>',
                  f'<a href="{link(root / role / "page.png")}"><img class="page" src="{link(root / role / "page.png")}"></a>']
        if args.ocr:
            directory = Path(args.ocr) / role
            entries = read(directory / "observations.json")
            parts += [f'<h3>OCR 문자 검출 — {len(entries)}개 후보</h3><a href="{link(directory / "detection_overlay.png")}"><img class="page" src="{link(directory / "detection_overlay.png")}"></a>',
                      '<table><tr><th>ID</th><th>원본에서 잘라낸 검출 영역</th><th>인식 원문</th><th>모델 점수 (정답 확률 아님)</th></tr>']
            for e in entries:
                parts += [f'<tr><td>{escape(e["id"])}</td><td><img loading="lazy" src="{link(Path(args.ocr) / e["crop"])}"></td><td>{escape(e["text"])}</td><td>{e["recognition_score"]:.3f}</td></tr>']
            parts += ['</table>']
        for experiment in args.vlm or []:
            directory = Path(experiment)
            run = read(directory / "run.json")
            parts += [f'<h3>{escape(run.get("model", directory.name))}</h3><p>실행 상태: {escape(run["status"])}</p>']
            for attempt in run.get("attempts", []):
                if attempt["role"] != role:
                    continue
                path = directory / role / attempt["view"]["id"]
                parts += [f'<details><summary>{escape(attempt["view"]["id"])} — {escape(attempt["status"])}</summary>',
                          f'<img class="page" src="{link(root / attempt["view"]["path"])}"><pre>{escape((path / "raw.txt").read_text() if (path / "raw.txt").exists() else attempt.get("error"))}</pre></details>']
                if (path / "proposal_overlay.png").exists():
                    parts += [f'<p>주황색: 미검토 VLM 위치 추정. 실제 경계·변경 표시가 아닙니다.</p><a href="{link(path / "proposal_overlay.png")}"><img class="page" src="{link(path / "proposal_overlay.png")}"></a>']
        parts += ['</section>']
    destination.write_text('\n'.join(parts), encoding="utf-8")
    print(destination)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    p = commands.add_parser("prepare")
    p.add_argument("--ref", default=str(ROOT / "source_pdfs/자이사업승인도서.pdf"))
    p.add_argument("--target", default=str(ROOT / "source_pdfs/자이실시도서.pdf"))
    p.add_argument("--page", type=int, default=4)
    p.add_argument("--zoom", type=float, default=2.)
    p.add_argument("--target-roi")
    p.add_argument("--out", required=True)
    p.set_defaults(func=prepare)
    p = commands.add_parser("ocr")
    p.add_argument("--inputs", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--det-dir", required=True)
    p.add_argument("--rec-dir", required=True)
    p.set_defaults(func=ocr)
    p = commands.add_parser("vlm")
    p.add_argument("--inputs", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--model", choices=["qwen35", "qwen3vl"], required=True)
    p.add_argument("--model-dir", required=True)
    p.add_argument("--views", nargs="+", choices=["overview", "detail_1", "detail_2", "detail_3", "detail_4"], default=["overview", "detail_1"])
    p.add_argument("--max-new-tokens", type=int, default=1600)
    p.set_defaults(func=vlm)
    p = commands.add_parser("review")
    p.add_argument("--inputs", required=True)
    p.add_argument("--ocr")
    p.add_argument("--vlm", nargs="*")
    p.add_argument("--out", required=True)
    p.set_defaults(func=review)
    args = parser.parse_args()
    try:
        args.func(args)
    except Exception as exc:
        if args.command == "vlm" and (Path(args.out) / "run.json").exists():
            result = read(Path(args.out) / "run.json")
            result.update(status="FAILED", error=str(exc), traceback=traceback.format_exc())
            write(Path(args.out) / "run.json", result)
        raise


if __name__ == "__main__":
    main()
