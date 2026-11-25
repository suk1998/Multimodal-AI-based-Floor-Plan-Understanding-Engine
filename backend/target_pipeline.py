"""Own-input Target observation adapter for the shared geometry/spatial engines."""
import copy
import time
import math

from backend.architectural_observations import analyze, existing_ocr
from backend.observation_audit import propose_drawing_regions
from backend.reference_space_closure import read_labels
from backend.source_contract import validate_binding

import cv2
import numpy as np


def analyze_target(pdf_bytes, image_png, binding, ocr_runner=existing_ocr):
    started=time.perf_counter()
    validate_binding(binding)
    if binding['source_role']!='Target':
        raise ValueError('Own Target input required')
    # These fields are server-created after validating the uploaded document.
    binding=copy.deepcopy(binding)
    scope=binding.get('drawing_region_bbox_native')
    if scope is not None:
        if (not isinstance(scope,list) or len(scope)!=4 or any(type(v) not in (int,float) or not math.isfinite(v) for v in scope)
                or not (0<=scope[0]<scope[2]<=binding['width'] and 0<=scope[1]<scope[3]<=binding['height'])):
            raise ValueError('Target context outside original image')
        binding['core_bbox_native']=list(scope)
        binding['context_bbox_native']=[math.floor(scope[0]),math.floor(scope[1]),math.ceil(scope[2]),math.ceil(scope[3])]
    observation=analyze(pdf_bytes,image_png,binding,ocr_runner)
    observation['independent_understanding']={'source_role':'Target','peer_input_used':False}
    labels=read_labels(observation['observations'],binding['core_bbox_native'])
    by_id={o['observation_id']:o for o in observation['observations']}
    cases=[]
    for label in labels:
        record=by_id[label['observation_id']]
        cases.append({'id':binding['frame_id']+':seed:'+label['observation_id'],
            'label':{'name':label['name'],'seed':label['seed_native'],
                     'observation':{'polygon_native':record['geometry_native']['points_native']}},
            'after':{'status':'PARTIAL_BOUNDARY','polygon_native':None,'reason':'AWAITING_TARGET_GEOMETRY'},
            'full_space_extent_verified':False})
    gray=cv2.imdecode(np.frombuffer(image_png,np.uint8),cv2.IMREAD_GRAYSCALE)
    return {'schema_version':'target_understanding_bundle/1','run_id':observation['run_id'],
        'source_role':'Target','frame_id':binding['frame_id'],'source_binding':binding,
        'status':'PARTIAL','bundle_kind':'TARGET_UNDERSTANDING_CANDIDATES',
        'observation_bundle':observation,'cases':cases,
        'drawing_regions':propose_drawing_regions(gray,binding,[o for o in observation['observations'] if o['observation_type']=='TextRegion']),
        'components':{'OBSERVATION':{'status':observation['status']},'OCR':observation['components']['OCR'],
                      'SPATIAL':{'status':'NOT_RUN'},'RELATIONAL':{'status':'NOT_RUN'}},
        'coverage':{'source_of_OCR':'CURRENT_UPLOADED_TARGET_PIXELS','peer_input_used':False,
                    'drawing_region_selection':binding.get('drawing_region_selection'),
                    'comparison_ROI_modified':False,'analysis_context_native':binding['context_bbox_native']},
        'summary':{'reobserved_region_proposals':0,'unresolved_after':len(cases)},
        'architectural_gate_passed':False,'runtime_seconds':time.perf_counter()-started}
