"""Read-only PoC 1 reuse + independent Geometry/Topology PoC 2 artifacts."""
import argparse
from collections import Counter
import copy
import csv
from dataclasses import asdict
from datetime import datetime,timezone
from io import BytesIO
import json
from pathlib import Path
import shutil
import sys
import time
import traceback

import cv2
import numpy as np
from PIL import Image,ImageDraw
import pymupdf

import space_grounding as sg
from space_grounding_geometry import (Settings,axis_points,barrier_masks,pdf_to_native_matrix,
    raster_observations,vector_observations)
from geometry_refinement import (RefinementSettings,reconstruct_bands,visible_raw_lines,
    opening_grounding,topology)
from run import load_inputs,validate_roi
from phase1_pdf import render_page

ROOT=Path(__file__).resolve().parents[1]
P1=ROOT/'poc/results/space_grounding_page4'


def baseline_links(band,baseline_elements):
    links=[]
    for entity in baseline_elements:
        if entity['type']!='Wall':continue
        for piece in entity['geometry'].get('physical_pieces',[]):
            if piece['axis']!=band['axis']:continue
            shared=set(piece.get('boundary_ids',[]))&set(band['boundary_ids'])
            axial_overlap=min(piece['end'],band['end'])-max(piece['start'],band['start'])
            normal_overlap=min(piece['normal']+piece['thickness_px']/2,band['n2'])-max(piece['normal']-piece['thickness_px']/2,band['n1'])
            if axial_overlap>1 and (shared or normal_overlap>0):
                links.append({'baseline_candidate_id':entity['id'],'basis':'SHARED_SOURCE_BOUNDARY' if shared else 'SAME_FRAME_GEOMETRIC_OVERLAP',
                    'shared_source_boundary_ids':sorted(shared),'not_physical_identity_or_change_correspondence':True});break
    return links


def opening_baseline_links(opening,baseline_elements):
    links=[]
    for entity in baseline_elements:
        if entity['type'] not in ['Door','Window','Opening']:continue
        geometry=entity['geometry'];points=geometry.get('logical_boundary_segment_native')
        if not points:continue
        from space_grounding_geometry import axis_segment
        segment=axis_segment(points)
        if not segment or segment[0]!=opening['axis']:continue
        _,n,a,b=segment;overlap=min(b,opening['end'])-max(a,opening['start'])
        if abs(n-opening['normal'])<max(1.5,opening['host_thickness_px']*.35) and overlap>.6*min(b-a,opening['width_px']):
            links.append({'baseline_candidate_id':entity['id'],'basis':'SAME_FRAME_OPENING_INTERVAL_OVERLAP',
                'not_physical_identity_or_change_correspondence':True})
    return links


