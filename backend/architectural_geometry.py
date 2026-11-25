"""Uploaded-ROI adapter for the preserved PoC geometry engine, not a second engine.

Rendering limits never feed inference. Source primitives are re-read, cropped,
linked to an uncapped evidence registry, then passed to existing PoC functions.
"""
import copy,hashlib,json,math,sys,time,uuid
from collections import Counter
from dataclasses import asdict
from pathlib import Path
import cv2,numpy as np,pymupdf
from backend.architectural_observations import digest,clip_segment,intersects,bbox,offset,MAX_PIXELS
from poc.space_grounding_geometry import (Settings,axis_points,axis_segment,transform_points,pdf_to_native_matrix,
    raster_observations,vector_observations,merge_lines,pair_boundaries,group_walls)
# The preserved PoC 2 module uses a sibling absolute import.
from poc import space_grounding_geometry as _geometry
sys.modules.setdefault('space_grounding_geometry',_geometry)
from backend.architectural_geometry_assessment import assess_geometry
from backend.observation_trace import TraceJournal,primitive_identity
from poc.geometry_refinement import (RefinementSettings,reconstruct_bands,visible_raw_lines,opening_grounding,topology)

VERSION='uploaded_roi_geometric_understanding/1.1-installation-shapes'


def validate_parent(binding,parent):
    if parent.get('bundle_kind')!='OBSERVATION_ONLY':raise ValueError('Observation bundle required')
    keys=['source_role','source_sha256','page','extraction_zoom','image_id','frame_id','width','height','image_sha256','core_bbox_native','context_bbox_native','document_id']
    if any(binding.get(k)!=parent.get('source_binding',{}).get(k) for k in keys):raise ValueError('Observation input no longer matches PDF/ROI/frame')
    if parent.get('status') not in ['COMPLETED','PARTIAL']:raise ValueError('Observation not completed')


