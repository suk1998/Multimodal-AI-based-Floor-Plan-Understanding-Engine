"""Live Ref adapter: fresh uploaded bytes, existing OCR and restoration core.

Execution completion never certifies a space. No Target input or fixed PoC data
is read. RDF validation uses the already installed isolated ontology environment.
"""
import copy
import math
import cv2
import numpy as np
import base64
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time

from backend.architectural_observations import analyze, existing_ocr
from backend.reference_restoration import restore_reference
from backend.reference_ontology_adapter import build_region_graphs
from backend.source_contract import validate_binding
from backend.observation_audit import propose_drawing_regions,reobservation_audit

ROOT = Path(__file__).resolve().parents[1]


def validate_ontology(before, after):
    python = ROOT/'integrations/fpue_ontology/.venv/bin/python'
    if not python.is_file():
        return {'status': 'UNAVAILABLE', 'reason': 'EXISTING_ONTOLOGY_ENVIRONMENT_UNAVAILABLE', 'architectural_truth_verified': False}
    with tempfile.TemporaryDirectory(prefix='fpue-reference-ontology-') as directory:
        folder = Path(directory)
        for name, graph in [('before', before), ('after', after)]:
            (folder/(name+'.json')).write_text(json.dumps(graph, ensure_ascii=False, allow_nan=False))
        try:
            result = subprocess.run([str(python), '-B', str(ROOT/'scripts/export_reference_ontology.py'),
                '--before', str(folder/'before.json'), '--after', str(folder/'after.json'), '--output-dir', str(folder/'export')],
                cwd=ROOT, env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'}, capture_output=True, timeout=35)
            if result.returncode:
                return {'status': 'FAILED', 'reason': 'ONTOLOGY_CONTRACT_VALIDATION_FAILED', 'architectural_truth_verified': False}
            reports = json.loads((folder/'export/validation.json').read_text())
            return {'status': 'COMPLETED', 'revisions': reports, 'architectural_truth_verified': False}
        except subprocess.TimeoutExpired:
            return {'status': 'FAILED', 'reason': 'ONTOLOGY_VALIDATION_TIMEOUT', 'architectural_truth_verified': False}


def analyze_reference(pdf_bytes, image_png, binding, ocr_runner=existing_ocr, ontology_validator=validate_ontology):
    started = time.perf_counter()
    validate_binding(binding)
    full = [0, 0, binding['width'], binding['height']]
    if binding['source_role'] != 'Ref' or binding['core_bbox_native'] != full or binding['context_bbox_native'] != full:
        raise ValueError('Reference understanding requires its own whole-page frame, not a Target ROI')
    binding=copy.deepcopy(binding)
    scope=binding.get('drawing_region_bbox_native')
    if scope is not None:
        if not isinstance(scope,list) or len(scope)!=4 or any(type(v) not in [int,float] or not math.isfinite(v) for v in scope):raise ValueError('Finite DrawingRegion bounds required')
        if not (0<=scope[0]<scope[2]<=binding['width'] and 0<=scope[1]<scope[3]<=binding['height']):raise ValueError('DrawingRegion outside source')
        # This is an analysis scope, never a mapped or generated Target ROI.
        binding['core_bbox_native']=list(scope)
        binding['context_bbox_native']=[math.floor(scope[0]),math.floor(scope[1]),math.ceil(scope[2]),math.ceil(scope[3])]
    observed = analyze(pdf_bytes, image_png, binding, ocr_runner)
    text = [{'id': o['observation_id'], 'frame_id': o['frame_id'],
        'polygon_native': o['geometry_native']['points_native'], 'text': o['source_evidence']['text'],
        'recognition_score': o['confidence']} for o in observed['observations'] if o['observation_type'] == 'TextRegion']
    bundle, assets = restore_reference(pdf_bytes, image_png, binding, text, ocr_detection_polygons=[o['geometry_native']['points_native'] for o in observed['observations'] if o['observation_type'] in ['TextRegion','TextDetection']])
    attempts=reobservation_audit(bundle)
    gray=cv2.imdecode(np.frombuffer(image_png,np.uint8),cv2.IMREAD_GRAYSCALE)
    layout=propose_drawing_regions(gray,binding,[o for o in observed['observations'] if o['observation_type']=='TextRegion'])
    before, after = build_region_graphs(bundle)
    validation = ontology_validator(before, after)
    bundle.update({'status': 'PARTIAL', 'frame_id': binding['frame_id'],
        'bundle_kind': 'REFERENCE_UNDERSTANDING_CANDIDATES', 'observation_bundle': observed,
        'region_graph_before': before, 'region_graph_after': after,
        'drawing_regions':layout,'reobservation_audit':attempts,
        'components': {'OBSERVATION': {'status': observed['status']}, 'OCR': observed['components']['OCR'],
            'RESTORATION': {'status': 'COMPLETED' if bundle['cases'] else 'UNAVAILABLE',
                'reason': None if bundle['cases'] else 'NO_RECOGNIZED_SPACE_LABEL_SEEDS'}, 'ONTOLOGY': validation},
        'reobservation_assets': {name: {'sha256': hashlib.sha256(data).hexdigest(), 'media_type': 'image/png',
            'image_base64': base64.b64encode(data).decode('ascii')} for name, data in assets.items()},
        'execution_status': 'COMPLETED', 'understanding_status': 'UNVERIFIED_CANDIDATES',
        'architectural_gate_passed': False,
        'runtime_seconds': time.perf_counter()-started})
    bundle['coverage'].update({'OCR_requested': True, 'new_OCR_run': observed['components']['OCR']['status'] in ['COMPLETED','PARTIAL'], 'OCR_status': observed['components']['OCR']['status'],
        'observation_omissions': observed['coverage']['omitted_due_to_limits'],
        'drawing_region_selection':binding.get('drawing_region_selection'),
        'drawing_region_semantics_verified':False,
        'source_of_OCR': 'CURRENT_UPLOADED_REFERENCE_PIXELS', 'target_input_used': False})
    return bundle
