"""Local OCR-VL experiment on preserved Page 4 crops; no GT or structural claims."""
import argparse
from datetime import datetime, timezone
import hashlib
import html
import json
import os
from pathlib import Path
import re
import subprocess
import time
import traceback

os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
from PIL import Image, ImageDraw
import numpy as np
from run import load_inputs, read, write, unique_dir, versions
from download_paddleocr_vl16 import REPO, REVISION, SIZE, SHA256, digest


def extent(points):
    return [min(p[0] for p in points), min(p[1] for p in points), max(p[0] for p in points), max(p[1] for p in points)]


def iou(a, b):
    intersection = max(0, min(a[2],b[2])-max(a[0],b[0])) * max(0, min(a[3],b[3])-max(a[1],b[1]))
    union = (a[2]-a[0])*(a[3]-a[1])+(b[2]-b[0])*(b[3]-b[1])-intersection
    return intersection/union if union else 0


def gpu_snapshot():
    try:
        data = subprocess.check_output(["nvidia-smi", "--query-gpu=timestamp,name,memory.used,utilization.gpu", "--format=csv,noheader"], text=True, timeout=5).strip()
        return {"scope":"whole GPU, includes other users/processes/display", "raw":data}
    except Exception as exc:
        return {"unavailable":str(exc)}


def json_ready(value):
    if isinstance(value, dict):
        return {str(key):json_ready(item) for key,item in value.items()}
    if isinstance(value,(set,frozenset)):
        return sorted((json_ready(item) for item in value),key=str)
    if isinstance(value,(list,tuple)):
        return [json_ready(item) for item in value]
    return value


def spotting(text, bbox):
    """Decode Paddle's LOC_0..1000 format, retaining failures as issues.

    Format follows installed Apache-2.0 PaddleX paddleocr_vl/uilts.py.
    Location candidates remain unreviewed estimates of TEXT, not buildings.
    """
    texts = re.findall(r"<\|TEXT_START\|>(.*?)<\|TEXT_END\|>", text, re.S)
    blocks = re.findall(r"<\|LOC_BEGIN\|>(.*?)<\|LOC_END\|>", text, re.S)
    candidates, issues = [], []
    if texts and blocks:
        groups = [(value.strip(), re.findall(r"<\|LOC_(\d+)\|>", block)) for value,block in zip(texts,blocks)]
        if len(texts) != len(blocks):
            issues.append("Unequal text and location block counts")
    else:
        tokens = list(re.finditer(r"<\|LOC_(\d+)\|>", text))
        groups = []
        last = 0
        for start in range(0,len(tokens)-7,8):
            chunk = tokens[start:start+8]
            groups.append((text[last:chunk[0].start()].strip(), [token.group(1) for token in chunk]))
            last = chunk[-1].end()
        if len(tokens)%8:
            issues.append("Incomplete location token group")
    x1,y1,x2,y2 = bbox
    for value,values in groups:
        coords = list(map(int,values[:8]))
        if len(coords) != 8 or not all(0 <= v <= 1000 for v in coords):
            issues.append({"invalid_location":values,"text":value})
            continue
        polygon = [[x1+coords[i]*(x2-x1)/1000,y1+coords[i+1]*(y2-y1)/1000] for i in range(0,8,2)]
        box = extent(polygon)
        if box[0] >= box[2] or box[1] >= box[3]:
            issues.append({"zero_area_location":values,"text":value})
            continue
        candidates.append({"text":value,"polygon_native":polygon,"verification_status":"PROPOSED", "scope":"text_location_not_physical_structure"})
    if not candidates:
        issues.append("NO_PARSEABLE_TEXT_LOCATIONS; not proof of absence")
    return {"candidates":candidates,"issues":issues}