def refine_role(role,binding,baseline_observation,baseline_elements,baseline_spaces,inputs,settings):
    tick=time.perf_counter();binding={**binding,'role':role};image_path=inputs/role/'page.png';source=Path(binding['source_path'])
    if sg.sha(source)!=binding['source_sha256'] or sg.sha(image_path)!=binding['image_sha256']:raise ValueError('Source PDF or original raster changed')
    image=Image.open(image_path).convert('RGB');rendered=render_page(source.read_bytes(),binding['page'],binding['extraction_zoom'])
    if not np.array_equal(np.asarray(image),np.asarray(Image.open(BytesIO(rendered.png)).convert('RGB'))):raise ValueError('Native raster does not match PDF render')
    ocr_path=ROOT/'poc/results/page4_ocr'/role/'observations.json';ocr=json.loads(ocr_path.read_text())
    for text in ocr:
        if text['frame_id']!=binding['frame_id']:raise ValueError('OCR frame mismatch')
        text['entity_id']=f'{role}:TextRegion:{text["id"].split(":")[-1]}'
    height=baseline_observation['reference_text_height_px'];raster=raster_observations(np.asarray(image.convert('L')),[o['polygon_native'] for o in ocr],height,Settings())
    lines=copy.deepcopy(baseline_observation['pdf_line_observations'])
    bands,singles,rejected,distribution=reconstruct_bands(lines,raster,baseline_observation['parallel_boundary_observations'],height,role,settings,
        ocr=ocr,rectangles=baseline_observation['rectangle_observations'])
    with pymupdf.open(source) as doc:
        page=doc[binding['page']-1];matrix=pdf_to_native_matrix(page,binding['extraction_zoom']);vector=vector_observations(page,matrix)
    raw=visible_raw_lines(vector['segments'],raster,height,role,settings)
    arcs=copy.deepcopy(baseline_observation['arc_observations'])
    openings,arc_attempts=opening_grounding(bands,lines,arcs,raw,raster,height,role,settings)
    graph=topology(bands,openings,raster,role,settings)
    walls=[];wall_candidates=[];segments=[];elements=copy.deepcopy([e for e in baseline_elements if e['type'] in ['TextRegion','Symbol']])
    for entity in elements:entity['related_entity_ids']=[]
    for band in bands:
        identity=band['id'].replace('Segment2','Wall2');band['wall_id']=identity
        evidence=baseline_links(band,baseline_elements)
        geometry={'polygons_native':[band['polygon']],'bbox_xyxy':sg.bounds(band['polygon']),
            'center_path_native':axis_points(band['axis'],band['normal'],band['start'],band['end']),
            'boundary_segments_native':[axis_points(band['axis'],n,band['start'],band['end']) for n in [band['n1'],band['n2']]],
            'local_thickness_px':band['thickness_px'],'observed_interval_native':[band['start'],band['end']],
            'physical_endpoint_status':'UNKNOWN','analysis_cut':band['arbitration_cut'],
            'space_barrier_eligible':band['space_barrier_eligible'],'coordinate_system':'image_native_pixels'}
        record={'id':identity,'segment_ids':[band['id']],'role':role,'frame_id':binding['frame_id'],'source_binding':binding,
            'geometry':geometry,'classification':band['classification'],'is_thin_hypothesis':band['is_thin_hypothesis'],
            'status':'PROPOSED','geometry_status':'SUPPORTED','baseline_links':evidence,'evidence':band,
            'algorithm':settings.version,'uncertainties':band['uncertainties']}
        wall_candidates.append(record)
        segments.append({**band,'role':role,'frame_id':binding['frame_id'],'source_binding':binding,'geometry':geometry,'baseline_links':evidence})
        if band['space_barrier_eligible']:
            piece={'axis':band['axis'],'normal':band['normal'],'start':band['start'],'end':band['end'],
                'thickness_px':band['thickness_px'],'polygon':band['polygon'],'observation_id':band['id'],
                'boundary_ids':band['boundary_ids'],'endpoint_semantics':band['endpoints_semantics']}
            walls.append({'id':identity,'axis':band['axis'],'normal':band['normal'],'thickness_px':band['thickness_px'],'pieces':[piece]})
            entity=sg.entity(identity,'Wall',binding,geometry,[band['id']],settings.version,band['uncertainties'])
            entity['baseline_links']=evidence;elements.append(entity)
    for single in singles:
        record={**single,'role':role,'frame_id':binding['frame_id'],'source_binding':binding,
            'geometry':{'center_path_native':single['points_native'],'local_thickness_px':None,'coordinate_system':'image_native_pixels'},
            'baseline_links':[],'uncertainties':['Single line cannot establish wall thickness, solid geometry or room enclosure.']}
        wall_candidates.append(record);segments.append(record)
    band_lookup={b['id']:b for b in bands};opening_adapters=[]
    for opening in openings:
        aligned_hosts=[s for s in opening['host_segment_ids'] if band_lookup[s]['axis']==opening['axis']]
        opening.update(role=role,frame_id=binding['frame_id'],source_binding=binding,
            opening_id=opening['id'],bracket_segment_ids=list(opening['host_segment_ids']),hosting_segment_ids=aligned_hosts,
            host_wall_ids=[band_lookup[s]['wall_id'] for s in aligned_hosts] if opening['gap_status']!='REJECTED_SOLID_OVERLAP' else [],
            proposed_host_wall_ids=[band_lookup[s]['wall_id'] for s in aligned_hosts],physical_opening_status='PROPOSED_NOT_VERIFIED',
            orientation_hypothesis=opening['axis'],geometry_evidence=opening['door_proposals'] or {'tracks':opening['track_line_ids'],'jambs':opening['bracket_cap_visibility']})
        opening['baseline_links']=opening_baseline_links(opening,baseline_elements)
        geom={'polygon_native':opening['polygon'],'bbox_xyxy':sg.bounds(opening['polygon']),
            'logical_boundary_segment_native':opening['logical_segment'],'opening_width_px':opening['width_px'],
            'opening_width_interval_px':opening.get('width_interval_px',[opening['width_px'],opening['width_px']]),
            'orientation_hypothesis':opening['axis'],'opening_observation_id':opening['id'],
            'logical_boundary_enabled':opening['eligible_logical_boundary']}
        entity=sg.entity(opening['element_id'],opening['kind'],binding,geom,[opening['id']],settings.version,[opening['uncertainty']])
        entity.update(opening_id=opening['id'],host_wall_ids=opening['host_wall_ids'],geometry_evidence=opening['geometry_evidence'],
            baseline_links=opening['baseline_links'],
            entity_semantics='canonical physical opening interpretation; opening_id refers to its observation, not a duplicate physical object')
        elements.append(entity)
        opening_adapters.append({**opening,'entity_id':opening['element_id'],'origin':'POC2_OBSERVED_GAP',
            'arc':opening['door_proposals'][0] if opening['kind']=='Door' else None})
    barriers=barrier_masks(walls,opening_adapters,raster['ink'].shape)
    # Reuse protected PoC 1 seed, partial-boundary and label-conflict safety code.
    spaces,_,queries=sg.build_spaces(ocr,{'rectangles':copy.deepcopy(baseline_observation['rectangle_observations'])},walls,opening_adapters,barriers,raster,height,binding,Settings())
    for entity in spaces:
        old=entity['id'];new=old.replace(':Space:',':Space2:');entity['id']=new
        entity['geometry_evidence_ids']=[s.replace(':free_component:',':free_component2:') for s in entity['geometry_evidence_ids']]
        key=entity['geometry'].get('component_observation_id')
        if key:entity['geometry']['component_observation_id']=key.replace(':free_component:',':free_component2:')
        for c in entity['claims']:
            c['id']=c['id'].replace(old,new);c['subject']=new;c['evidence_ids']=[s.replace(':free_component:',':free_component2:') for s in c['evidence_ids']]
        entity['baseline_links']=[{'baseline_candidate_id':s['id'],'basis':'SAME_OWN_OCR_LABEL_NOT_VERIFIED_ROOM_IDENTITY',
            'not_physical_identity_or_change_correspondence':True} for s in baseline_spaces if set(s['related_text_region_ids'])&set(entity['related_text_region_ids'])]
    for query in queries:query['space_ids']=[identity.replace(':Space:',':Space2:') for identity in query['space_ids']]
    relations,relation_attempts=sg.build_relations(elements,spaces,walls,opening_adapters,barriers,binding)
    for relation in relations:relation['id']=relation['id'].replace(':relation:',':relation2:')
    for opening in openings:
        if opening['gap_status']=='REJECTED_SOLID_OVERLAP':
            relation_attempts.append({'role':role,'frame_id':binding['frame_id'],'subject_id':opening['element_id'],
                'predicate':'hosts','attempt_status':'REJECTED_BY_SOLID_OVERLAP',
                'reason':opening['uncertainty'],'evidence_ids':opening['contradicting_solid_band_ids']})
    for entity in spaces:entity['poc2_boundary_status']='PARTIAL_BOUNDARY' if entity['geometry']['polygon_native'] is None else 'CONDITIONAL_REGION_CANDIDATE'
    before=[]
    for query in sg.QUERIES:
        old=[s for s in baseline_spaces if any(sg.normalized(n['text'])==query for n in s['name_hypotheses'])]
        new=[s for s in spaces if any(sg.normalized(n['text'])==query for n in s['name_hypotheses'])]
        before.append({'query':query,'role':role,'frame_id':binding['frame_id'],
            'poc1':[{'id':s['id'],'region_status':s['geometry']['region_status'],'bbox_native':s['geometry']['bbox_xyxy']} for s in old],
            'poc2':[{'id':s['id'],'region_status':s['poc2_boundary_status'],'bbox_native':s['geometry']['bbox_xyxy'],'seed_native':s['geometry']['seed_native'],'uncertainties':s['uncertainties']} for s in new],
            'name_absence_not_proven':True,'label_source':'own PP-OCRv5 only'})
    for e in graph['edges']+graph['uncertain_connections']:e.update(role=role,frame_id=binding['frame_id'])
    result={'role':role,'frame_id':binding['frame_id'],'source_binding':binding,'frame_registry':baseline_observation['frame_registry'],
        'wall_candidates':wall_candidates,'wall_segments':segments,'openings':openings,'elements':elements,'spaces':spaces,
        'topology':graph,'relations':relations,'relation_attempts':relation_attempts,'space_comparison':before,'queries':queries,
        'thickness_distribution':distribution,'rejected_candidates':rejected,'arc_attachment_attempts':arc_attempts,
        'component_observations':[{'id':f'{role}:free_component2:{i}','role':role,'frame_id':binding['frame_id'],
            'stats_xywh_area':stats.tolist(),'mask_artifact':f'{role}/component_evidence.npz',
            'barrier_band_ids':[b['id'] for b in bands if b['space_barrier_eligible']],
            'logical_opening_ids':[g['id'] for g in openings if g['eligible_logical_boundary']],
            'claim':'conditional free region, not verified architectural room'} for i,stats in enumerate(barriers['component_stats']) if i],
        'coordinate_checks':{'rerender_pixel_differences':0,'native_size':list(image.size)},
        'counts':{'active_wall_bands':len(walls),'all_band_hypotheses':len(bands),'single_line_unknown':len(singles),
            'thin_active_bands':sum(b['is_thin_hypothesis'] and b['space_barrier_eligible'] for b in bands),
            'opening_subtypes':dict(Counter(g['kind'] for g in openings)),'spaces':len(spaces),
            'conditional_regions':sum(s['geometry']['polygon_native'] is not None for s in spaces),
            'relations':dict(Counter(r['predicate'] for r in relations))},
        'metrics':None,'ground_truth':None,'vlm_calls':0,'runtime_seconds':time.perf_counter()-tick}
    debug={'image':image,'raster':raster,'barriers':barriers,'bands':bands,'singles':singles,'walls':walls,'openings':opening_adapters}
    return result,debug


