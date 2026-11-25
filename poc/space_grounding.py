"""Independent, evidence-first Page 4 space grounding research runner.

Reads existing OCR and Qwen artifacts. No model calls, alignment or changes.
Existing output folders are never overwritten. No architectural GT is assumed.
"""
import argparse
from collections import Counter,defaultdict
from dataclasses import asdict
from datetime import datetime,timezone
from io import BytesIO
import hashlib
import html
import json
import math
from pathlib import Path
import re
import shutil
import sys
import time
import traceback

import cv2
import numpy as np
from PIL import Image,ImageDraw
import pymupdf

from run import load_inputs,validate_roi
from space_grounding_geometry import (Settings,axis_points,barrier_masks,component_is_closed,
    component_polygon,group_walls,junction_observations,merge_lines,opening_observations,
    pair_boundaries,partial_boundaries,pdf_to_native_matrix,raster_observations,
    relation_error,sample_line,seed_for_label,vector_observations)

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from phase1_pdf import render_page

QUERIES=['침실1','드레스룸','팬트리']


def sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as file:
        for b in iter(lambda:file.read(8*1024*1024),b''):h.update(b)
    return h.hexdigest()


def normalized(text):return re.sub(r'\s+','',str(text))


def jsonable(value):
    if isinstance(value,dict):return {str(k):jsonable(v) for k,v in value.items()}
    if isinstance(value,(list,tuple)):return [jsonable(v) for v in value]
    if isinstance(value,np.ndarray):return value.tolist()
    if isinstance(value,np.generic):return value.item()
    return value