def tasks_from_existing(root, manifest, baseline, manual):
    tasks = []
    observations = {role: read(baseline / role / "observations.json") for role in manifest["drawings"]}
    for observation in read(manual / "run.json")["observations"]:
        sample = observation["sample"]
        role = sample["role"]
        binding = manifest["drawings"][role]["binding"]
        expected = {k:binding[k] for k in ["source_sha256","page","extraction_zoom","width","height"]}
        if sample["source_binding"] != expected:
            raise ValueError("Manual crop source binding mismatch")
        box = sample["bbox_native_xyxy"]
        tasks.append({"id":"manual_"+sample["id"],"role":role,"kind":"manual_text_crop", "task":"ocr",
                      "image":str(manual / (sample["id"]+".png")),"native_bbox_xyxy":box,
                      "baseline_text":observation["raw_recognition"].get("rec_text"),
                      "baseline_score":observation["raw_recognition"].get("rec_score"),
                      "selection":"Existing researcher-selected development crop, NOT GT"})
        match = max(observations[role], key=lambda e: iou(box,extent(e["polygon_native"])))
        overlap = iou(box,extent(match["polygon_native"]))
        if overlap >= .25:
            tasks.append({"id":"detected_"+sample["id"],"role":role,"kind":"detected_text_crop","task":"ocr",
                          "image":str(baseline / match["crop"]),"native_bbox_xyxy":extent(match["polygon_native"]),
                          "native_polygon":match["polygon_native"], "baseline_id":match["id"],
                          "native_to_rectified_crop":match["native_to_rectified_crop"],
                          "crop_rotated_ccw_90":match["crop_rotated_ccw_90"],
                          "baseline_text":match["text"],"baseline_score":match["recognition_score"],
                          "selection":"Same preserved detector crop bytes as PP-OCRv5; not a detector comparison"})
    for role in ["Ref","Target"]:
        # Independently inspected source context crops. Neither is a user ROI or mapping.
        for name,box in [("symbol_context",[1178,428,1232,483]),("room_context",[935,570,1710,1150])]:
            tasks.append({"id":role.lower()+"_"+name,"role":role,"kind":name,"task":"spotting",
                          "image":str(root / role / "page.png"),"native_bbox_xyxy":box,"crop_from_page":True,
                          "baseline_text":None,"selection":"Research context only, NOT user ROI, room polygon, GT or Ref/Target mapping"})
    return tasks