def draw_results(out,role,result,debug,baseline,contexts):
    directory=out/role;directory.mkdir();original=debug['image'];original.save(directory/'original.png')
    shutil.copyfile(baseline/role/'geometry_overlay.png',directory/'poc1_wall_overlay.png')
    variants={name:original.copy() for name in ['poc2_wall_overlay','thin_wall_candidates','opening_overlay','wall_connectivity','uncertain_rejected','space_overlay']}
    painters={key:ImageDraw.Draw(value,'RGBA') for key,value in variants.items()}
    for band in debug['bands']:
        poly=[tuple(p) for p in band['polygon']];active=band['space_barrier_eligible'];d=painters['poc2_wall_overlay']
        if active:d.polygon(poly,fill=(225,50,35,55),outline=(190,30,10,230),width=2)
        if band['is_thin_hypothesis']:
            color=(0,95,205,230) if active else (205,140,0,190);painters['thin_wall_candidates'].line(poly+[poly[0]],fill=color,width=2)
        if not active:painters['uncertain_rejected'].line(poly+[poly[0]],fill=(215,150,0,150),width=1)
    for single in debug['singles']:painters['thin_wall_candidates'].line([tuple(p) for p in single['points_native']],fill=(210,145,0,220),width=2)
    rejected_image=original.copy();rd=ImageDraw.Draw(rejected_image,'RGBA')
    for candidate in result['rejected_candidates']:
        # Separate image keeps the accepted geometry readable; all rejected
        # hypotheses remain accessible in the evidence JSON.
        rd.line([tuple(p) for p in candidate['polygon']+[candidate['polygon'][0]]],fill=(160,35,175,65),width=1)
    rejected_image.save(directory/'rejected_hypotheses.png')
    for g in result['openings']:
        if g['kind']=='Opening' and not(g['track_line_ids'] or g['door_proposals']):continue
        color=(30,90,220,230) if g['kind']=='Door' else (0,140,160,220) if g['kind']=='Window' else (200,130,0,160)
        if g['gap_status']=='REJECTED_SOLID_OVERLAP':color=(160,35,175,150)
        painters['opening_overlay'].line([tuple(p) for p in g['polygon']+[g['polygon'][0]]],fill=color,width=2)
        painters['opening_overlay'].text(tuple(g['logical_segment'][0]),g['id'].split(':')[-1]+' '+g['kind'][0]+'/P',fill=color)
        for proof in g['door_proposals']:
            painters['opening_overlay'].line([tuple(p) for p in proof['swing_polyline_native']],fill=(35,90,190,180),width=1)
    variants['wall_connectivity']=variants['poc2_wall_overlay'].copy();td=ImageDraw.Draw(variants['wall_connectivity'])
    for edge in result['topology']['edges']+result['topology']['uncertain_connections']:
        if edge['type']!='WALL_JUNCTION':continue
        x,y=edge['point_native'];color='#288b42' if edge['status']=='SUPPORTED' else '#b78c28';td.ellipse((x-4,y-4,x+4,y+4),outline=color,width=2);td.text((x+4,y),edge['junction_type'],fill=color)
    variants['space_overlay']=variants['poc2_wall_overlay'].copy();sd=ImageDraw.Draw(variants['space_overlay'],'RGBA')
    for space in result['spaces']:
        geometry=space['geometry'];poly=geometry['polygon_native']
        if poly:
            for ring in poly['outer_rings']:sd.polygon([tuple(p) for p in ring],fill=(20,175,70,45),outline=(20,120,55,230),width=2)
        else:
            for line in geometry['partial_physical_boundary_segments']:sd.line([tuple(p) for p in line['points_native']],fill=(210,130,0,230),width=3)
        p=geometry['seed_native']
        if p:
            x,y=p;sd.ellipse((x-4,y-4,x+4,y+4),fill=(20,175,65,255),outline='black');sd.text((x+5,y),space['id'].split(':')[-1]+(' PARTIAL' if not poly else ' CONDITIONAL'),fill=(20,100,30,255))
    for name,image in variants.items():image.save(directory/(name+'.png'))
    for name,mask in [('physical_barriers',debug['barriers']['physical']),('logical_separators',debug['barriers']['logical'])]:Image.fromarray(mask).save(directory/(name+'.png'))
    np.savez_compressed(directory/'component_evidence.npz',labels=debug['barriers']['components'])
    old_space=Image.open(baseline/role/'space_grounding_overlay.png').convert('RGB');combo=Image.new('RGB',(original.width*2,original.height),'white');combo.paste(old_space,(0,0));combo.paste(variants['space_overlay'],(original.width,0));combo.save(directory/'space_boundary_comparison.png')
    crops=[]
    for context in contexts:
        if context['id'] not in ['neighborhood','local_01','local_02','local_03']:continue
        box=context['native_bbox_xyxy'];name=context['id']
        for stage,image in [('original',original),('poc1_wall',Image.open(baseline/role/'geometry_overlay.png')),('poc2_wall',variants['poc2_wall_overlay']),('thin',variants['thin_wall_candidates']),('openings',variants['opening_overlay']),('topology',variants['wall_connectivity']),('poc1_space',old_space),('poc2_space',variants['space_overlay']),('uncertain',variants['uncertain_rejected'])]:image.crop(box).save(directory/(name+'_'+stage+'.png'))
        crops.append({'id':name,'role':role,'native_bbox_xyxy':box,'purpose':'same own-role PoC 1 display context; not user ROI, mapped Ref ROI or semantic assertion'})
    return crops