def analyze_geometry(pdf_bytes,image_png,binding,parent):
    tick=time.perf_counter();validate_parent(binding,parent)
    if digest(pdf_bytes)!=binding['source_sha256'] or digest(image_png)!=binding['image_sha256']:raise ValueError('Original bytes mismatch')
    role=binding['source_role']
    if role not in ['Ref','Target']:raise ValueError('Invalid source role')
    im=cv2.imdecode(np.frombuffer(image_png,np.uint8),cv2.IMREAD_COLOR)
    if im is None or im.shape[:2]!=(binding['height'],binding['width']):raise ValueError('Native image dimensions mismatch')
    x0,y0,x1,y1=context=binding['context_bbox_native'];core=binding['core_bbox_native']
    if not (0<=x0<x1<=binding['width'] and 0<=y0<y1<=binding['height']) or (x1-x0)*(y1-y0)>MAX_PIXELS:raise ValueError('Context outside allowed frame')
    crop=im[y0:y1,x0:x1];crop_sha=digest(cv2.imencode('.png',crop)[1].tobytes())
    if crop_sha!=parent['analysis_scope']['input_crop_sha256']:raise ValueError('Context differs from Observation')
    run=binding.get('analysis_run_id') or uuid.uuid4().hex;prefix=f'{binding["frame_id"]}:geometry:{run}'
    trace=TraceJournal(binding,run,'GEOMETRY_INPUT_AND_EXPORT')
    registry=[];lookup={};parent_sources={}
    for o in parent['observations']:
        key=o['source_evidence'].get('source_primitive_id')
        if key:parent_sources.setdefault(key,[]).append(o['observation_id'])
    def observe(key,kind,points,source):
        if key in lookup:return lookup[key]
        ident=f'{prefix}:observation:{len(registry):06d}'
        record={'observation_id':ident,'source_role':role,'frame_id':binding['frame_id'],'observation_type':kind,
            'geometry_native':{'coordinate_system':'image_native_pixels','points_native':points},
            'source_evidence':{'source_sha256':binding['source_sha256'],'image_sha256':binding['image_sha256'],'page':binding['page'],'input_crop_sha256':crop_sha,'source_registry_key':key,**source},
            'evidence_status':'OBSERVED_UNREVIEWED','claim_status':'PROPOSED','parent_display_observation_refs':parent_sources.get(key,[])}
        registry.append(record);lookup[key]=ident;return ident
    text=[]
    for o in parent['observations']:
        if o['observation_type']=='TextRegion':
            p=offset(o['geometry_native']['points_native'],-x0,-y0)
            text.append({'id':o['observation_id'],'text':o['source_evidence']['text'],'polygon_native':p})
            registry.append(copy.deepcopy(o));lookup[o['observation_id']]=o['observation_id']
    height=float(np.median([bbox(t['polygon_native'])[3]-bbox(t['polygon_native'])[1] for t in text])) if text else max(8,binding['height']/100)
    raster=raster_observations(cv2.cvtColor(crop,cv2.COLOR_BGR2GRAY),[t['polygon_native'] for t in text],height,Settings())
    trace.record(None,'RasterMaskDiagnostic','MEASURED','EXISTING_PIXEL_MASK_AUDIT_NOT_OBJECT_DELETION',
                 bounds=context,pixel_audit=raster.get('morphology_audit',{}))
    with pymupdf.open(stream=pdf_bytes,filetype='pdf') as doc:
        page=doc[binding['page']-1];pix=page.get_pixmap(matrix=pymupdf.Matrix(binding['extraction_zoom'],binding['extraction_zoom']),colorspace=pymupdf.csRGB,alpha=False)
        if digest(pix.tobytes('png'))!=binding['image_sha256']:raise ValueError('PDF/native coordinate frame mismatch')
        matrix=np.asarray(pdf_to_native_matrix(page,binding['extraction_zoom'],(pix.x,pix.y)))
        vector=vector_observations(page,matrix)
        # Record the fitter's ACTUAL omissions, not every mixed-item path.
        interpreted_curve_ids={a['id'] for a in vector['arcs']}
        for pi,path in enumerate(page.get_drawings()):
            if len(path['items'])<=1:continue
            for ii,item in enumerate(path['items']):
                if item[0]!='c':continue
                control=transform_points([list(p) for p in item[1:]],matrix);bounds=bbox(control)
                if bounds[2]<x0 or bounds[0]>x1 or bounds[3]<y0 or bounds[1]>y1:continue
                key=f'pdf_path:{pi}:curve:{ii}'
                if key in interpreted_curve_ids:continue
                trace.record(primitive_identity(binding,run,{'kind':'PDF_VECTOR','source_primitive_id':key}),
                    'PdfCurve','NOT_INTERPRETED','COMPOUND_PDF_CURVE_NOT_SUPPORTED_BY_EXISTING_FITTER',
                    bounds=bounds,geometry_semantics='CONTROL_POINT_EXTENT_ONLY_NOT_A_FITTED_ARCHITECTURAL_ARC')
    # Extractability metadata can cover the page; only context-intersecting
    # primitives enter the engine. Never use the parent's capped PDF lines.
    segments=[];arcs=[];rectangles=[];context_clips=0
    for s in vector['segments']:
        clipped=clip_segment(s['points'],context)
        if clipped is None:continue
        cut=clipped!=s['points'];context_clips+=cut
        local={**s,'points':offset(clipped,-x0,-y0),'context_clipped':cut};segments.append(local)
        observe(s['id'],'PdfLine',clipped,{'kind':'PDF_VECTOR','algorithm':'preserved vector_observations','source_primitive_id':s['id'],
            'native_unclipped_points':s['points'],'context_clipped':cut,'pdf_points_local':transform_points(s['points'],np.linalg.inv(matrix)),
            'layer':s['layer'],'path_type':s['path_type'],'stroke_px':s['stroke_px']})
    for a in vector['arcs']:
        if not intersects(a['polyline'],context):continue
        # Do not fabricate a hinge/arc from a cropped part of a curve.
        complete=all(x0<=p[0]<=x1 and y0<=p[1]<=y1 for p in a['polyline'])
        observe(a['id'],'PdfCurve',a['polyline'],{'kind':'PDF_VECTOR','algorithm':'preserved fit_arc','source_primitive_id':a['id'],
            'pdf_control_points_local':transform_points(a['control'],np.linalg.inv(matrix)),'context_truncated':not complete})
        if complete:arcs.append({**a,'polyline':offset(a['polyline'],-x0,-y0),'control':offset(a['control'],-x0,-y0),'center':offset([a['center']],-x0,-y0)[0],'endpoints':offset(a['endpoints'],-x0,-y0)})
    for r in vector['rectangles']:
        b=r['bbox']
        if b[2]<x0 or b[0]>x1 or b[3]<y0 or b[1]>y1:continue
        rectangles.append({**r,'bbox':[b[0]-x0,b[1]-y0,b[2]-x0,b[3]-y0]})
        observe(r['id'],'PdfRectangle',[[b[0],b[1]],[b[2],b[1]],[b[2],b[3]],[b[0],b[3]]],{'kind':'PDF_VECTOR','source_primitive_id':r['id']})
    for s in raster['segments']:
        observe(s['id'],'RasterLine',offset(s['points'],x0,y0),{'kind':'RASTER','algorithm':'preserved raster_observations LSD','source_primitive_id':s['id'],'detector_support_width_px':s['stroke_px']})
    context_curves=parent['coverage']['observation_counts'].get('PdfCurve',0)+parent['coverage']['omitted_due_to_limits'].get('PdfCurve',0)
    single_curve_count=sum(o['observation_type']=='PdfCurve' for o in registry)
    curve_scope={'all_context_PDF_curve_primitives_from_observation_stage':context_curves,'single_Bezier_curves_inspected_from_PDF':single_curve_count,'single_Bezier_arcs_with_complete_context':len(arcs),'compound_curve_primitives_not_interpreted':max(0,context_curves-single_curve_count),'context_cut_arcs_not_fitted':single_curve_count-len(arcs)}
    lines=[];baseline_pairs=[];filter_audits={}
    for name,raw in [('pdf',segments),('raster',raster['segments'])]:
        def record_filter(segment,state,reason):
            trace.record(lookup.get(segment['id']),name+'Line',state,reason,
                points=offset(segment['points'],x0,y0),source_primitive_id=segment['id'],
                channel='WALL_BOUNDARY_MERGING',decision_scope='THIS_FILTER_ONLY_NOT_GLOBAL_OBSERVATION_DELETION')
        def record_pair(event):
            trace.record(None,'ParallelBoundaryProposal',event['status'],event['reason'],
                points=offset(event['polygon'],x0,y0),input_ids=[prefix+':registry-key:'+key for key in event['boundary_ids']],
                duplicate_of_ids=[prefix+':registry-key:'+key for key in event.get('duplicate_of_boundary_ids',[])])
        ls,audit=merge_lines(raw,raster,height,Settings(),name,audit_sink=record_filter);pairs,pa=pair_boundaries(ls,raster,height,Settings(),audit_sink=record_pair)
        lines+=ls;baseline_pairs+=pairs;filter_audits[name]={'line_filter':audit,'pair_filter':pa}
    # A single well-supported merged band can cite both vector and raster
    # sources. PoC 2 arbitration decides local alternatives, not display caps.
    complete=True;coverage_reasons=[]
    if parent['components'].get('OCR',{}).get('status')!='COMPLETED' or parent['coverage']['omitted_due_to_limits'].get('TextRegion'):
        complete=False;coverage_reasons.append('TEXT_COVERAGE_INCOMPLETE')
    if any(axis_segment(s['points']) is None for s in segments+raster['segments']):
        complete=False;coverage_reasons.append('DIAGONAL_PRIMITIVES_PRESERVED_BUT_WALL_RECONSTRUCTION_AXIAL_ONLY')
    if curve_scope['compound_curve_primitives_not_interpreted']:
        complete=False;coverage_reasons.append('COMPOUND_PDF_CURVES_NOT_INTERPRETED_BY_PRESERVED_SWING_FITTER')
    if curve_scope['context_cut_arcs_not_fitted']:
        complete=False;coverage_reasons.append('CONTEXT_CUT_ARCS_NOT_FITTED_AS_COMPLETE_SWINGS')
    if not vector['arcs'] and not vector['segments']:
        complete=False;coverage_reasons.append('RASTER_ONLY_INPUT_NO_SWING_ARC_ESTIMATOR_IN_PRESERVED_ENGINE')
    counts_before={'pdf_segments_page':len(vector['segments']),'pdf_segments_context':len(segments),'raster_segments_context':len(raster['segments']),'merged_lines':len(lines),'baseline_pairs':len(baseline_pairs)}
    budget_exceeded=len(lines)>6000 or len(baseline_pairs)>12000
    recovery_events=[]
    if budget_exceeded:
        bands=[];singles=[];rejected=[];distribution={'status':'NOT_COMPUTED_RESOURCE_BUDGET','modes':[]};gaps=[];attempts=[];graph={'edges':[],'uncertain_edges':[]}
        complete=False;coverage_reasons.append('ENGINE_WORK_BUDGET_EXCEEDED_NO_TRUNCATED_INFERENCE_USED')
    else:
        bands,singles,rejected,distribution=reconstruct_bands(lines,raster,baseline_pairs,height,role,RefinementSettings(),ocr=text,rectangles=rectangles)
        raw=visible_raw_lines(segments+raster['segments'],raster,height,role,RefinementSettings())
        gaps,attempts=opening_grounding(bands,lines,arcs,raw,raster,height,role,RefinementSettings(),audit_sink=recovery_events.append)
        graph=topology(bands,gaps,raster,role,RefinementSettings())
    # Derived evidence has its own registry, so every entity reference resolves
    # even when its original line was absent from the Phase 1 display bundle.
    for l in lines:
        observe(l['id'],'MergedBoundary',offset(l['points'],x0,y0),{'kind':'DERIVED','algorithm':'preserved merge_lines','observation_refs':[lookup[k] for k in l['source_ids'] if k in lookup],
            'source_ids':l['source_ids'],'layers':l['layers'],'raster_support_fraction':l['raster_support_fraction']})
    for event in recovery_events:
        trace.record(None,event['kind'],event['status'],event['reason'],points=offset(event['points'],x0,y0),
            input_ids=[lookup.get(k.removeprefix(role+':'),prefix+':registry-key:'+k.removeprefix(role+':')) for k in event['source_ids']],
            stage='INSTALLATION_SHAPE_RECOVERY',claim_scope='OWN_DRAWN_GEOMETRY_NOT_PHYSICAL_GAP',
            physical_wall_gap_status='UNKNOWN')
    line_lookup={l['id']:l for l in lines};source_lookup={s['id']:s for s in segments+raster['segments']}
    entities=[];evidence=[];band_entity={}
    def native(p):return offset(p,x0,y0)
    def add_entity(kind,geometry,properties,refs,support,contra,uncertainties,review=False):
        points=[p for seg in geometry.get('polylines_native',[]) for p in seg]+[p for poly in geometry.get('polygons_native',[]) for p in poly]
        points+=geometry.get('points_native',[])
        if not points:return None
        poly=bbox(points);hit=not(poly[2]<core[0] or poly[0]>core[2] or poly[3]<core[1] or poly[1]>core[3])
        cut=hit and any(not(core[0]<=p[0]<=core[2] and core[1]<=p[1]<=core[3]) for p in points)
        context_cut=any(min(abs(p[0]-x0),abs(p[0]-x1),abs(p[1]-y0),abs(p[1]-y1))<=2 or not(x0<=p[0]<=x1 and y0<=p[1]<=y1) for p in points)
        ident=f'{prefix}:entity:{len(entities):05d}';ev_id=ident+':evidence'
        # refs must resolve; unresolved source evidence makes the candidate review-required.
        resolved=sorted(set(lookup[r] for r in refs if r in lookup));missing=sorted(set(r for r in refs if r not in lookup))
        ev={'evidence_id':ev_id,'frame_id':binding['frame_id'],'source_role':role,'source_observation_refs':resolved,'supporting':support,'contradicting':contra,'unresolved_source_ids':missing,'status':'OBSERVED_GEOMETRIC_EVIDENCE_NOT_ARCHITECTURAL_TRUTH'};evidence.append(ev)
        entity={'entity_id':ident,'source_role':role,'frame_id':binding['frame_id'],'element_type':kind,'geometry_native':{'coordinate_system':'image_native_pixels','bbox_native':poly,**geometry},
            'geometry_properties':properties,'observation_refs':resolved,'evidence_refs':[ev_id],'confidence':None,'confidence_semantics':'No calibrated architectural correctness score',
            'claim_status':'PROPOSED','evidence_status':'REVIEW_REQUIRED' if review or missing or contra else 'GEOMETRIC_SUPPORT_UNREVIEWED','uncertainty_reason':uncertainties+(['UNRESOLVED_SOURCE_EVIDENCE'] if missing else []),
            'roi_intersection':hit,'roi_intersection_semantics':'geometry bounding extent overlap','boundary_truncated':bool(cut or context_cut),'boundary_details':{'core_boundary_crossed':cut,'context_boundary_contact_or_cut':context_cut,'physical_endpoints_status':'UNKNOWN'}}
        entities.append(entity);return entity
    grouped=[]
    for classification in sorted({b['classification'] for b in bands}):
        own=[{**b,'_band_id':b['id']} for b in bands if b['classification']==classification]
        for group in group_walls(own,role,height):group['classification']=classification;grouped.append(group)
    for g in grouped:
        pieces=g['pieces'];is_wall=all(p['space_barrier_eligible'] for p in pieces);refs=[k for p in pieces for k in p['boundary_ids']]
        supporting=[{'kind':'PARALLEL_BOUNDARIES','boundary_ids':p['boundary_ids'],'separation_px':p['thickness_px'],'source_overlap_length_px':p['source_overlap_length_px'],'raster_support':p['boundary_support'],'interior_ink_fraction':p['interior_ink_fraction'],'stroke_balance':p['stroke_balance']} for p in pieces]
        contra=[{'kind':'CLASSIFICATION_OR_CONTEXT_CAUTION','classification':p['classification'],'flags':p['flags'],'annotation_context':p['annotation_context']} for p in pieces if not p['space_barrier_eligible'] or p['flags']]
        for p in pieces:
            for tid in p['annotation_context']['nearby_numeric_text_region_ids']+p['annotation_context']['interior_text_region_ids']:refs.append(tid)
        center=[native(axis_points(p['axis'],p['normal'],p['start'],p['end'])) for p in pieces]
        boundaries=[native(axis_points(p['axis'],n,p['start'],p['end'])) for p in pieces for n in [p['n1'],p['n2']]]
        fragments = []
        if is_wall:
            for i, part in enumerate(pieces):
                faces = boundaries[2*i:2*i+2]
                visible = [float(np.mean(_geometry.sample_line(raster['visible_distance'],
                    offset(face, -x0, -y0)) <= RefinementSettings().pixel_tolerance)) for face in faces]
                polygon = native(part['polygon'])
                contacts = sorted({hit['other_segment_id'] for end in part['endpoint_contacts']
                                   for hit in end['contacts']})
                fragments.append({
                    'segment_id': part['_band_id'], 'polygon_native': polygon, 'faces_native': faces,
                    'centerline_native': center[i], 'thickness_px': part['thickness_px'],
                    'raster_support': visible, 'endpoint_contact_ids': contacts,
                    'observation_refs': [lookup[k] for k in part['boundary_ids']],
                    'arbitration_cut': part['arbitration_cut'],
                    'context_truncated': any(x <= x0+2 or x >= x1-2 or y <= y0+2 or y >= y1-2 for x,y in polygon),
                    'interior_text_ids': part['annotation_context']['interior_text_region_ids'],
                })
        entity=add_entity('Wall' if is_wall else 'Unknown',{'polygons_native':[native(p['polygon']) for p in pieces],'polylines_native':center+boundaries},
            {'classification':g['classification'],'orientation':g['axis'],'centerlines_native':center,'boundary_segments_native':boundaries,'thickness_estimate_px':float(np.median([p['thickness_px'] for p in pieces])),
             'thickness_interval_px':[min(p['thickness_px'] for p in pieces),max(p['thickness_px'] for p in pieces)],'thickness_status':'PARALLEL_SEPARATION_HYPOTHESIS_NOT_VERIFIED_WALL_THICKNESS',
             'is_thin_hypothesis':any(p['is_thin_hypothesis'] for p in pieces),'observed_endpoints_native':[v for c in center for v in c],
             'physical_endpoints_status':'UNKNOWN','physical_gaps_filled':False,'observed_wall_fragments':fragments,'part_count':len(pieces),'legacy_segment_ids':[p['_band_id'] for p in pieces]},
             refs,supporting,contra,['Wall type remains PROPOSED; endpoints are observed intervals, not physical wall ends.']+([] if is_wall else ['Nonstructural/annotation/frame alternative retained; no automatic wall approval.']),not is_wall)
        if entity:
            for p in pieces:band_entity[p['_band_id']]=entity['entity_id']
    for s in singles:
        add_entity('Unknown',{'polylines_native':[native(s['points_native'])]}, {'classification':'UNKNOWN_SINGLE_LINE_PARTITION','thickness_estimate_px':None,'orientation':s['axis'],'physical_endpoints_status':'UNKNOWN'},
            [s['source_line_id']],[{'kind':'SINGLE_VISIBLE_LINE_WITH_CONTACT'}],[],['Single line does not establish thickness or architectural wall type.'],True)
    for gap in gaps:
        refs=[];support=[]
        for host in gap['host_segment_ids']:
            b=next(b for b in bands if b['id']==host);refs+=b['boundary_ids']
        for proposal in gap['door_proposals']:
            refs.append(proposal['arc_id'])
            for proof in proposal['jamb_connections']:
                for key in proof.get('source_segment_ids',[]):refs.append(key.removeprefix(role+':'))
            # Derive leaf locations from the same visible arc endpoints, not a nearest point.
            arc=next(a for a in arcs if a['id']==proposal['arc_id'])
            leaves=[{'segment_native':native([arc['center'],p]),'visible_fraction':float(np.mean(_geometry.sample_line(raster['visible_distance'],[arc['center'],p])<=RefinementSettings().pixel_tolerance))} for p in arc['endpoints']]
            support.append({'kind':'SWING_LEAF_AND_BOTH_JAMBS','arc_observation_ref':lookup[proposal['arc_id']], 'hinge_candidate_native':native([proposal['hinge_native']])[0],
                'swing_polyline_native':native(proposal['swing_polyline_native']),'swing_radius_px':proposal['radius_px'],'curve_visibility':proposal['curve_visibility'],'leaf_candidates':leaves,
                'jamb_connections':[{'connected':j['connected'],'observation_refs':[lookup[k.removeprefix(role+':')] for k in j.get('source_segment_ids',[]) if k.removeprefix(role+':') in lookup],
                    'start_native':native([j['start_native']])[0] if 'start_native' in j else None,'host_boundary_points_native':native(j['host_boundary_points_native']) if 'host_boundary_points_native' in j else None} for j in proposal['jamb_connections']]})
        refs+=gap['track_line_ids'];refs += [k.removeprefix(role+':') for k in gap['transverse_frame_ids']]
        if gap.get('installation_shape_only'):
            support.append({'kind':'INSTALLATION_SHAPE_RECOVERY','claim_scope':'DRAWING_ONLY',
                'method':gap['recovery_evidence']['method'],'physical_wall_gap_status':'UNKNOWN',
                'source_observation_refs':[lookup[k.removeprefix(role+':')] for k in gap['recovery_evidence']['source_ids'] if k.removeprefix(role+':') in lookup],
                'type_alternatives':gap['recovery_evidence'].get('type_alternatives',['Door'])})
        support.append({'kind':'GAP_BRACKETS_TRACKS','gap_kind':gap['gap_kind'],'jamb_brackets_native':[native(p) for p in gap['bracket_points_native']],
            'cap_visibility':gap['bracket_cap_visibility'],'tracks_native':[native(p) for p in gap['track_lines_native']],'track_observation_refs':[lookup[k] for k in gap['track_line_ids'] if k in lookup]})
        contra=[{'kind':'SOLID_BAND_CONTRADICTS_GAP','candidate_ids':[band_entity[k] for k in gap['contradicting_solid_band_ids'] if k in band_entity]}] if gap['contradicting_solid_band_ids'] else []
        kind=gap['kind'];uncertainties=[gap['uncertainty']]
        if kind=='Window':
            kind='Unknown';uncertainties+=['WINDOW_VS_SLIDING_DOOR_NOT_RESOLVED_BY_TRACKS; no architectural code mapping.']
        geom={'polygons_native':[native(gap['polygon'])],'polylines_native':[native(gap['logical_segment'])]+[p['swing_polyline_native'] for p in support if p['kind']=='SWING_LEAF_AND_BOTH_JAMBS']}
        if gap.get('installation_shape_only'):
            geom['polylines_native'] += [native(p) for p in gap['track_lines_native']+gap['bracket_points_native']]
        add_entity(kind,geom,{'classification':'WINDOW_OR_SLIDING_TRACK' if gap['kind']=='Window' else gap['kind'].upper()+'_GEOMETRIC_HYPOTHESIS',
            **({'installation_shape_only':True,'recovery_method':gap['recovery_evidence']['method'],
                'installation_type_alternatives':gap['recovery_evidence'].get('type_alternatives',['Door']),
                'logical_boundary_eligible':False,'physical_wall_gap_status':'UNKNOWN'} if gap.get('installation_shape_only') else {}),
            'opening_width_px':gap['width_px'],'opening_width_interval_px':gap.get('width_interval_px',[gap['width_px'],gap['width_px']]),'orientation':gap['axis'],
            'host_wall_candidate_ids':[band_entity[k] for k in gap['host_segment_ids'] if k in band_entity and next(b for b in bands if b['id']==k)['axis']==gap['axis']], 'jamb_bracket_candidate_ids':[band_entity[k] for k in gap['host_segment_ids'] if k in band_entity], 'swing_radii_px':[p['radius_px'] for p in gap['door_proposals']], 'opening_segment_native':native(gap['logical_segment']),
            'window_track_count':len(gap['track_line_ids']),'geometry_subtype_hypothesis':gap['kind'],'gap_status':gap['gap_status'],'physical_endpoints_status':'UNKNOWN'},refs,support,contra,uncertainties,bool(contra) or kind=='Unknown')
    # Preserve diagonal observations explicitly instead of asserting an axial wall fit.
    diagonal=[s['id'] for s in segments+raster['segments'] if axis_segment(s['points']) is None]
    for key in diagonal:
        s=source_lookup[key]
        if s.get('path_type')=='f' or np.linalg.norm(np.diff(s['points'],axis=0))<height*2:continue
        add_entity('Unknown',{'polylines_native':[native(s['points'])]}, {'classification':'UNKNOWN_DIAGONAL_STRUCTURE','orientation':'DIAGONAL','thickness_estimate_px':None,'physical_endpoints_status':'UNKNOWN'},
            [key],[{'kind':'OBSERVED_DIAGONAL_SEGMENT'}],[],['No diagonal wall-band reconstruction in the preserved engine; may be furniture, annotation or structure.'],True)
    junctions=[]
    for edge in graph.get('edges',[])+graph.get('uncertain_connections',[]):
        if edge['type'] not in ['WALL_JUNCTION','OBSERVED_CONTINUATION']:continue
        ids=sorted(set(band_entity[k] for k in edge['wall_segment_ids'] if k in band_entity))
        if len(ids)<1:continue
        junctions.append({'evidence_id':f'{prefix}:contact:{len(junctions):04d}','kind':edge['type'],'junction_type':edge.get('junction_type'),'candidate_ids':ids,
            'point_native':native([edge['point_native']])[0] if edge.get('point_native') else None,'gap_px':edge.get('gap_px'),'geometric_support_status':edge['status'],'claim_status':'PROPOSED',
            'source_role':role,'frame_id':binding['frame_id'],'meaning':'geometric contact only, no scene graph or semantic relation approval'})
    assessment = assess_geometry({**binding, 'analysis_run_id': run}, entities, evidence, registry, RefinementSettings())
    partition = None
    independent_target = role=='Target' and parent.get('independent_understanding')=={'source_role':'Target','peer_input_used':False}
    understanding = role=='Ref' or independent_target
    if understanding and budget_exceeded:
        partition = {'schema_version':'reference_space_closure/1' if role=='Ref' else 'target_space_closure/1','source_role':role,
                     'source_binding':assessment['source_binding'],'frame_id':binding['frame_id'],
                     'status':'NOT_COMPUTED_RESOURCE_BUDGET','reason':'PARENT_GEOMETRY_WORK_BUDGET',
                     'faces':[], 'regions':[], 'unlabelled_regions':[], 'verified_spaces':0}
    elif understanding:
        from backend.reference_space_closure import restore_partitions
        partition = restore_partitions(assessment['source_binding'], registry, assessment,
                                       cv2.cvtColor(im, cv2.COLOR_BGR2GRAY), expected_role=role)
    domains = None
    relations = None
    if partition:
        from backend.reference_domains import derive_reference_domains
        domains = derive_reference_domains(partition, assessment, registry, entities,expected_role=role)
        from backend.reference_relations import ground_reference_relations
        relations = ground_reference_relations(pdf_bytes,partition,domains,assessment,registry,entities,evidence,vector['symbols'],matrix,expected_role=role)
    # Export evidence closure, not tens of thousands of unrelated glyph edges.
    # All raw context primitives were used by filtering/inference above; this
    # export reduction is independent of inference and is recorded explicitly.
    raw_registry_count=len(registry);by_observation={o['observation_id']:o for o in registry}
    retained={o['observation_id'] for o in registry if o['observation_type'] in ['MergedBoundary','TextRegion']}
    retained.update(r for e in entities for r in e['observation_refs'])
    retained.update(lookup[a['arc_id']] for a in attempts if a['arc_id'] in lookup)
    if partition:
        retained.update(ref for face in partition['faces'] for ref in face['source_refs'] if ref in by_observation)
    stack=list(retained)
    while stack:
        current=by_observation[stack.pop()]
        for ref in current['source_evidence'].get('observation_refs',[]):
            if ref not in retained:retained.add(ref);stack.append(ref)
    for observation in registry:
        source=observation['source_evidence'];identity=observation['observation_id']
        trace.record(identity,observation['observation_type'],
            'EXPORTED' if identity in retained else 'NOT_SERIALIZED',
            'IN_EVIDENCE_DEPENDENCY_CLOSURE' if identity in retained else 'NOT_REFERENCED_BY_EXPORTED_ENTITIES_EXPORT_ONLY',
            points=observation['geometry_native']['points_native'],
            input_ids=[primitive_identity(binding,run,source),*observation.get('parent_display_observation_refs',[]),*source.get('observation_refs',[])],
            output_ids=[prefix+':registry-key:'+source['source_registry_key']] if source.get('source_registry_key') else [],
            source_primitive_id=source.get('source_primitive_id'),claim_status=observation.get('claim_status','PROPOSED'))
    registry=[o for o in registry if o['observation_id'] in retained]
    unknown=sum(e['element_type']=='Unknown' for e in entities)
    if unknown:complete=False;coverage_reasons.append('UNRESOLVED_ARCHITECTURAL_TYPES_RETAINED')
    status='PARTIAL' if not complete else 'COMPLETED'
    result = {'schema_version':'architectural_geometry_bundle/1','bundle_kind':'GEOMETRIC_ENTITY_CANDIDATES','run_id':run,'source_role':role,'frame_id':binding['frame_id'],
        'source_binding':binding,'parent_observation_run_id':parent['run_id'],'algorithm':VERSION,'reused_settings':{'geometry':asdict(Settings()),'refinement':asdict(RefinementSettings())},
        'analysis_scope':copy.deepcopy(parent['analysis_scope']),'frame_registry':copy.deepcopy(parent['frame_registry']),'status':status,'entities':entities,'observations':registry,'evidence':evidence,'junction_evidence':junctions,'architectural_assessment':assessment,**({'space_closure':partition,('reference_domains' if role=='Ref' else 'target_domains'):domains,('reference_relations' if role=='Ref' else 'target_relations'):relations} if partition else {}),'claims':[],
        'producer_traces':[*parent.get('producer_traces',[]),trace.finish()],
        'coverage':{'source_observations_reextracted_from_uploaded_PDF':True,'parent_display_caps_used_for_geometry':False,'parent_status':parent['status'],'parent_display_omissions':copy.deepcopy(parent['coverage']['omitted_due_to_limits']),
            'source_linear_raster_context_complete':not budget_exceeded,'context_geometry_input_complete':not budget_exceeded and not curve_scope['compound_curve_primitives_not_interpreted'],'curve_inspection_scope':curve_scope,'architectural_understanding_complete':False,'reasons':coverage_reasons,'source_counts':counts_before,'native_context_clipped_lines':context_clips,
            'diagonal_source_primitive_ids':diagonal,'evidence_registry':{'raw_context_registry_count':raw_registry_count,'exported_evidence_closure_count':len(registry),'unreferenced_primitives_not_serialized':raw_registry_count-len(registry),'inference_inputs_omitted':0,'meaning':'Export merged/evidence dependency closure; source PDF can reproduce unreferenced primitives.'},'curves_context':sum(o['observation_type']=='PdfCurve' for o in registry),'arcs_complete_context':len(arcs),'display_cap_applied_to_inference':False},
        'audit':{'thickness_distribution':distribution,'candidate_counts':dict(Counter(e['element_type'] for e in entities)),'classification_counts':dict(Counter(e['geometry_properties']['classification'] for e in entities)),
            'filter_decisions_crop_coordinates':filter_audits,'withheld_band_hypotheses_crop_coordinates':rejected,'door_attempts_crop_coordinates':attempts,
            'annotation_policy':'Selected uncertain bands retained as Unknown. Nearby numeric OCR alone is never an architectural deletion decision. Existing PoC door filtering limitations remain visible.'},
        'policy':{'all_entities_PROPOSED':True,'code_mapping_performed':False,'space_completion_performed':False,'alignment_performed':False,'change_detection_performed':False,'VLM_run':False},'runtime_seconds':time.perf_counter()-tick}
    if understanding:
        from backend.reference_model import build_reference_model
        result['reference_model' if role=='Ref' else 'target_model'] = build_reference_model(result,parent,expected_role=role)
    return result