def write_json(path,value):
    Path(path).write_text(json.dumps(jsonable(value),ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')


def bounds(points):
    p=np.asarray(points,float);return [float(p[:,0].min()),float(p[:,1].min()),float(p[:,0].max()),float(p[:,1].max())]


def claim(subject,predicate,value,status,evidence,scope):
    return {'id':f'{subject}:claim:{predicate}','subject':subject,'predicate':predicate,'value':value,
        'status':status,'verification_status':status,'evidence_ids':evidence,'scope':scope,
        'criteria_version':'space_grounding_claims/1','independent_architectural_gt':False}


def entity(identity,kind,binding,geometry,evidence,algorithm,uncertainties=None,text_ids=None):
    return {'id':identity,'type':kind,'entity_family':'Opening' if kind in ['Opening','Door','Window'] else kind,
        'role':binding['role'],'frame_id':binding['frame_id'],'source_binding':binding,
        'geometry':{'coordinate_system':'image_native_pixels',**geometry},
        'observation_sources':[{'kind':'own PDF and native raster observations','pdf_sha256':binding.get('source_sha256'),
            'image_sha256':binding.get('image_sha256'),'page':binding.get('page'),'frame_id':binding['frame_id']}],
        'algorithm':algorithm,'algorithm_version':'space_grounding/1','geometry_evidence_ids':evidence,
        'related_text_region_ids':text_ids or [],'related_entity_ids':[],
        'claim_status':'PROPOSED','verification_status':'PROPOSED',
        'uncertainties':uncertainties or [],'claims':[claim(identity,'architectural_type',kind,'PROPOSED',evidence,
            'candidate interpretation; line detection or label reading is not architectural verification')]}


def boxed_label(observation,rectangles):
    box=bounds(observation['polygon_native']);w=box[2]-box[0];h=box[3]-box[1];cx=(box[0]+box[2])/2;cy=(box[1]+box[3])/2
    candidates=[]
    for rectangle in rectangles:
        a,b,c,d=rectangle['bbox']
        tolerance=max(2,h*.30)
        overlap=max(0,min(c,box[2])-max(a,box[0]))*max(0,min(d,box[3])-max(b,box[1]))/max(w*h,1)
        # OCR often includes the label frame or antialias margin. Requiring the
        # OCR box to fit perfectly would miss a second room and permit a merge.
        if a-tolerance<=box[0] and b-tolerance<=box[1] and c+tolerance>=box[2] and d+tolerance>=box[3] and a<=cx<=c and b<=cy<=d and .65<=(d-b)/max(h,1)<=2 and .55<=(c-a)*(d-b)/max(w*h,1)<=3.5 and overlap>.55:
            candidates.append(rectangle['id'])
    return candidates


def label_observations(ocr,rectangles):
    result=[]
    for o in ocr:
        text=normalized(o['text']);rects=boxed_label(o,rectangles)
        if text in QUERIES or (2<=len(text)<=12 and re.search('[가-힣]',text) and rects):
            result.append({**o,'label_rectangle_observation_ids':rects,
                'label_semantics':'query or boxed Korean text; room interpretation is a hypothesis, not a fixed room template'})
    return result


def boundary_segments(wall_ids,rays,walls):
    result=[]
    for wall in walls:
        if wall['id'] not in wall_ids:continue
        hits=np.array([r['hit_native'] for r in rays if r.get('wall_id')==wall['id'] and r.get('hit_native') is not None])
        if not len(hits):continue
        axial=0 if wall['axis']=='H' else 1;normal=1-axial
        for piece in wall.get('physical_pieces',wall['pieces']):
            lo=max(piece['start'],float(hits[:,axial].min()));hi=min(piece['end'],float(hits[:,axial].max()))
            if hi-lo<1:continue
            face=piece['normal']+np.sign(float(np.median(hits[:,normal]))-piece['normal'])*piece['thickness_px']/2
            result.append({'wall_id':wall['id'],'points_native':axis_points(wall['axis'],face,lo,hi),
                'geometry_status':'SUPPORTED','architectural_boundary_status':'PROPOSED',
                'evidence_ids':[piece['observation_id']],
                'coverage':'sampled visible subset; ends are analysis limits, not physical structural endpoints'})
    return result


def build_spaces(ocr,vector,walls,openings,barriers,raster,height,binding,settings):
    role=binding['role'];labels=label_observations(ocr,vector['rectangles']);seeds=[];issues=[];by_component=defaultdict(list)
    for label in labels:
        seed,error=seed_for_label(label['polygon_native'],barriers,raster,height)
        item={'ocr':label,'seed':seed,'error':error}
        if seed:by_component[seed['component_id']].append(item)
        seeds.append(item)
    result=[];component_space={};minimum=height*height*settings.min_closed_area_text_height_sq
    eligible_components={i for i,stats in enumerate(barriers['component_stats']) if i and component_is_closed(stats,barriers['total'].shape,minimum)}
    def make(identity,component,label_items,seed,errors):
        text_ids=[x['ocr']['entity_id'] for x in label_items];closed=component in eligible_components and len(label_items)<=1 and seed is not None and 'MULTIPLE_LABEL_COMPONENT_CONFLICT' not in errors
        if component is not None and component not in eligible_components:errors.append('FREE_COMPONENT_OPEN_TO_PAGE_OR_TOO_SMALL; region polygon UNKNOWN')
        if len(label_items)>1:errors.append('MULTIPLE_LABELS_SHARE_COMPONENT; no merged room polygon')
        wall_ids=[];rays=[];segments=[]
        if seed:
            wall_ids,rays=partial_boundaries(seed,walls,barriers,settings)
            boundary_rays=rays
            if not closed:
                distances=[float(np.linalg.norm(np.asarray(r['hit_native'])-seed['point'])) for r in rays if r.get('wall_id')]
                cutoff=max(height*3,min(distances)*settings.partial_boundary_local_distance_factor) if distances else 0
                boundary_rays=[]
                for r in rays:
                    distance=float(np.linalg.norm(np.asarray(r['hit_native'])-seed['point'])) if r.get('hit_native') is not None else None
                    r['local_boundary_assignment']='PROPOSED_LOCAL' if distance is not None and distance<=cutoff else 'UNKNOWN_OR_DISTANT_VISIBLE_STRUCTURE_NOT_ASSIGNED'
                    if r['local_boundary_assignment']=='PROPOSED_LOCAL':boundary_rays.append(r)
                wall_ids=sorted({r['wall_id'] for r in boundary_rays if r.get('wall_id')})
            segments=boundary_segments(wall_ids,boundary_rays,walls)
        polygon=component_polygon(component,barriers) if closed else None
        geometry={'seed_native':seed['point'] if seed else None,'seed_observation':seed,
            'polygon_native':polygon,'partial_physical_boundary_segments':segments,
            'logical_opening_ids':sorted({openings[r['opening_index']-1]['entity_id'] for r in (boundary_rays if seed else []) if r.get('opening_index')}),
            'region_status':'ENCLOSED_UNDER_CONDITIONAL_GEOMETRY_HYPOTHESES' if closed else 'PARTIAL_BOUNDARY_UNKNOWN_REGION',
            'bbox_xyxy':None,'component_observation_id':f'{role}:free_component:{component}' if component else None}
        if polygon and polygon['outer_rings']:geometry['bbox_xyxy']=bounds([p for ring in polygon['outer_rings'] for p in ring])
        elif segments:geometry['analysis_extent_xyxy']=bounds([p for s in segments for p in s['points_native']])
        e=entity(identity,'Space',binding,geometry,[f'{role}:free_component:{component}'] if component else [],
            'geometry barriers + evidenced logical separators + clear interior seed; no OCR-box expansion',errors,text_ids)
        e['name_hypotheses']=[{'text':x['ocr']['text'],'text_region_id':x['ocr']['entity_id'],'status':'PROPOSED'} for x in label_items]
        e['related_entity_ids']=wall_ids+geometry['logical_opening_ids'];e['boundary_rays']=rays
        e['component_id']=component;e['claims'].append(claim(identity,'observed_region_enclosure',bool(closed),'SUPPORTED' if closed else 'PROPOSED',
            e['geometry_evidence_ids'],'enclosure only under this run’s physical Wall and logical Opening hypotheses, not verified room boundaries'))
        if closed:component_space[component]=identity
        result.append(e)
    used=set()
    for item in seeds:
        component=item['seed']['component_id'] if item['seed'] else None
        siblings=by_component[component] if component else [item]
        shared_open=component not in eligible_components or len(siblings)>1
        identity=f'{role}:Space:label:{item["ocr"]["entity_id"].split(":")[-1]}' if shared_open else f'{role}:Space:component:{component:03d}'
        if identity in used:continue
        used.add(identity)
        errors=[item['error']] if item['error'] else []
        if len(siblings)>1:errors.append('Component shared by other boxed labels; names not merged and no complete polygon inferred.')
        make(identity,component,[item],item['seed'],errors+(['MULTIPLE_LABEL_COMPONENT_CONFLICT'] if len(siblings)>1 else []))
        if len(siblings)>1:
            result[-1]['geometry']['polygon_native']=None;result[-1]['geometry']['bbox_xyxy']=None;result[-1]['geometry']['region_status']='PARTIAL_BOUNDARY_UNKNOWN_REGION'
            result[-1]['claims'][-1]['value']=False;result[-1]['claims'][-1]['verification_status']='PROPOSED';result[-1]['claims'][-1]['status']='PROPOSED'
            component_space.pop(component,None)
    for component in sorted(eligible_components):
        if component in by_component:continue
        mask=(barriers['components']==component)&(raster['ink']==0)&(raster['text_mask']==0)
        clearance=cv2.distanceTransform((barriers['components']==component).astype(np.uint8)*255,cv2.DIST_L2,5)
        clearance[~mask]=0
        y,x=np.unravel_index(np.argmax(clearance),clearance.shape)
        if clearance[y,x]<height*.4:continue
        seed={'point':[int(x),int(y)],'component_id':component,'clearance_to_barrier_px':float(clearance[y,x]),
            'method':'unlabeled enclosed geometry component interior; no OCR or VLM seed'}
        make(f'{role}:Space:component:{component:03d}',component,[],seed,['No supported space name; architectural room semantics UNKNOWN.'])
    attempts=[{'query':q,'role':role,'frame_id':binding['frame_id'],'status':'OCR_OBSERVED' if any(normalized(o['text'])==q for o in ocr) else 'NOT_DETECTED_IN_EXISTING_OCR',
        'space_ids':[e['id'] for e in result if any(normalized(n['text'])==q for n in e['name_hypotheses'])],
        'absence_is_not_proven':True} for q in QUERIES]
    return result,component_space,attempts


def build_relations(elements,spaces,walls,openings,barriers,binding):
    by_id={e['id']:e for e in elements+spaces};relations=[];attempts=[];role=binding['role']
    def add(subject,predicate,objects,evidence,status='PROPOSED',**extra):
        r={'id':f'{role}:relation:{len(relations):04d}','subject_id':subject,'predicate':predicate,'object_ids':objects,
            'role':role,'frame_id':binding['frame_id'],'source_binding':binding,'status':status,'verification_status':status,
            'evidence_ids':evidence,'criteria_version':'space_grounding_relation_types/1',**extra}
        def location(identity):
            e=by_id.get(identity,{})
            return {'entity_id':identity,'type':e.get('type'),'bbox_xyxy':e.get('geometry',{}).get('bbox_xyxy'),
                'seed_native':e.get('geometry',{}).get('seed_native'),'coordinate_system':'image_native_pixels'}
        r['native_locations']={'subject':location(subject),'objects':[location(i) for i in objects],
            'semantics':'entity locations for evidence navigation, not a measured physical connector'}
        error=relation_error(r,by_id)
        if error:attempts.append({**r,'attempt_status':'REJECTED_BY_TYPE_OR_EVIDENCE_CONSTRAINT','reason':error})
        else:relations.append(r)
    component_space={s['component_id']:s['id'] for s in spaces if s['geometry']['polygon_native'] is not None}
    for space in spaces:
        for text_id in space['related_text_region_ids']:
            add(text_id,'labels',[space['id']],space['geometry_evidence_ids']+by_id[text_id]['geometry_evidence_ids'],
                'SUPPORTED' if space['geometry']['polygon_native'] is not None else 'PROPOSED',
                scope='text location and seed inside same candidate component; recognized name correctness remains PROPOSED')
        for wall_id in sorted({b['wall_id'] for b in space['geometry']['partial_physical_boundary_segments']}):
            if space['geometry']['polygon_native'] is None:
                attempts.append({'role':role,'frame_id':binding['frame_id'],'subject_id':space['id'],'predicate':'boundedBy','object_ids':[wall_id],
                    'attempt_status':'DEFERRED','reason':'REGION_OPEN; visible local wall is not sufficient to assign the space boundary',
                    'evidence_ids':space['geometry_evidence_ids']+by_id[wall_id]['geometry_evidence_ids']})
                continue
            add(space['id'],'boundedBy',[wall_id],space['geometry_evidence_ids']+by_id[wall_id]['geometry_evidence_ids'],
                'SUPPORTED' if space['geometry']['polygon_native'] is not None else 'PROPOSED',
                boundary_mode='physical candidate band; partial sampled visibility, not complete or VERIFIED architectural boundary')
        for opening_id in space['geometry']['logical_opening_ids']:
            if space['geometry']['polygon_native'] is None:
                attempts.append({'role':role,'frame_id':binding['frame_id'],'subject_id':space['id'],'predicate':'boundedBy','object_ids':[opening_id],
                    'attempt_status':'DEFERRED','reason':'REGION_OPEN; logical separator-to-space association not established',
                    'evidence_ids':space['geometry_evidence_ids']+by_id[opening_id]['geometry_evidence_ids']})
                continue
            add(space['id'],'boundedBy',[opening_id],space['geometry_evidence_ids']+by_id[opening_id]['geometry_evidence_ids'],boundary_mode='conditional logical separator; not a physical filled wall')
    for opening in openings:
        for wall_id in opening['host_wall_ids']:add(wall_id,'hosts',[opening['entity_id']],[opening['id']],scope='candidate band/gap association; parent wall identity not VERIFIED')
        if opening['kind']!='Door':continue
        center=np.mean(opening['logical_segment'],axis=0);offset=opening['host_thickness_px']/2+5
        normal=np.array([0,1]) if opening['axis']=='H' else np.array([1,0]);samples=[center-normal*offset,center+normal*offset]
        components=[]
        for p in samples:
            x,y=np.rint(p).astype(int)
            components.append(int(barriers['components'][y,x]) if 0<=x<barriers['total'].shape[1] and 0<=y<barriers['total'].shape[0] else None)
        ids=[component_space.get(c) for c in components]
        evidence={'opening_observation_id':opening['id'],'arc':opening['arc'],'side_sample_points_native':[p.tolist() for p in samples],
            'side_component_ids':components,'side_space_ids':ids,'conditional_logical_separator':opening['eligible_logical_boundary']}
        if all(ids) and ids[0]!=ids[1]:add(opening['entity_id'],'connects',ids,[opening['id']],opening_and_two_sides_evidence=evidence)
        else:attempts.append({'role':role,'frame_id':binding['frame_id'],'predicate':'connects','subject_id':opening['entity_id'],
            'attempt_status':'DEFERRED','reason':'TWO_DISTINCT_ENCLOSED_SPACE_SIDES_NOT_ESTABLISHED','evidence':evidence})
    for symbol in [e for e in elements if e['type']=='Symbol']:
        for text_id in symbol['related_text_region_ids']:add(text_id,'labels',[symbol['id']],symbol['geometry_evidence_ids'],scope='text region within observed circle; not a window code-to-window assertion')
        attempts.append({'role':role,'frame_id':binding['frame_id'],'predicate':'refersTo','subject_id':symbol['id'],
            'attempt_status':'DEFERRED','reason':'DIRECT_POINTER_TO_PHYSICAL_OPENING_NOT_VERIFIED; nearest neighbor not used'})
    # Adjacency is restricted to distinct enclosed components on opposite sides
    # of the same observed wall piece, not overlapping space bboxes.
    seen=set()
    for wall in walls:
        for p in wall['physical_pieces']:
            center=np.mean(axis_points(wall['axis'],wall['normal'],p['start'],p['end']),axis=0)
            normal=np.array([0,1]) if wall['axis']=='H' else np.array([1,0]);ids=[]
            for point in [center-normal*(wall['thickness_px']/2+4),center+normal*(wall['thickness_px']/2+4)]:
                x,y=np.rint(point).astype(int);cid=int(barriers['components'][y,x]) if 0<=x<barriers['total'].shape[1] and 0<=y<barriers['total'].shape[0] else None
                ids.append(component_space.get(cid))
            if all(ids) and ids[0]!=ids[1] and tuple(sorted(ids)) not in seen:
                seen.add(tuple(sorted(ids)));add(ids[0],'adjacentTo',[ids[1]],[p['observation_id']],scope='opposite sides of same candidate physical wall; no passage asserted')
    return relations,attempts


def analyze_drawing(inputs,ocr_root,role,binding,settings):
    """No peer role, peer OCR or VLM argument is accepted by geometry extraction."""
    started=time.perf_counter();binding={**binding,'role':role}
    source=Path(binding['source_path']);image_path=inputs/role/'page.png'
    if sha(source)!=binding['source_sha256'] or sha(image_path)!=binding['image_sha256']:raise ValueError('Original PDF/raster binding mismatch')
    image=Image.open(image_path).convert('RGB');pixels=np.asarray(image)
    if image.size!=(binding['width'],binding['height']):raise ValueError('Native dimensions mismatch')
    rendered=render_page(source.read_bytes(),binding['page'],binding['extraction_zoom'])
    rerender=np.asarray(Image.open(BytesIO(rendered.png)).convert('RGB'))
    if not np.array_equal(pixels,rerender):raise ValueError('PDF rerender does not match original raster; no coordinate success assumed')
    ocr_file=ocr_root/role/'observations.json';ocr=json.loads(ocr_file.read_text());ocr_source={'path':str(ocr_file),'sha256':sha(ocr_file),'models':json.loads((ocr_root/'run.json').read_text())['models']}
    for o in ocr:
        if o['frame_id']!=binding['frame_id']:raise ValueError('OCR frame mismatch')
        o['entity_id']=f'{role}:TextRegion:{o["id"].split(":")[-1]}'
    text_height=float(np.median([bounds(o['polygon_native'])[3]-bounds(o['polygon_native'])[1] for o in ocr])) if ocr else max(8,binding['height']/100)
    with pymupdf.open(source) as document:
        page=document[binding['page']-1];pixmap=page.get_pixmap(matrix=pymupdf.Matrix(binding['extraction_zoom'],binding['extraction_zoom']),colorspace=pymupdf.csRGB,alpha=False)
        matrix=pdf_to_native_matrix(page,binding['extraction_zoom'],(pixmap.x,pixmap.y))
        vector=vector_observations(page,matrix)
        pdf_transform=page.transformation_matrix
        pdf_user_to_local=[[pdf_transform.a,pdf_transform.c,pdf_transform.e],[pdf_transform.b,pdf_transform.d,pdf_transform.f],[0,0,1]]
        frame={'pdf_user_to_pymupdf_local':pdf_user_to_local,'pymupdf_unrotated_local_to_native':matrix,
            'pdf_user_to_native':(np.array(matrix)@np.array(pdf_user_to_local)).tolist(),
            'rotation':page.rotation,'mediabox':list(page.mediabox),'cropbox':list(page.cropbox),'page_rect':list(page.rect),
            'pixmap_origin':[pixmap.x,pixmap.y],'native_size':list(image.size),
            'rerender_pixel_differences':0,'coordinate_validation':'VERIFIED_RERENDER_IDENTITY_ONLY_NOT_WALL_SEMANTICS'}
    for collection in ['segments','arcs','symbols','rectangles']:
        for observation in vector[collection]:observation['id']=role+':'+observation['id'];observation['role']=role;observation['frame_id']=binding['frame_id']
    raster=raster_observations(cv2.cvtColor(pixels,cv2.COLOR_RGB2GRAY),[o['polygon_native'] for o in ocr],text_height,settings)
    lines,line_rejections=merge_lines(vector['segments'],raster,text_height,settings)
    for line in lines:line['id']=role+':'+line['id']
    pairs,pair_rejections=pair_boundaries(lines,raster,text_height,settings)
    raster_sources=[{**l,'id':role+':'+l['id']} for l in raster['segments'] if np.linalg.norm(np.diff(l['points'],axis=0))>=text_height*3]
    raster_lines,raster_rejections=merge_lines(raster_sources,raster,text_height,settings,'raster')
    for line in raster_lines:line['id']=role+':'+line['id']
    raster_pairs,raster_pair_rejections=pair_boundaries(raster_lines,raster,text_height,settings)
    walls=group_walls(pairs,role,text_height);openings,eligible_arcs=opening_observations(walls,lines,vector['arcs'],raster,text_height,settings,role)
    for opening in openings:opening['entity_id']=opening['id'].replace('opening_observation',opening['kind'])
    barriers=barrier_masks(walls,openings,raster['ink'].shape);junctions=junction_observations(walls,role)
    elements=[]
    for o in ocr:
        e=entity(o['entity_id'],'TextRegion',binding,{'polygon_native':o['polygon_native'],'bbox_xyxy':bounds(o['polygon_native']),
            'semantics':'OCR text extent, never a physical space boundary'},[o['id']],'preserved PP-OCRv5 detection + recognition',
            ['Recognition score is uncalibrated; OCR text is not GT.'])
        e['text']=o['text'];e['recognition_score']=o['recognition_score'];e['observation_sources']=[{**ocr_source,'observation_id':o['id']}];elements.append(e)
    for wall in walls:
        ps=wall['physical_pieces'];polygons=[p['polygon'] for p in ps]
        matched=[]
        for line in raster_lines:
            if line['axis']!=wall['axis']:continue
            for p in wall['pieces']:
                if min(abs(line['normal']-p['normal']-p['thickness_px']/2),abs(line['normal']-p['normal']+p['thickness_px']/2))<3 and min(line['end'],p['end'])-max(line['start'],p['start'])>min(p['end']-p['start'],line['end']-line['start'])*.5:
                    matched.append(line['id']);break
        e=entity(wall['id'],'Wall',binding,{'polygons_native':polygons,'bbox_xyxy':bounds([v for p in polygons for v in p]) if polygons else None,
            'axis':wall['axis'],'thickness_px_hypothesis':wall['thickness_px'],'physical_pieces':ps,
            'parent_grouping':'collinear compatible bands; same physical parent is PROPOSED, gaps are not filled',
            'endpoints': [{'points_native':axis_points(p['axis'],p['normal'],p['start'],p['end']),
                'physical_endpoint_status':'UNKNOWN','reason':p.get('endpoint_semantics','observed primitive range, not verified physical endpoint')} for p in ps]},
            [p['observation_id'] for p in wall['pieces']],'parallel PDF boundaries + stroke/strip/raster support + optional LSD corroboration',
            ['Wall identity and architectural role remain hypotheses; stroke width or a layer name is not sufficient proof.'])
        connections=[j for j in junctions if wall['id'] in j['wall_ids']]
        e['geometry_evidence']={'parallel_pair_ids':e['geometry_evidence_ids'],
            'thickness_px_hypothesis':wall['thickness_px'],'junction_observation_ids':[j['id'] for j in connections],
            'connected_candidate_wall_ids':sorted({w for j in connections for w in j['wall_ids'] if w!=wall['id']}),
            'associated_opening_observation_ids':[g['id'] for g in openings if wall['id'] in g['host_wall_ids']],
            'raster_lsd_boundary_support_ids':sorted(set(matched)),
            'assessment':'MULTIPLE_CUES_STILL_PROPOSED' if connections or matched else 'ISOLATED_PARALLEL_GEOMETRY_ONLY',
            'limitation':'dependent PDF/raster cues are not independent architectural GT; fixture and frame bands can still be false walls'}
        if not connections:e['uncertainties'].append('No junction with another wall band was established; isolated band role needs review.')
        e['raster_lsd_corroboration_ids']=sorted(set(matched));e['claims'].append(claim(e['id'],'parallel_band_geometry',True,'SUPPORTED',e['geometry_evidence_ids'],'native parallel separation and raster visibility only'))
        elements.append(e)
    for opening in openings:
        e=entity(opening['entity_id'],opening['kind'],binding,{'polygon_native':opening['polygon'],'bbox_xyxy':bounds(opening['polygon']),
            'logical_boundary_segment_native':opening['logical_segment'],'logical_boundary_enabled':opening['eligible_logical_boundary'],
            'width_px_hypothesis':opening['width_px'],'physical_geometry_semantics':'gap/threshold analysis band, not a filled solid wall',
            'arc_observation_id':opening['arc']['arc_id'] if opening['arc'] else None},[opening['id']],
            'wall gap/threshold + visible arc/leaf or parallel tracks/end-cap evidence',[opening['kind_uncertainty']]+opening['failure_reasons'])
        e['related_entity_ids']=opening['host_wall_ids'];e['geometry_evidence']=opening
        e['claims'].append(claim(e['id'],'opening_pattern_observed',True,'SUPPORTED' if opening['eligible_logical_boundary'] else 'PROPOSED',
            [opening['id']],'documented gap/arc/track visual criteria; subtype and passage connectivity not VERIFIED'))
        elements.append(e)
    for index,symbol in enumerate(vector['symbols']):
        inside=[o['entity_id'] for o in ocr if symbol['bbox'][0]-1<=np.mean(o['polygon_native'],axis=0)[0]<=symbol['bbox'][2]+1 and symbol['bbox'][1]-1<=np.mean(o['polygon_native'],axis=0)[1]<=symbol['bbox'][3]+1]
        e=entity(f'{role}:Symbol:{index:03d}','Symbol',binding,{'bbox_xyxy':symbol['bbox'],'circle_center_native':symbol['center']},
            [symbol['id']],'approximately circular four-Bezier PDF path + own-role OCR containment',
            ['Circle or text code does not identify a physical window. No nearest-symbol-to-window association.'],inside)
        elements.append(e)
    spaces,component_space,query_attempts=build_spaces(ocr,vector,walls,openings,barriers,raster,text_height,binding,settings)
    relations,relation_attempts=build_relations(elements,spaces,walls,openings,barriers,binding)
    by_id={e['id']:e for e in elements+spaces}
    for relation in relations:
        subject=by_id[relation['subject_id']]
        for identity in relation['object_ids']:
            subject['related_entity_ids'].append(identity);by_id[identity]['related_entity_ids'].append(subject['id'])
    for e in elements+spaces:e['related_entity_ids']=sorted(set(e['related_entity_ids']))
    relevant_sources={s for l in lines for s in l['source_ids']}
    primitive_examples=[s for s in vector['segments'] if s['id'] in relevant_sources]
    for line in lines:line['role']=role;line['frame_id']=binding['frame_id']
    for p in [p for wall in walls for p in wall['pieces']]:p['role']=role;p['frame_id']=binding['frame_id']
    observation={'role':role,'frame_id':binding['frame_id'],'source_binding':binding,'frame_registry':frame,
        'pdf_vector_inspection':vector['summary'],'reference_text_height_px':text_height,'config':asdict(settings),
        'ocr_label_frame_policy':{'bbox_margin_height_ratio':.30,'minimum_ocr_rectangle_overlap_fraction':.55,
            'purpose':'match an observed label frame despite detector margin; never expand this box into a room boundary',
            'room_interpretation_status':'PROPOSED; boxed equipment text may also be ambiguous'},
        'pdf_line_observations':lines,'source_primitive_details_for_retained_lines':primitive_examples,
        'pdf_rejected_observations':line_rejections,'wall_pair_rejections':pair_rejections,
        'parallel_boundary_observations':[p for wall in walls for p in wall['pieces']],
        'raster':{'algorithm':'OpenCV Otsu, LSD, morphology audit, distance transforms, constrained barrier components',
            'dark_ink_threshold':raster['threshold'],'visibility_ink_threshold':255-settings.visibility_min_contrast_8bit,
            'raw_lsd_count':len(raster['segments']),'long_axis_lines':raster_lines,'parallel_pairs':raster_pairs,
            'line_rejections':raster_rejections,'pair_rejections':raster_pair_rejections,'morphology_audit':raster['morphology_audit'],
            'fallback_policy':'Raster pairs corroborate and expose missed/ambiguous structure. Uncorroborated LSD support width is not wall stroke width and does not silently become a physical barrier.'},
        'arc_observations':eligible_arcs,'opening_observations':openings,'junction_observations':junctions,
        'barrier_observation':{'id':f'{role}:barriers','role':role,'frame_id':binding['frame_id'],
            'physical_mask_artifact':f'{role}/physical_barriers.png','logical_mask_artifact':f'{role}/logical_boundaries.png',
            'wall_pair_observation_ids':[p['observation_id'] for wall in walls for p in wall['pieces']],
            'logical_opening_observation_ids':[g['id'] for g in openings if g['eligible_logical_boundary']],
            'conditional_interpretation_cuts':[g['id'] for g in openings if g['origin']=='ARC_OVER_VISIBLE_THRESHOLD'],
            'connectivity':4,'semantic_scope':'physical candidate bands and separate conditional logical closures; not GT masks'},
        'rectangle_observations':vector['rectangles'],'symbol_observations':vector['symbols'],
        'free_component_observations':[{'id':f'{role}:free_component:{i}','role':role,'frame_id':binding['frame_id'],'stats_xywh_area':stats.tolist(),
            'closed_region_eligible':component_is_closed(stats,barriers['total'].shape,text_height**2*settings.min_closed_area_text_height_sq),
            'derived_from':[f'{role}:barriers'],
            'status':'SUPPORTED_GEOMETRY_COMPONENT_NOT_VERIFIED_ROOM'} for i,stats in enumerate(barriers['component_stats']) if i],
        'query_observation_attempts':query_attempts,'relation_attempts':relation_attempts,
        'source_dependencies':'PDF and its rendered raster are dependent observations, not independent architectural GT',
        'runtime_seconds':time.perf_counter()-started,'metrics':None,'architectural_gt':None}
    debug={'image':image,'raster':raster,'barriers':barriers,'walls':walls,'openings':openings,'junctions':junctions}
    return observation,spaces,elements,relations,debug


def compare_vlm(vlm_root,role,spaces,elements,barriers):
    attempts=[];comparisons=[];invalid=[];sources=[]
    if vlm_root is None:return {'status':'NOT_REQUESTED','model_calls':0,'comparisons':[],'sources':[],'failures':[],'relation_constraint_failures':[]}
    state_path=vlm_root/'status.json'
    if not state_path.is_file():return {'status':'MISSING_EXISTING_VLM_STATUS','model_calls':0,'comparisons':[],'sources':[],'failures':['No prior status.json; no output fabricated'],'relation_constraint_failures':[]}
    state=json.loads(state_path.read_text())
    for key,model in state['models'].items():
        directory=Path(model.get('spatial_output',''))
        run_path=directory/'run.json'
        if not run_path.is_file():attempts.append({'model':key,'status':'MISSING_PRIOR_RUN','path':str(run_path)});continue
        run=json.loads(run_path.read_text());sources.append({'model':key,'path':str(run_path),'sha256':sha(run_path),'revision':run['revision']})
        for attempt in run['attempts']:
            if attempt['view']['role']!=role:continue
            if attempt['view']['binding']['frame_id']!=elements[0]['frame_id']:
                attempts.append({'model':key,'view':attempt['view']['id'],'status':'FRAME_MISMATCH_PRIOR_VLM_NOT_USED'});continue
            proposed=attempt.get('proposal')
            if proposed is None:attempts.append({'model':key,'view':attempt['view']['id'],'status':attempt['status'],'parse_error':attempt.get('parse_error'),'raw_response':attempt.get('response'),'geometry_candidates_created':False});continue
            by_id={e['id']:{**e,'frame_id':attempt['view']['binding']['frame_id']} for e in proposed['entities']}
            for relation in proposed['relations']:
                error=relation_error(relation,by_id)
                if error:invalid.append({'model':key,'view':attempt['view']['id'],'raw_relation':relation,'constraint_failure':error,'status':'PROPOSED_ORIGINAL_RETAINED_NOT_ADOPTED'})
            for candidate in proposed['entities']:
                if candidate.get('type') not in ['Space','Wall','Door','Window','Opening','Symbol']:continue
                label=normalized(candidate.get('label'));matches=[s for s in spaces if any(normalized(n['text'])==label for n in s['name_hypotheses'])]
                item={'model':key,'view':attempt['view']['id'],'role':role,'frame_id':attempt['view']['binding']['frame_id'],
                    'vlm_entity':candidate,'status':'PROPOSED','comparison_semantics':'hypothesis extent diagnostic, not GT accuracy, verified identity or architectural correspondence',
                    'matched_by':'same independently observed OCR string for Space; geometric overlap for other types; neither is accepted physical identity','geometry_space_ids':[s['id'] for s in matches] if candidate.get('type')=='Space' else [],
                    'geometry_regions':[{'space_id':s['id'],'region_status':s['geometry']['region_status'],'bbox_xyxy':s['geometry']['bbox_xyxy']} for s in matches]}
                bbox=candidate.get('bbox_native_xyxy')
                item['geometry_region_extent_is_not_model_box']=True
                item['location_status']='UNLOCATED_MODEL_OUTPUT' if bbox is None else 'MODEL_ESTIMATE'
                if bbox:
                    x1,y1,x2,y2=np.asarray(bbox).astype(int);patch=barriers['physical'][max(0,y1):y2,max(0,x1):x2]
                    item['candidate_box_physical_band_fraction']=float(np.mean(patch>0)) if patch.size else None
                    pool=spaces if candidate['type']=='Space' else [e for e in elements if e['type']==candidate['type'] or candidate['type'] in ['Door','Window','Opening'] and e['type'] in ['Door','Window','Opening']]
                    overlaps=[]
                    for e in pool:
                        b=e['geometry'].get('bbox_xyxy')
                        if not b:continue
                        area=max(0,min(bbox[2],b[2])-max(bbox[0],b[0]))*max(0,min(bbox[3],b[3])-max(bbox[1],b[1]))
                        if area>0:overlaps.append({'entity_id':e['id'],'type':e['type'],'bbox_overlap_area_px2':area})
                    item['overlapping_geometry_candidates']=overlaps
                    item['overlap_policy']='diagnostic only; no object matching, grounding approval or correspondence generated'
                comparisons.append(item)
    return {'status':'EXISTING_OUTPUTS_COMPARED_NOT_ADOPTED','role':role,'model_calls':0,'sources':sources,
        'comparisons':comparisons,'failures':attempts,'relation_constraint_failures':invalid,
        'policy':'VLM bboxes did not create wall barriers, seeds, polygons or semantic approvals.'}


def render_drawing(directory,observation,spaces,elements,relations,debug,comparison,vlm_root):
    directory.mkdir(parents=True,exist_ok=False);image=debug['image'];image.save(directory/'source_page.png')
    physical=debug['barriers']['physical'];logical=debug['barriers']['logical'];raster=debug['raster']
    for name,mask in [('physical_barriers',physical),('logical_boundaries',logical),('visible_ink',raster['visible_ink']),('dark_ink',raster['ink']),('morphology_diagnostic',raster['morphology'])]:Image.fromarray(mask).save(directory/(name+'.png'))
    np.savez_compressed(directory/'component_evidence.npz',component_labels=debug['barriers']['components'],wall_owner=debug['barriers']['wall_owner'],opening_owner=debug['barriers']['opening_owner'])
    geometry=image.copy();layer=np.asarray(geometry).copy();layer[physical>0]=(np.asarray(layer[physical>0],float)*.25+np.array([225,65,35])*.75).astype(np.uint8);geometry=Image.fromarray(layer);d=ImageDraw.Draw(geometry)
    for wall in debug['walls']:
        if wall['physical_pieces']:d.text(tuple(wall['physical_pieces'][0]['polygon'][0]),wall['id'].split(':')[-1]+' W/P',fill='#b02010')
    for opening in debug['openings']:
        color='#305cce' if opening['kind']=='Door' else '#bf7300' if opening['kind']=='Opening' else '#008eab'
        d.line(opening['logical_segment'],fill=color,width=3)
        d.text(tuple(opening['logical_segment'][0]),opening['entity_id'].split(':')[-1]+' '+opening['kind'][0]+'/P',fill=color)
    for arc in observation['arc_observations']:
        d.line([tuple(p) for p in arc['polyline']],fill='#527fa0',width=1)
        if arc.get('host_assignment_status')=='UNRESOLVED_HOST_OR_ORIENTATION':
            d.text(tuple(arc['center']),'ARC / UNKNOWN HOST',fill='#416b89')
    for j in debug['junctions']:
        x,y=j['point'];d.ellipse((x-2,y-2,x+2,y+2),outline='#7030a0',width=1)
    geometry.save(directory/'geometry_overlay.png')
    rejected=image.copy();rejection_draw=ImageDraw.Draw(rejected)
    for example in observation['pdf_rejected_observations']['examples']:
        color='#b047a2' if example['reason']=='OCR_TEXT_OVERLAP' else '#d48b00'
        rejection_draw.line([tuple(p) for p in example['source']['points']],fill=color,width=2)
    rejected.save(directory/'line_exclusion_examples.png')
    weak=image.copy();weak_draw=ImageDraw.Draw(weak)
    for example in observation['wall_pair_rejections']['examples']:
        p=example['polygon'];weak_draw.line([tuple(q) for q in p+[p[0]]],fill='#a07820',width=1)
    weak.save(directory/'weak_pair_examples.png')
    grounded=geometry.copy();g=ImageDraw.Draw(grounded,'RGBA')
    for space in spaces:
        polygon=space['geometry']['polygon_native']
        if polygon:
            for ring in polygon['outer_rings']:g.polygon([tuple(p) for p in ring],fill=(45,175,80,45),outline=(20,130,55,255),width=2)
        else:
            for segment in space['geometry']['partial_physical_boundary_segments']:g.line([tuple(p) for p in segment['points_native']],fill=(230,90,0,255),width=3)
        point=space['geometry']['seed_native']
        if point:
            x,y=point;g.ellipse((x-4,y-4,x+4,y+4),fill=(20,200,70,255),outline='black');g.text((x+6,y+4),space['id'].split(':')[-1]+' S/P'+(' UNKNOWN' if polygon is None else ''),fill=(0,95,30,255))
            for ray in space['boundary_rays']:
                if ray['kind']=='UNKNOWN_BEYOND_SEARCH_OR_IMAGE':
                    direction=np.array([math.cos(ray['angle_rad']),math.sin(ray['angle_rad'])]);q=np.array(point)+direction*28
                    g.line([tuple(point),tuple(q)],fill=(230,135,0,150),width=1)
    for text in [e for e in elements if e['type']=='TextRegion']:
        polygon=text['geometry']['polygon_native'];g.line([tuple(p) for p in polygon+[polygon[0]]],fill=(185,45,160,200),width=1)
    grounded.save(directory/'space_grounding_overlay.png')
    relation_view=grounded.copy();d=ImageDraw.Draw(relation_view);by_id={e['id']:e for e in elements+spaces}
    def center(e):
        point=e['geometry'].get('seed_native')
        if point:return point
        box=e['geometry'].get('bbox_xyxy');return [(box[0]+box[2])/2,(box[1]+box[3])/2] if box else None
    for relation in relations:
        if relation['predicate'] not in ['labels','connects']:continue
        a=center(by_id[relation['subject_id']])
        for identity in relation['object_ids']:
            b=center(by_id[identity])
            if a and b:d.line([tuple(a),tuple(b)],fill='#925ac7',width=2)
    relation_view.save(directory/'relation_overlay.png')
    crops=[]
    for space in spaces:
        if not any(normalized(n['text']) in QUERIES for n in space['name_hypotheses']):continue
        point=space['geometry']['seed_native']
        if point is None:continue
        extent=space['geometry'].get('bbox_xyxy') or [point[0]-150,point[1]-150,point[0]+150,point[1]+150]
        box=[max(0,math.floor(extent[0]-45)),max(0,math.floor(extent[1]-45)),min(image.width,math.ceil(extent[2]+45)),min(image.height,math.ceil(extent[3]+45))]
        filename='space_'+space['id'].split(':')[-1]+'.png';grounded.crop(box).save(directory/filename)
        crops.append({'id':space['id'],'labels':[n['text'] for n in space['name_hypotheses']],'path':filename,'native_bbox_xyxy':box,
            'purpose':'display context only; not generated user ROI or space polygon'})
    # Existing own-role VLM crop extents are display contexts only. They never
    # enter extraction, which has already completed before this function.
    if vlm_root:
        state=json.loads((vlm_root/'status.json').read_text()) if (vlm_root/'status.json').is_file() else {}
        model=next(iter(state.get('models',{}).values()),None)
        if model:
            run_path=Path(model['spatial_output'])/'run.json'
            if run_path.exists():
                run=json.loads(run_path.read_text())
                for attempt in run['attempts']:
                    v=attempt['view']
                    if v['role']!=observation['role'] or v['id'] not in ['neighborhood','local_01','local_02','local_03']:continue
                    box=v['native_bbox_xyxy'];filename='context_'+v['id']+'.png';grounded.crop(box).save(directory/filename)
                    original='input_'+v['id']+'.png';image.crop(box).save(directory/original)
                    crops.append({'id':v['id'],'path':filename,'original_path':original,'native_bbox_xyxy':box,
                        'purpose':'existing own-role observation context; not ROI, Ref mapping or semantic location assertion'})
    return crops


def render_review(out,drawings,comparisons,crops):
    escape=lambda value:html.escape(str(value))
    parts=['<!doctype html><html lang="ko"><meta charset="utf-8"><title>Space Grounding Engine PoC 1</title>',
        '<style>body{font:16px/1.6 sans-serif;background:#f1f3f6;color:#152331;margin:24px}section{padding:20px;margin:20px 0;background:white}img{max-width:100%;height:auto}pre{white-space:pre-wrap;overflow-wrap:anywhere}table{border-collapse:collapse;width:100%}td,th{padding:8px;border:1px solid #ccd}.notice{background:#fff0bd;padding:16px}.pair{display:grid;grid-template-columns:1fr 1fr;gap:12px}.unknown{color:#a75500}</style>',
        '<h1>FPUE Space Grounding Engine · PoC 1</h1>',
        '<p class="notice"><b>건축 해석은 PROPOSED · 근거가 있는 개별 Geometry Claim만 SUPPORTED · GT 없음</b><br>빨강: 물리적 벽 후보 band / 파랑: Door 후보 / 옅은 파랑 ARC: 원시 곡선, UNKNOWN HOST는 호스트 미확정 / 청록: Window 가설 / 황색: 미확정 Opening·논리 경계·UNKNOWN 방향 / 보라: OCR TextRegion·관계 표시 / 초록 점: 내부 Seed / 초록 영역: 조건부 기하 폐쇄 영역. 녹색 영역도 VERIFIED 공간이 아닙니다. 보라 관계선은 표시에 불과하며 물리적 선이 아닙니다. OCR 박스를 공간 경계로 사용하지 않았습니다. 원본 도면은 별도 보존했습니다.</p>',
        '<p>Ref/Target 독립 실행 · VLM 재추론 0회 · 정렬/Ref ROI 매핑/변경 탐지 없음 · px→mm 환산 없음 · P/R/F1 없음</p>',
        '<p><a href="geometry_observations.json">Geometry 관찰</a> · <a href="space_candidates.json">공간 후보</a> · <a href="element_candidates.json">건축 요소·문자 후보</a> · <a href="relation_candidates.json">관계와 보류</a> · <a href="understanding_bundle.json">부분 Understanding Bundle</a> · <a href="vlm_geometry_comparison.json">기존 VLM 비교·실패</a> · <a href="run.json">실행 상태</a></p>']
    for role,data in drawings.items():
        obs=data['observation'];spaces=data['spaces'];elements=data['elements'];relations=data['relations'];comp=comparisons[role]
        counts=Counter(e['type'] for e in elements);partial=sum(s['geometry']['polygon_native'] is None for s in spaces)
        parts += [f'<section><h2>{role} · Page {obs["source_binding"]["page"]}</h2><p>frame: {escape(obs["frame_id"])}</p>',
            f'<p>PDF Path {obs["pdf_vector_inspection"]["path_count"]} · 후보 {escape(dict(counts))} · Space {len(spaces)} (부분/UNKNOWN {partial}) · 관계 {len(relations)}</p>',
            f'<p>PDF 재추출 pixel 차이: {obs["frame_registry"]["rerender_pixel_differences"]}. 이는 좌표 검증이며 벽 의미 검증이 아닙니다. Target 레이어/텍스트 소실과 모든 solid dash array의 한계는 JSON에 기록했습니다.</p>',
            f'<details><summary>전체 원본 및 실제 물리 기하 후보</summary><div class="pair"><img src="{role}/source_page.png" alt="원본 복사"><img src="{role}/geometry_overlay.png" alt="실제 검출 기하 후보"></div></details>',
            f'<h3>전체 도면 · 내부 Seed와 부분 경계</h3><img src="{role}/space_grounding_overlay.png" alt="원본 좌표의 실제 Geometry 결과">',
            f'<p><a href="{role}/relation_overlay.png">관계 표시</a> · <a href="{role}/physical_barriers.png">물리 후보 마스크</a> · <a href="{role}/logical_boundaries.png">별도 논리 경계</a> · <a href="{role}/morphology_diagnostic.png">Morphology 손실 진단</a> · <a href="{role}/line_exclusion_examples.png">문자·레이어 제외 실제 사례</a> · <a href="{role}/weak_pair_examples.png">약한 평행선·짧은 band 보류 사례</a></p>',
            '<h3>요청한 공간의 관찰 결과</h3><pre>'+escape(json.dumps(obs['query_observation_attempts'],ensure_ascii=False,indent=2))+'</pre>',
            '<table><tr><th>Space / 명칭 가설</th><th>Seed · 경계 상태</th><th>원본 영역 / 부분 경계</th><th>불확실성</th></tr>']
        for s in spaces:
            names=[n['text'] for n in s['name_hypotheses']]
            parts.append(f'<tr><td>{escape(s["id"])}<br>{escape(names or "UNLABELED")}</td><td>{escape(s["geometry"]["seed_native"])}<br>{escape(s["geometry"]["region_status"])}</td><td>{escape(s["geometry"]["bbox_xyxy"])}<br>부분 경계 {len(s["geometry"]["partial_physical_boundary_segments"])}개 · PROPOSED</td><td class="unknown">{escape(s["uncertainties"])}</td></tr>')
        parts+=['</table>']
        for crop in crops[role]:
            parts.append(f'<details><summary>{escape(crop["id"])} · {escape(crop.get("labels","기존 관찰 문맥"))} · 원본 {escape(crop["native_bbox_xyxy"])}</summary><p>{escape(crop["purpose"])}</p>')
            if crop.get('original_path'):parts.append(f'<div class="pair"><img loading="lazy" src="{role}/{crop["original_path"]}" alt="실제 원본 crop"><img loading="lazy" src="{role}/{crop["path"]}" alt="실제 후보 crop"></div>')
            else:parts.append(f'<img loading="lazy" src="{role}/{crop["path"]}" alt="실제 공간 후보 및 UNKNOWN">')
            parts.append('</details>')
        parts+=['<details><summary>벽·문·개구부·창문·기호 후보 ID와 상태</summary><table><tr><th>ID / 유형</th><th>원본 bbox</th><th>Claim 및 불확실성</th></tr>']
        for e in elements:
            if e['type']=='TextRegion':continue
            parts.append(f'<tr><td>{escape(e["id"])}<br>{escape(e["type"])}</td><td>{escape(e["geometry"].get("bbox_xyxy"))}</td><td>PROPOSED<br>{escape(e["uncertainties"])}</td></tr>')
        parts+=['</table></details>', '<details><summary>실제 관계 후보와 연결 보류 이유</summary><pre>'+escape(json.dumps({'relations':relations,'attempts':obs['relation_attempts']},ensure_ascii=False,indent=2))+'</pre></details>',
            '<details><summary>기존 VLM 비교 · 원문 실패와 잘못된 유형 관계 보존</summary><pre>'+escape(json.dumps(comp,ensure_ascii=False,indent=2))+'</pre></details>',
            '<details><summary>문자·치수선 및 약한 평행선의 제외 사례</summary><pre>'+escape(json.dumps({'lines':obs['pdf_rejected_observations'],'pairs':obs['wall_pair_rejections']},ensure_ascii=False,indent=2))+'</pre></details></section>']
    parts.append('</html>');(out/'review.html').write_text('\n'.join(parts),encoding='utf-8')


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--inputs',type=Path,default=ROOT/'poc/results/page4_inputs')
    parser.add_argument('--ocr',type=Path,default=ROOT/'poc/results/page4_ocr');parser.add_argument('--vlm-results',type=Path,default=ROOT/'poc/results/qwen_recovery_2026-11-13')
    parser.add_argument('--out',type=Path,default=ROOT/'poc/results/space_grounding_page4');args=parser.parse_args()
    if args.out.exists():raise SystemExit('Existing output preserved; choose a new --out directory')
    inputs,manifest=load_inputs(args.inputs);roi_context=None
    target=manifest['drawings'].get('Target',{})
    if target.get('user_roi'):
        roi_path=inputs/target['user_roi']['path']
        if sha(roi_path)!=target['user_roi']['sha256']:raise ValueError('Preserved ROI hash mismatch')
        roi=json.loads(roi_path.read_text());validate_roi(roi,target['binding'])
        roi_context={'path':str(roi_path),'sha256':sha(roi_path),'frame_binding_valid':True,
            'policy':'preserved user ROI only; whole-page understanding observations may lie outside; no Ref mapping or geometry boundary inference from ROI'}
    args.out.mkdir(parents=True,exist_ok=False);settings=Settings()
    run={'schema_version':'space_grounding_poc/1','started_utc':datetime.now(timezone.utc).isoformat(),'status':'RUNNING',
        'input_manifest':str(args.inputs/'manifest.json'),'input_manifest_sha256':sha(args.inputs/'manifest.json'),
        'code_sha256':{Path(p).name:sha(p) for p in [__file__,Path(__file__).with_name('space_grounding_geometry.py')]},
        'config':asdict(settings),'versions':{'python':sys.version,'pymupdf':pymupdf.VersionBind,'opencv':cv2.__version__,'numpy':np.__version__},
        'vlm_model_calls':0,'independent_drawings':True,'architectural_gt':None,'metrics':None,
        'user_roi_context':roi_context,
        'claim_policy':'PROPOSED architectural interpretations; narrow geometry facts SUPPORTED; only mechanical coordinate checks VERIFIED',
        'observed_failures':[]}
    write_json(args.out/'run.json',run);drawings={};comparisons={};crops={}
    try:
        for role,drawing in manifest['drawings'].items():
            observation,spaces,elements,relations,debug=analyze_drawing(inputs,args.ocr,role,drawing['binding'],settings)
            comparison=compare_vlm(args.vlm_results,role,spaces,elements,debug['barriers'])
            drawings[role]={'observation':observation,'spaces':spaces,'elements':elements,'relations':relations};comparisons[role]=comparison
            crops[role]=render_drawing(args.out/role,observation,spaces,elements,relations,debug,comparison,args.vlm_results)
            print(role,dict(Counter(e['type'] for e in elements)),'Spaces',len(spaces),'closed',sum(s['geometry']['polygon_native'] is not None for s in spaces),'seconds',round(observation['runtime_seconds'],2),flush=True)
            del debug
        bindings={role:d['observation']['source_binding'] for role,d in drawings.items()}
        common={'schema_version':'space_grounding/1','source_bindings':bindings,'coordinate_system':'image_native_pixels','architectural_gt':None,'metrics':None}
        write_json(args.out/'geometry_observations.json',{**common,'drawings':{role:d['observation'] for role,d in drawings.items()}})
        write_json(args.out/'space_candidates.json',{**common,'drawings':{role:d['spaces'] for role,d in drawings.items()}})
        write_json(args.out/'element_candidates.json',{**common,'drawings':{role:d['elements'] for role,d in drawings.items()}})
        write_json(args.out/'relation_candidates.json',{**common,'drawings':{role:{'candidates':d['relations'],'attempts':d['observation']['relation_attempts']} for role,d in drawings.items()}})
        write_json(args.out/'vlm_geometry_comparison.json',{**common,'drawings':comparisons,'model_calls':0})
        bundle={**common,'run_config':{k:v for k,v in run.items() if k not in ['status','observed_failures']},'frame_registry':{role:d['observation']['frame_registry'] for role,d in drawings.items()},
            'entities':[e for d in drawings.values() for e in d['elements']+d['spaces']],
            'relations':[r for d in drawings.values() for r in d['relations']],
            'claims':[c for d in drawings.values() for e in d['elements']+d['spaces'] for c in e['claims']],
            'observation_artifact':'geometry_observations.json','vlm_comparison_artifact':'vlm_geometry_comparison.json',
            'coverage':{'mode':'whole-page geometry, requested OCR labels and unlabeled enclosed components','display_crops':crops},
            'unresolved_issues':[{'role':role,'space_id':s['id'],'issues':s['uncertainties']} for role,d in drawings.items() for s in d['spaces'] if s['uncertainties']],
            'completion':'PARTIAL_UNDERSTANDING_NOT_AUTOMATIC_ALIGNMENT_OR_CHANGE_DETECTION'}
        write_json(args.out/'understanding_bundle.json',bundle);render_review(args.out,drawings,comparisons,crops)
        run.update(status='COMPLETE_WITH_PARTIAL_GROUNDING',completed_utc=datetime.now(timezone.utc).isoformat(),
            summary={role:{'entities':dict(Counter(e['type'] for e in d['elements'])),'spaces':len(d['spaces']),
                'closed_space_hypotheses':sum(s['geometry']['polygon_native'] is not None for s in d['spaces']),
                'relations':dict(Counter(r['predicate'] for r in d['relations'])),'queries':d['observation']['query_observation_attempts']} for role,d in drawings.items()})
    except Exception as exc:
        run.update(status='FAILED',error=str(exc),traceback=traceback.format_exc());raise
    finally:write_json(args.out/'run.json',run)


if __name__=='__main__':main()