def gt_templates(out,drawings):
    records=[]
    def row(role,binding,kind,identity,geometry,baseline_ids):
        records.append({'review_id':f'{role}:review:{len(records):05d}','role':role,'frame_id':binding['frame_id'],
            'source_binding':binding,'review_kind':kind,'candidate_id':identity,'poc1_candidate_ids':baseline_ids,
            'proposed_geometry_native':geometry,'prediction_is_not_gt':True,'review_status':'UNREVIEWED',
            'gt_is_wall':None,'gt_is_opening':None,'gt_opening_kind':None,'gt_host_wall_ids':None,
            'gt_space_boundary_native':None,'manual_geometry_native':None,'gt_notes':None,'reviewer':None,'reviewed_at':None})
    for role,data in drawings.items():
        binding=data['source_binding']
        for candidate in data['wall_candidates']:row(role,binding,'WALL_OR_UNKNOWN_PARTITION',candidate['id'],candidate['geometry'],[x['baseline_candidate_id'] for x in candidate.get('baseline_links',[])])
        for opening in data['openings']:row(role,binding,'OPENING_HOST_OR_SUBTYPE',opening['id'],{'polygon_native':opening['polygon'],'host_wall_ids':opening['host_wall_ids'],'proposed_kind':opening['kind']},[x['baseline_candidate_id'] for x in opening.get('baseline_links',[])])
        for space in data['spaces']:row(role,binding,'SPACE_BOUNDARY',space['id'],space['geometry'],[x['baseline_candidate_id'] for x in space.get('baseline_links',[])])
        for i in range(6):row(role,binding,'MISSING_GEOMETRY_BLANK_SLOT','',None,[])
    sg.write_json(out/'gt_review_template.json',{'schema_version':'human_grounding_review/1','warning':'All GT values are null. Predictions and manual draft points are not automatically ground truth.','records':records})
    columns=['review_id','role','frame_id','source_sha256','page','candidate_id','poc1_candidate_ids','review_kind','proposed_geometry_native','review_status','gt_is_wall','gt_is_opening','gt_opening_kind','gt_host_wall_ids','gt_space_boundary_native','manual_geometry_native','gt_notes','reviewer','reviewed_at']
    with (out/'gt_review_template.csv').open('w',encoding='utf-8-sig',newline='') as file:
        writer=csv.DictWriter(file,fieldnames=columns);writer.writeheader()
        for r in records:
            d={k:r.get(k) for k in columns};d.update(source_sha256=r['source_binding']['source_sha256'],page=r['source_binding']['page'])
            for k in ['poc1_candidate_ids','proposed_geometry_native']:
                d[k]=json.dumps(d[k],ensure_ascii=False) if d[k] is not None else ''
            writer.writerow(d)
    return records