def render_review(out, run):
    escape = lambda value: html.escape(str(value))
    parts = ['<!doctype html><meta charset="utf-8"><title>PaddleOCR-VL-1.6 comparison</title>',
             '<style>body{font:16px sans-serif;margin:24px;background:#f0f3f6}section{background:white;padding:16px;margin:16px 0}table{border-collapse:collapse;width:100%}td,th{border:1px solid #ccc;padding:8px}img.word{height:60px;max-width:400px}img.context{max-width:100%}pre{white-space:pre-wrap;overflow-wrap:anywhere}p.notice{background:#fff0b0;padding:16px}</style>',
             '<h1>PaddleOCR-VL-1.6 실제 Page 4 비교</h1><p class="notice">미검토 모델 출력입니다. GT·정확도 점수 없음. 문자 위치는 실제 공간/창문/벽의 위치가 아닙니다. 기존 PP-OCRv5 CPU 결과와 OCR-VL GPU 결과의 처리 시간은 동일 조건 속도 비교가 아닙니다.</p>',
             f'<p>상태: {escape(run["status"])} · 모델: {escape(run["model"])} · dtype: BF16</p>',
             '<h2>같은 문자 Crop의 판독 비교</h2><table><tr><th>입력</th><th>이미지</th><th>PP-OCRv5 원문</th><th>OCR-VL 원문</th><th>생성 시간 / PyTorch 최대 allocated</th></tr>']
    for attempt in run.get("attempts",[]):
        task = attempt["input"]
        if task["task"] != "ocr": continue
        parts.append(f'<tr><td>{escape(task["id"])}<br>{escape(task["native_bbox_xyxy"])}</td><td><img class="word" src="{escape(task["id"])}/input.png"></td><td><pre>{escape(task.get("baseline_text"))}</pre></td><td><pre>{escape(attempt.get("text",attempt.get("error")))}</pre></td><td>{attempt.get("generation_seconds",0):.3f}s / {attempt.get("peak_allocated_bytes",0)/1024**3:.2f} GiB</td></tr>')
    parts.append('</table>')
    for attempt in run.get("attempts",[]):
        task = attempt["input"]
        if task["task"] != "spotting": continue
        parts += [f'<section><h2>{escape(task["id"])} — {escape(attempt["status"])}</h2><p>원본 픽셀 분석 범위: {escape(task["native_bbox_xyxy"])}</p>',
                  f'<img class="context" src="{escape(task["id"])}/input.png">']
        if (out / task["id"] / "text_location_overlay.png").exists():
            parts += [f'<p>주황색은 미검토 문자 위치 추정입니다.</p><img class="context" src="{escape(task["id"])}/text_location_overlay.png">']
        parts += [f'<details><summary>원문과 미검토 원본 좌표</summary><pre>{escape(attempt.get("raw",attempt.get("error")))}</pre><pre>{escape(json.dumps(attempt.get("spotting"),ensure_ascii=False,indent=2))}</pre></details></section>']
    (out / "review.html").write_text('\n'.join(parts),encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs",type=Path,required=True)
    parser.add_argument("--baseline",type=Path,required=True)
    parser.add_argument("--manual",type=Path,required=True)
    parser.add_argument("--model-dir",type=Path,required=True)
    parser.add_argument("--out",type=Path,required=True)
    args = parser.parse_args()
    root,manifest = load_inputs(args.inputs)
    weight = args.model_dir / "model.safetensors"
    if weight.stat().st_size != SIZE or digest(weight) != SHA256:
        raise RuntimeError("Official weight SHA-256 mismatch")
    out = unique_dir(args.out)
    tasks = tasks_from_existing(root,manifest,args.baseline,args.manual)
    import torch
    from transformers import AutoProcessor,AutoModelForImageTextToText
    if not torch.cuda.is_available(): raise RuntimeError("CUDA unavailable; no silent CPU fallback")
    run = {"schema_version":"paddleocr_vl16_comparison/1", "created_utc":datetime.now(timezone.utc).isoformat(),
           "model":REPO,"revision":REVISION,"weight_sha256":SHA256,"versions":versions(),
           "code_sha256":hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
           "device":torch.cuda.get_device_name(0),"dtype":"bfloat16","quantization":None,
           "implementation":"Transformers native, trust_remote_code=False; element recognition/text spotting, NOT full document layout pipeline",
           "status":"MODEL_LOADING","attempts":[],"gpu_snapshots":[gpu_snapshot()],"metrics":None,
           "scope":"Development diagnostics on selected Page 4 crops; no structural interpretation, GT or relation validation"}
    write(out / "run.json",run)
    try:
        tick=time.perf_counter()
        model,loading = AutoModelForImageTextToText.from_pretrained(args.model_dir,local_files_only=True,
            trust_remote_code=False,dtype=torch.bfloat16,attn_implementation="sdpa",output_loading_info=True)
        loading=json_ready(loading)
        write(out / "loading_info.json",loading)
        if loading.get("missing_keys") or loading.get("mismatched_keys") or loading.get("error_msgs"):
            raise RuntimeError("Incomplete model weight load; refusing random initialized parameters")
        model=model.to("cuda").eval()
        processor=AutoProcessor.from_pretrained(args.model_dir,local_files_only=True,trust_remote_code=False)
        minimum_pixels=processor.image_processor.to_dict()["size"]["shortest_edge"]
        torch.cuda.synchronize()
        run["load_seconds"]=time.perf_counter()-tick
        run["gpu_snapshots"].append(gpu_snapshot())
        run["status"]="RUNNING_UNREVIEWED"
        write(out / "run.json",run)
        for task in tasks:
            attempt={"input":task,"binding":manifest["drawings"][task["role"]]["binding"],"status":"RUNNING","verification_status":"PROPOSED","ground_truth":None}
            directory=out / task["id"];directory.mkdir()
            try:
                source=Path(task["image"])
                image=Image.open(source).convert("RGB")
                if task.get("crop_from_page"): image=image.crop(task["native_bbox_xyxy"])
                image.save(directory / "input.png")
                attempt["source_image_sha256"]=hashlib.sha256(source.read_bytes()).hexdigest()
                processed=image
                if task["task"]=="spotting" and image.width<1500 and image.height<1500:
                    processed=image.resize((image.width*2,image.height*2),Image.Resampling.LANCZOS)
                x1,y1,x2,y2=task["native_bbox_xyxy"]
                attempt["processed_size"]=list(processed.size)
                if task["kind"] == "detected_text_crop":
                    native_to_input=np.array(task["native_to_rectified_crop"])
                    if task["crop_rotated_ccw_90"]:
                        native_to_input=np.array([[0,1,0],[-1,0,image.height-1],[0,0,1]]) @ native_to_input
                    input_to_native=np.linalg.inv(native_to_input)
                    attempt["processed_to_native"]=(input_to_native @ np.diag([image.width/processed.width,image.height/processed.height,1])).tolist()
                else:
                    attempt["processed_to_native"]=[[(x2-x1)/processed.width,0,x1],[0,(y2-y1)/processed.height,y1],[0,0,1]]
                limit=2048*28*28 if task["task"]=="spotting" else 1280*28*28
                attempt["max_pixels"]=limit
                prompt="Spotting:" if task["task"]=="spotting" else "OCR:"
                attempt["prompt"]=prompt
                batch=processor.apply_chat_template([{"role":"user","content":[{"type":"image","image":processed},{"type":"text","text":prompt}]}],
                    tokenize=True,add_generation_prompt=True,return_dict=True,return_tensors="pt",
                    images_kwargs={"size":{"shortest_edge":minimum_pixels,"longest_edge":limit}}).to("cuda")
                maximum=4096 if task["kind"]=="room_context" else (512 if task["task"]=="spotting" else 128)
                torch.cuda.reset_peak_memory_stats();torch.cuda.synchronize();tick=time.perf_counter()
                with torch.inference_mode():
                    output=model.generate(**batch,max_new_tokens=maximum,do_sample=False,use_cache=True)
                torch.cuda.synchronize()
                attempt["generation_seconds"]=time.perf_counter()-tick
                tokens=output[0,batch["input_ids"].shape[1]:]
                attempt.update(input_tokens=int(batch["input_ids"].shape[1]),output_tokens=len(tokens),max_new_tokens=maximum,
                    generation_limit_reached=len(tokens)>=maximum,peak_allocated_bytes=torch.cuda.max_memory_allocated(),peak_reserved_bytes=torch.cuda.max_memory_reserved())
                attempt["raw"]=processor.decode(tokens,skip_special_tokens=False)
                attempt["text"]=processor.decode(tokens,skip_special_tokens=True)
                (directory / "raw.txt").write_text(attempt["raw"],encoding="utf-8")
                if task["task"]=="spotting":
                    attempt["spotting"]=spotting(attempt["raw"],task["native_bbox_xyxy"])
                    overlay=image.copy();draw=ImageDraw.Draw(overlay)
                    for index,item in enumerate(attempt["spotting"]["candidates"]):
                        points=[(x-x1,y-y1) for x,y in item["polygon_native"]]
                        draw.line(points+[points[0]],fill="orange",width=2);draw.text(points[0],str(index),fill="red")
                    overlay.save(directory / "text_location_overlay.png")
                else:
                    attempt["same_text_as_baseline"]=attempt["text"].strip()==task["baseline_text"].strip()
                attempt["status"]="TRUNCATED_UNREVIEWED" if attempt["generation_limit_reached"] else "OUTPUT_UNREVIEWED"
                del batch,output
            except Exception as exc:
                attempt.update(status="FAILED",error=str(exc),traceback=traceback.format_exc())
                torch.cuda.empty_cache()
            write(directory / "attempt.json",attempt)
            run["attempts"].append(attempt)
            write(out / "run.json",run)
            render_review(out,run)
            print(task["id"],attempt["status"],repr(attempt.get("text",""))[:160],flush=True)
        run["gpu_snapshots"].append(gpu_snapshot())
        run["status"]="OUTPUTS_UNREVIEWED" if all(a["status"]=="OUTPUT_UNREVIEWED" for a in run["attempts"]) else "PARTIAL_OR_FAILED"
    except Exception as exc:
        run.update(status="FAILED",error=str(exc),traceback=traceback.format_exc())
        raise
    finally:
        write(out / "run.json",run)
        render_review(out,run)


if __name__=="__main__":
    main()