def svg_layer(role,data):
    info={};shapes=[]
    for record in data['wall_candidates']:
        identity=record['id'];info[identity]=record;geometry=record['geometry'];polys=geometry.get('polygons_native',[])
        color='#d43322' if record.get('evidence',{}).get('space_barrier_eligible') else '#bf9016'
        for p in polys:shapes.append(f'<polygon data-id="{identity}" points="'+ ' '.join(f'{x:.3f},{y:.3f}' for x,y in p)+f'" fill="{color}" fill-opacity=".23" stroke="{color}" stroke-width="2"><title>{identity}</title></polygon>')
        if not polys:
            points=geometry.get('center_path_native',[])
            if points:shapes.append(f'<polyline data-id="{identity}" points="'+ ' '.join(f'{x:.3f},{y:.3f}' for x,y in points)+f'" fill="none" stroke="#b58a1b" stroke-width="3"><title>{identity} UNKNOWN</title></polyline>')
    for opening in data['openings']:
        identity=opening['id'];info[identity]=opening
        if not(opening['door_proposals'] or opening['track_line_ids']):continue
        color='#235ed3' if opening['kind']=='Door' else '#008e9e' if opening['kind']=='Window' else '#ba8615'
        if opening['gap_status']=='REJECTED_SOLID_OVERLAP':color='#a023af'
        shapes.append(f'<polygon data-id="{identity}" points="'+' '.join(f'{x:.3f},{y:.3f}' for x,y in opening['polygon'])+f'" fill="{color}" fill-opacity=".15" stroke="{color}" stroke-width="3"><title>{identity} {opening["kind"]} PROPOSED</title></polygon>')
    for space in data['spaces']:
        identity=space['id'];info[identity]=space;seed=space['geometry']['seed_native']
        if seed:
            x,y=seed;shapes.append(f'<circle data-id="{identity}" cx="{x}" cy="{y}" r="6" fill="#149044" stroke="white" stroke-width="1"><title>{identity} seed PROPOSED</title></circle>')
    binding=data['source_binding']
    svg=f'<svg id="viewer-{role}" data-role="{role}" viewBox="0 0 {binding["width"]} {binding["height"]}" xmlns="http://www.w3.org/2000/svg"><image href="{role}/original.png" width="{binding["width"]}" height="{binding["height"]}"/>'+''.join(shapes)+'<g class="manual-layer"></g></svg>'
    return svg,info


def render_review(out,drawings,crops,records):
    import html
    e=lambda x:html.escape(str(x));payload={'records':{},'bindings':{r:d['source_binding'] for r,d in drawings.items()}}
    parts=['<!doctype html><html lang="ko"><meta charset="utf-8"><title>Geometry & Topology Refinement PoC 2</title>',
        '<style>body{font:16px/1.6 sans-serif;margin:24px;background:#f1f3f6;color:#152331}section{background:white;padding:18px;margin:20px 0}.notice{background:#fff0ba;padding:16px}img{max-width:100%}.pair{display:grid;grid-template-columns:1fr 1fr;gap:12px}.triple{display:grid;grid-template-columns:1fr 1fr 1fr;gap:10px}svg{width:100%;max-height:80vh;background:white;touch-action:none}pre{white-space:pre-wrap;overflow-wrap:anywhere;max-height:38em;overflow:auto}table{border-collapse:collapse;width:100%}td,th{border:1px solid #ccc;padding:8px}.manual-layer{pointer-events:none}button,select{padding:8px;margin:4px}</style>',
        '<h1>FPUE Geometry & Topology Refinement · PoC 2</h1>',
        '<p class="notice"><b>모든 건축 유형·공간 해석은 PROPOSED. 개별 가시성·접촉만 SUPPORTED. 건축 GT·P/R/F1 없음.</b><br>빨강은 Wall band 후보, 황색은 UNKNOWN·frame·단일선 후보, 파랑은 Door 후보, 청록은 Window/track 가설, 보라는 Solid와 겹쳐 기각한 Opening 가설, 녹색 점은 공간 Seed입니다. 후보 수 증가는 성능 개선이 아닙니다. Ref/Target은 독립 분석이며 도면 간 변경 판정은 하지 않습니다. 과거 수동 검토 메모도 GT가 아닙니다.</p>',
        '<p><a href="wall_candidates.json">Wall 후보</a> · <a href="wall_segments.json">가시 분절</a> · <a href="opening_candidates.json">Opening</a> · <a href="element_candidates.json">Door/Window 등 요소</a> · <a href="wall_topology.json">Topology</a> · <a href="space_boundary_review.json">Space 재평가</a> · <a href="understanding_bundle.json">부분 Bundle</a> · <a href="gt_review_template.csv">빈 GT 검토 CSV</a> · <a href="gt_review_template.json">빈 GT 검토 JSON</a></p>']
    for role,data in drawings.items():
        svg,info=svg_layer(role,data);payload['records'].update(info)
        parts += [f'<section id="{role}"><h2>{role} · 원본 좌표와 Evidence 확인</h2><p>{e(data["frame_id"])}</p>',
            '<p>후보를 클릭하면 원본 좌표·근거·PoC 1 ID 연결을 확인합니다. “검토 선분 찍기”는 사람의 검토 초안일 뿐 GT를 자동 확정하지 않습니다.</p>',
            f'<button class="mode" data-role="{role}" data-mode="inspect">후보 확인</button><button class="mode" data-role="{role}" data-mode="mark">검토 선분 찍기</button><button class="clear" data-role="{role}">검토 초안 초기화</button>',svg,
            f'<pre id="details-{role}">후보 클릭 또는 원본 위치 두 점을 선택하세요. 모든 gt_* 값은 null입니다.</pre>',
            f'<p>관찰 수: {e(data["counts"])}</p><details><summary>기하 가시 구간·끝점·두께 분포·Topological 근거</summary><pre>{e(json.dumps({"distribution":data["thickness_distribution"],"uncertain_connections":data["topology"]["uncertain_connections"]},ensure_ascii=False,indent=2))}</pre></details>']
        for crop in crops[role]:
            name=crop['id'];parts += [f'<h3>{e(name)} · 동일 원본 확대 영역 {e(crop["native_bbox_xyxy"])}</h3>',
                f'<div class="triple"><figure><figcaption>Original</figcaption><img src="{role}/{name}_original.png"></figure><figure><figcaption>PoC 1 Wall</figcaption><img src="{role}/{name}_poc1_wall.png"></figure><figure><figcaption>PoC 2 Wall</figcaption><img src="{role}/{name}_poc2_wall.png"></figure></div>',
                f'<details><summary>Thin Wall / Opening / Topology / 미확정 비교</summary><div class="pair"><img loading="lazy" src="{role}/{name}_thin.png"><img loading="lazy" src="{role}/{name}_openings.png"><img loading="lazy" src="{role}/{name}_topology.png"><img loading="lazy" src="{role}/{name}_uncertain.png"></div></details>',
                f'<details><summary>Space Boundary: PoC 1 / PoC 2</summary><div class="pair"><img loading="lazy" src="{role}/{name}_poc1_space.png"><img loading="lazy" src="{role}/{name}_poc2_space.png"></div></details>']
        parts += ['<h3>침실1·드레스룸·팬트리의 독립 재평가</h3><pre>'+e(json.dumps(data['space_comparison'],ensure_ascii=False,indent=2))+'</pre>',
            f'<p><a href="{role}/poc2_wall_overlay.png">전체 Wall</a> · <a href="{role}/thin_wall_candidates.png">전체 Thin/단일선</a> · <a href="{role}/opening_overlay.png">전체 Opening</a> · <a href="{role}/wall_connectivity.png">전체 연결성</a> · <a href="{role}/uncertain_rejected.png">UNKNOWN/보류</a> · <a href="{role}/rejected_hypotheses.png">제외된 대안</a></p></section>']
    parts += ['<section><h2>사람의 검토 초안</h2><p>CSV/JSON의 gt_*는 모두 비워 두었습니다. 후보가 누락된 곳은 검토 선분으로 좌표를 기록한 뒤 사람이 유형과 Host를 검토하세요. 아래 다운로드도 GT 확정본이 아닙니다.</p><button id="export-manual">검토 초안 JSON 다운로드</button><pre id="manual-status">검토 초안 0개 · GT 미확정</pre></section>']
    script='''
const data=__PAYLOAD__,modes={Ref:'inspect',Target:'inspect'},pending={},drafts=[];
function details(role,value){document.querySelector('#details-'+role).textContent=JSON.stringify(value,null,2)}
document.querySelectorAll('.mode').forEach(b=>b.onclick=()=>{modes[b.dataset.role]=b.dataset.mode;delete pending[b.dataset.role];details(b.dataset.role,{mode:b.dataset.mode,frame:data.bindings[b.dataset.role].frame_id,gt_status:'UNREVIEWED'})});
document.querySelectorAll('svg[data-role]').forEach(svg=>svg.addEventListener('click',event=>{
 const role=svg.dataset.role,point=new DOMPoint(event.clientX,event.clientY).matrixTransform(svg.getScreenCTM().inverse());
 if(modes[role]==='inspect'){const id=event.target.closest('[data-id]')?.dataset.id;if(id)details(role,data.records[id]);return}
 const binding=data.bindings[role];if(point.x<0||point.y<0||point.x>=binding.width||point.y>=binding.height)return;
 if(!pending[role]){pending[role]=[point.x,point.y];details(role,{first_point_native:pending[role],frame_id:binding.frame_id,gt_status:'UNREVIEWED'});return}
 const draft={review_id:'manual:'+role+':'+drafts.length,role,frame_id:binding.frame_id,source_binding:binding,manual_geometry_native:{type:'segment',points:[pending[role],[point.x,point.y]]},review_status:'DRAFT_NOT_GT',gt_is_wall:null,gt_is_opening:null,gt_opening_kind:null,gt_host_wall_ids:null,gt_notes:null};
 drafts.push(draft);delete pending[role];const line=document.createElementNS('http://www.w3.org/2000/svg','line');const [a,b]=draft.manual_geometry_native.points;for(const [k,v]of Object.entries({x1:a[0],y1:a[1],x2:b[0],y2:b[1],stroke:'#df00bc','stroke-width':4}))line.setAttribute(k,v);svg.querySelector('.manual-layer').appendChild(line);details(role,draft);document.querySelector('#manual-status').textContent='검토 초안 '+drafts.length+'개 · GT 미확정';
}));
document.querySelectorAll('.clear').forEach(button=>button.onclick=()=>{const role=button.dataset.role;for(let i=drafts.length-1;i>=0;i--)if(drafts[i].role===role)drafts.splice(i,1);delete pending[role];document.querySelector('#viewer-'+role+' .manual-layer').replaceChildren();document.querySelector('#manual-status').textContent='검토 초안 '+drafts.length+'개 · GT 미확정'});
document.querySelector('#export-manual').onclick=()=>{const payload={schema_version:'manual_geometry_review_draft/1',prediction_is_not_gt:true,review_status:'DRAFT_NOT_GT',records:drafts};const a=document.createElement('a');a.href=URL.createObjectURL(new Blob([JSON.stringify(payload,null,2)],{type:'application/json'}));a.download='page4_manual_geometry_review_draft.json';a.click();setTimeout(()=>URL.revokeObjectURL(a.href),1000)};
window.__geometryReview={data,drafts,modes};
'''.replace('__PAYLOAD__',json.dumps(sg.jsonable(payload),ensure_ascii=False).replace('<','\u003c'))
    parts+=['<script>'+script+'</script></html>'];(out/'review.html').write_text('\n'.join(parts),encoding='utf-8')


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--baseline',type=Path,default=P1);parser.add_argument('--inputs',type=Path,default=ROOT/'poc/results/page4_inputs');parser.add_argument('--out',type=Path,default=ROOT/'poc/results/geometry_refinement_page4');args=parser.parse_args()
    if args.out.exists():raise SystemExit('Existing output preserved. Use a new --out folder.')
    inputs,manifest=load_inputs(args.inputs);baseline=args.baseline.resolve()
    required=['geometry_observations.json','element_candidates.json','space_candidates.json','understanding_bundle.json']
    for name in required:
        if not (baseline/name).is_file():raise FileNotFoundError('Missing actual PoC 1 artifact: '+str(baseline/name))
    obs=json.loads((baseline/required[0]).read_text())['drawings'];elements=json.loads((baseline/required[1]).read_text())['drawings'];spaces=json.loads((baseline/required[2]).read_text())['drawings'];bundle=json.loads((baseline/required[3]).read_text())
    target=manifest['drawings']['Target'];roi_context=None
    if target.get('user_roi'):
        path=inputs/target['user_roi']['path'];roi=json.loads(path.read_text());validate_roi(roi,target['binding']);assert sg.sha(path)==target['user_roi']['sha256'];roi_context={'path':str(path),'sha256':sg.sha(path),'use':'preserved context only, not a Ref mapping or inferred space boundary'}
    args.out.mkdir(parents=True);settings=RefinementSettings();run={'schema_version':'geometry_refinement_poc/2','status':'RUNNING','started_utc':datetime.now(timezone.utc).isoformat(),
        'baseline':str(baseline),'baseline_hashes':{n:sg.sha(baseline/n) for n in required},'inputs':str(inputs),'config':asdict(settings),'user_roi_context':roi_context,
        'source_code_sha256':{name:sg.sha(Path(__file__).with_name(name)) for name in ['run_geometry_refinement.py','geometry_refinement.py']},
        'vlm_calls':0,'model_downloads':0,'ground_truth':None,'metrics':None,'independent_roles':True}
    sg.write_json(args.out/'run.json',run);drawings={};crops={}
    try:
        for role,drawing in manifest['drawings'].items():
            if obs[role]['frame_id']!=drawing['binding']['frame_id']:raise ValueError('Baseline/input frame mismatch')
            result,debug=refine_role(role,drawing['binding'],obs[role],elements[role],spaces[role],inputs,settings)
            drawings[role]=result;crops[role]=draw_results(args.out,role,result,debug,baseline,bundle['coverage']['display_crops'][role]);del debug
            print(role,result['counts'],'seconds',round(result['runtime_seconds'],2),flush=True)
        bindings={r:d['source_binding'] for r,d in drawings.items()};common={'schema_version':'geometry_refinement/2','source_bindings':bindings,'coordinate_system':'image_native_pixels','ground_truth':None,'metrics':None,
            'reused_evidence_sources':{'poc1_geometry_observations':{'path':str(baseline/'geometry_observations.json'),'sha256':run['baseline_hashes']['geometry_observations.json']},
                'ocr':{r:{'path':str(ROOT/'poc/results/page4_ocr'/r/'observations.json'),'sha256':sg.sha(ROOT/'poc/results/page4_ocr'/r/'observations.json')} for r in drawings}},
            'architectural_claim_status':'PROPOSED; SUPPORTED denotes scoped geometric facts, not architectural GT'}
        for name,key in [('wall_candidates','wall_candidates'),('wall_segments','wall_segments'),('opening_candidates','openings'),('element_candidates','elements'),('wall_topology','topology')]:sg.write_json(args.out/(name+'.json'),{**common,'drawings':{r:d[key] for r,d in drawings.items()}})
        sg.write_json(args.out/'space_boundary_review.json',{**common,'drawings':{r:{'space_candidates':d['spaces'],'comparison':d['space_comparison'],'queries':d['queries'],'relations':d['relations'],'relation_attempts':d['relation_attempts']} for r,d in drawings.items()}})
        sg.write_json(args.out/'geometry_evidence.json',{**common,'drawings':{r:{k:d[k] for k in ['thickness_distribution','rejected_candidates','arc_attachment_attempts','component_observations','coordinate_checks','counts','runtime_seconds']} for r,d in drawings.items()}})
        sg.write_json(args.out/'understanding_bundle.json',{**common,'entities':[e for d in drawings.values() for e in d['elements']+d['spaces']],
            'relations':[r for d in drawings.values() for r in d['relations']],'local_topology_artifact':'wall_topology.json','wall_segment_artifact':'wall_segments.json','opening_artifact':'opening_candidates.json',
            'coverage':{'display_contexts':crops,'mode':'independent page geometry, not Ref–Target correspondence'},'completion':'PARTIAL_GEOMETRY_AND_TOPOLOGY_NOT_CHANGE_DETECTION'})
        records=gt_templates(args.out,drawings);render_review(args.out,drawings,crops,records)
        run.update(status='COMPLETE_WITH_PARTIAL_GEOMETRY',completed_utc=datetime.now(timezone.utc).isoformat(),summary={r:d['counts'] for r,d in drawings.items()})
    except Exception as exc:run.update(status='FAILED',error=str(exc),traceback=traceback.format_exc());raise
    finally:sg.write_json(args.out/'run.json',run)


if __name__=='__main__':main()
