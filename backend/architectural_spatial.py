"""Spatial bundle adapter using the preserved PoC barrier/seed/region engine.

Geometry hypotheses are inputs, never GT. No raw-ink flood fill, invented
closure, room template, compass direction or cross-document correspondence.
"""
import copy,math,time,unicodedata,uuid
from collections import Counter,defaultdict
import cv2,numpy as np
from backend.architectural_observations import digest,bbox,offset,intersects,MAX_PIXELS
from backend.spatial_ontology import SpatialReasoner
from integrations.fpue_ontology.vocabulary import BASE_SPACE_TERMS, resolve_space_term
from poc.space_grounding_geometry import (Settings,barrier_masks,raster_observations,
    seed_for_label,component_polygon,component_is_closed,partial_boundaries,axis_points)

SPACE_NAMES=tuple(term[0] for term in BASE_SPACE_TERMS)
STATES=('CLOSED_CANDIDATE','PARTIAL_BOUNDARY','OPEN_BOUNDARY','AMBIGUOUS','UNRESOLVED')
VERSION='uploaded_roi_spatial_adapter/2-ontology'


def normalize_space_name(text):
    term=resolve_space_term(text)
    return term[0] if term else None


def validate_geometry_parent(binding,parent):
    if parent.get('schema_version')!='architectural_geometry_bundle/1' or parent.get('bundle_kind')!='GEOMETRIC_ENTITY_CANDIDATES':raise ValueError('Completed Geometry bundle required')
    if parent.get('status') not in ['COMPLETED','PARTIAL']:raise ValueError('Geometry not completed')
    keys=['source_role','source_sha256','page','extraction_zoom','image_id','frame_id','width','height','image_sha256','core_bbox_native','context_bbox_native','document_id']
    if any(binding.get(k)!=parent.get('source_binding',{}).get(k) for k in keys):raise ValueError('Geometry PDF/ROI/native frame mismatch')
    if any(e['source_role']!=binding['source_role'] or e['frame_id']!=binding['frame_id'] for e in parent['entities']):raise ValueError('Mixed Geometry frames')
    if any(o['source_role']!=binding['source_role'] or o['frame_id']!=binding['frame_id'] for o in parent['observations']):raise ValueError('Mixed observation frames')


def polygon_measures(polygon):
    """Validate raster-derived polygon rings and report native pixel area only."""
    if not polygon or not polygon.get('outer_rings'):return None
    outer=polygon['outer_rings'];holes=polygon.get('holes',[])
    for ring in outer+holes:
        p=np.asarray(ring,float)
        if p.ndim!=2 or p.shape[1]!=2 or len(p)<3 or not np.isfinite(p).all() or len(set(map(tuple,p)))<3:return None
        # Reject a proper nonadjacent edge crossing (e.g. an invalid bow tie).
        def cross(a,b,c):return float((b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0]))
        for i in range(len(p)):
            a,b=p[i],p[(i+1)%len(p)]
            for j in range(i+2,len(p)):
                if (j+1)%len(p)==i:continue
                c,d=p[j],p[(j+1)%len(p)]
                if cross(a,b,c)*cross(a,b,d)<-1e-9 and cross(c,d,a)*cross(c,d,b)<-1e-9:return None
    areas=[];centers=[]
    for rings,sign in [(outer,1),(holes,-1)]:
        for ring in rings:
            m=cv2.moments(np.asarray(ring,np.float32));area=abs(m['m00'])
            if area<=0:return None
            areas.append(sign*area);centers.append([m['m10']/m['m00'],m['m01']/m['m00']])
    area=sum(areas)
    if area<=0:return None
    return {'area_native_px2':float(area),'centroid_native':np.average(centers,axis=0,weights=areas).tolist(),'bbox_native':bbox([p for ring in outer for p in ring])}


def analyze_spatial(pdf_bytes,image_png,binding,geometry):
    started=time.perf_counter();validate_geometry_parent(binding,geometry)
    if digest(pdf_bytes)!=binding['source_sha256'] or digest(image_png)!=binding['image_sha256']:raise ValueError('Uploaded source bytes differ')
    im=cv2.imdecode(np.frombuffer(image_png,np.uint8),cv2.IMREAD_COLOR)
    if im is None or im.shape[:2]!=(binding['height'],binding['width']):raise ValueError('Native raster dimensions differ')
    x0,y0,x1,y1=context=binding['context_bbox_native'];core=binding['core_bbox_native'];shape=(y1-y0,x1-x0)
    if not(0<=x0<x1<=binding['width'] and 0<=y0<y1<=binding['height']) or shape[0]*shape[1]>MAX_PIXELS:raise ValueError('Invalid context')
    crop=im[y0:y1,x0:x1]
    if digest(cv2.imencode('.png',crop)[1].tobytes())!=geometry['analysis_scope']['input_crop_sha256']:raise ValueError('Spatial crop mismatch')
    role=binding['source_role'];run=binding.get('analysis_run_id') or uuid.uuid4().hex;prefix=f'{binding["frame_id"]}:spatial:{run}'
    settings=Settings();texts=[o for o in geometry['observations'] if o['observation_type']=='TextRegion']
    heights=[bbox(o['geometry_native']['points_native'])[3]-bbox(o['geometry_native']['points_native'])[1] for o in texts]
    height=float(np.median(heights)) if heights else max(8,binding['height']/100)
    raster=raster_observations(cv2.cvtColor(crop,cv2.COLOR_BGR2GRAY),[offset(o['geometry_native']['points_native'],-x0,-y0) for o in texts],height,settings)
    by_id={e['entity_id']:e for e in geometry['entities']};by_ev={e['evidence_id']:e for e in geometry['evidence']}
    reasoner=SpatialReasoner({**binding,'analysis_run_id':run},geometry['entities'],geometry['evidence'],geometry['observations'])
    walls=[];openings=[];decisions=[];caution_walls=set();fixture_polygons=[]
    def own_evidence(e):return [by_ev[k] for k in e['evidence_refs'] if k in by_ev]
    for entity in geometry['entities']:
        identity=entity['entity_id'];kind=entity['element_type'];props=entity['geometry_properties'];proofs=own_evidence(entity)
        cautions=[c for proof in proofs for c in proof['contradicting']]
        flags=[flag for c in cautions for flag in c.get('flags',[])]
        nonstructural=any(any(word in f for word in ['DIMENSION','FURNITURE','FIXTURE','TEXT_OR_LABEL']) for f in flags)
        ontology_eligibility=reasoner.decisions[identity]
        nonstructural=nonstructural or not ontology_eligibility['eligible']
        # A coincident CAD style on a single face cannot identify the entire parent as furniture.
        fixture_flags=[flag for c in cautions if c.get('kind')!='COINCIDENT_VECTOR_SOURCE_STYLE' for flag in c.get('flags',[])]
        if any('FURNITURE' in f or 'FIXTURE' in f for f in fixture_flags):fixture_polygons+=entity['geometry_native'].get('polygons_native',[])
        decision={'geometry_id':identity,'element_type':kind,'geometry_evidence_refs':entity['evidence_refs'],'supporting_evidence':[p['supporting'] for p in proofs],'contradicting_evidence':cautions,'role_in_spatial_analysis':'NOT_A_PHYSICAL_BARRIER','reason':'Unknown/Opening is not automatically a wall or a separator'}
        decision['ontology_eligibility']=ontology_eligibility
        if kind=='Wall' and not nonstructural:
            decision.update(role_in_spatial_analysis='CONDITIONAL_PHYSICAL_BAND',reason='Proposed band polygon, not a verified wall')
            if cautions:caution_walls.add(identity)
            for n,polygon in enumerate(entity['geometry_native'].get('polygons_native',[])):
                b=bbox(offset(polygon,-x0,-y0));axis=props.get('orientation')
                if axis not in ['H','V']:continue
                start,end=(b[0],b[2]) if axis=='H' else (b[1],b[3]);n1,n2=(b[1],b[3]) if axis=='H' else (b[0],b[2])
                if end<=start or n2<=n1:continue
                piece={'axis':axis,'normal':(n1+n2)/2,'start':start,'end':end,'thickness_px':n2-n1,'polygon':offset(polygon,-x0,-y0),'observation_id':identity}
                walls.append({'id':identity+f':piece:{n}','geometry_id':identity,'axis':axis,'normal':piece['normal'],'thickness_px':piece['thickness_px'],'pieces':[piece]})
        elif kind=='Wall':decision['reason']='Whole wall withheld; consult the recorded geometry eligibility and fragment evidence'
        if kind=='Wall' and props.get('orientation') not in ['H','V']:
            decision.update(role_in_spatial_analysis='NOT_A_PHYSICAL_BARRIER',reason='UNSUPPORTED_DIAGONAL_BAND_IN_PRESERVED_ENGINE; no axial wall fabricated')
        if kind=='Door':
            door_proofs=[p for proof in proofs for p in proof['supporting'] if p.get('kind')=='SWING_LEAF_AND_BOTH_JAMBS']
            jambs=any(len(p.get('jamb_connections',[]))==2 and all(j.get('connected') for j in p['jamb_connections']) and p.get('curve_visibility',0)>=.8 and any(l.get('visible_fraction',0)>=.8 for l in p.get('leaf_candidates',[])) for p in door_proofs)
            segment=props.get('opening_segment_native');hosts=props.get('host_wall_candidate_ids',[])
            host_assessment=reasoner.host_assessment(entity)
            decision['ontology_host_assessment']=host_assessment
            if jambs and reasoner.door_support(entity) and not cautions and segment and hosts and host_assessment['eligible'] and props.get('gap_status')!='REJECTED_SOLID_OVERLAP':
                openings.append({'id':identity,'entity_id':identity,'origin':'POC2_OBSERVED_GAP','eligible_logical_boundary':True,'logical_segment':offset(segment,-x0,-y0),'host_wall_ids':hosts,'geometry_id':identity,'separator_kind':'CONDITIONAL_DOOR_SEPARATOR_NOT_SOLID'})
                decision.update(role_in_spatial_analysis='CONDITIONAL_DOOR_SEPARATOR',reason='Observed swing/leaf/both jambs; logical spatial separation, not a physical solid wall')
            else:decision['reason']='Door geometric support or ontology Host/jamb/frame evidence incomplete; no invented closing line'
        if kind=='Unknown' and props.get('classification')=='WINDOW_OR_SLIDING_TRACK':
            tracks=[p for proof in proofs for p in proof['supporting'] if p.get('kind')=='GAP_BRACKETS_TRACKS']
            supported=any(len(p.get('tracks_native',[]))>=3 and len(p.get('cap_visibility',[]))==2 and min(p['cap_visibility'])>=.8 for p in tracks)
            segment=props.get('opening_segment_native');hosts=props.get('host_wall_candidate_ids',[])
            track_host=reasoner.host_assessment(entity)
            decision['ontology_host_assessment']=track_host
            if supported and not cautions and segment and hosts and track_host['eligible'] and props.get('gap_status')!='REJECTED_SOLID_OVERLAP':
                openings.append({'id':identity,'entity_id':identity,'origin':'POC2_OBSERVED_GAP','eligible_logical_boundary':True,'logical_segment':offset(segment,-x0,-y0),'host_wall_ids':hosts,'geometry_id':identity,'separator_kind':'CONDITIONAL_TRACK_SEPARATOR_TYPE_UNKNOWN_NOT_SOLID'})
                decision.update(role_in_spatial_analysis='CONDITIONAL_TRACK_SEPARATOR_TYPE_UNKNOWN',reason='Track/end-cap/host evidence supports a conditional separator; window/sliding subtype remains Unknown')
        decisions.append(decision)
    # Preserved engine: actual band polygons + supported logical thresholds.
    barriers=barrier_masks(copy.deepcopy(walls),copy.deepcopy(openings),shape)
    physical=barrier_masks(copy.deepcopy(walls),[],shape)
    cautious=barrier_masks(copy.deepcopy([w for w in walls if w['geometry_id'] not in caution_walls]),copy.deepcopy(openings),shape) if caution_walls else barriers
    wall_map={w['id']:w for w in walls};labels=[];by_component=defaultdict(list)
    seed_limit=128;omitted_unlabeled=[];evidence=[];spaces=[];component_cache={}
    def to_native(points):return offset(points,x0,y0)
    def inside_core(p):return core[0]<=p[0]<=core[2] and core[1]<=p[1]<=core[3]
    def on_fixture(p):return any(cv2.pointPolygonTest(np.asarray(poly,np.float32),tuple(map(float,p)),False)>=0 for poly in fixture_polygons)
    for text in texts:
        name=normalize_space_name(text['source_evidence'].get('text',''))
        if name is None:continue
        poly=text['geometry_native']['points_native'];center=np.mean(poly,axis=0).tolist();seed=None;error=None
        if len(labels)>=seed_limit:error='SEED_WORK_BUDGET_EXCEEDED'
        elif on_fixture(center):error='TEXT_CENTER_IN_FURNITURE_FIXTURE_HYPOTHESIS_NO_INTERIOR_SEED_ASSUMED'
        else:
            seed,error=seed_for_label(offset(poly,-x0,-y0),barriers,raster,height)
            if seed and on_fixture(to_native([seed['point']])[0]):seed=None;error='SEED_IN_FURNITURE_FIXTURE_HYPOTHESIS'
        item={'space_name':name,'raw_text':text['source_evidence']['text'],'text_observation_id':text['observation_id'],'ocr_polygon_native':copy.deepcopy(poly),'ocr_bbox_native':bbox(poly),'text_center_native':center,'ocr_confidence':text.get('confidence'),'seed':seed,'seed_failure':error}
        labels.append(item)
        if seed:by_component[seed['component_id']].append(item)
    minimum=height*height*settings.min_closed_area_text_height_sq
    clearance=cv2.distanceTransform(255-barriers['total'],cv2.DIST_L2,5)
    def component_summary(component):
        if component in component_cache:return component_cache[component]
        stats=barriers['component_stats'][component];a,b,w,h,area=map(int,stats);mask=(barriers['components']==component).astype(np.uint8)
        contact=(cv2.dilate(mask,np.ones((3,3),np.uint8))>0)&(mask==0)
        owners=sorted(int(i) for i in np.unique(barriers['wall_owner'][contact]) if i)
        separator_indices=sorted(int(i) for i in np.unique(barriers['opening_owner'][contact]) if i)
        boundary_segments=[];geometry_refs=set()
        for index in owners:
            wall=walls[index-1];geometry_refs.add(wall['geometry_id']);ys,xs=np.where(contact&(barriers['wall_owner']==index))
            if not len(xs):continue
            piece=wall['pieces'][0];axis=wall['axis'];axials=xs if axis=='H' else ys;normals=ys if axis=='H' else xs
            for sign in [-1,1]:
                side=normals<piece['normal'] if sign<0 else normals>piece['normal']
                if not np.any(side):continue
                lo=max(piece['start'],float(axials[side].min()));hi=min(piece['end'],float(axials[side].max()))
                if hi-lo<1:continue
                face=piece['normal']+sign*piece['thickness_px']/2
                boundary_segments.append({'geometry_id':wall['geometry_id'],'points_native':to_native(axis_points(axis,face,lo,hi)),'kind':'OBSERVED_BAND_FACE_SUBSET','claim_status':'PROPOSED','ends_are_not_physical_wall_endpoints':True})
        for i in separator_indices:
            opening=openings[i-1];geometry_refs.add(opening['geometry_id']);boundary_segments.append({'geometry_id':opening['geometry_id'],'points_native':to_native(opening['logical_segment']),'kind':opening['separator_kind'],'claim_status':'PROPOSED'})
        edge=bool(a==0 or b==0 or a+w>=shape[1] or b+h>=shape[0]);extent=[a+x0,b+y0,a+w-1+x0,b+h-1+y0]
        core_cut=not(core[0]<=extent[0] and core[1]<=extent[1] and extent[2]<=core[2] and extent[3]<=core[3])
        value={'component_id':component,'area_raster_cells':area,'context_edge_contact':edge,'core_cut':core_cut,'extent_native':extent,'boundary_segments':boundary_segments,'geometry_refs':sorted(geometry_refs),'component_is_closed':component_is_closed(stats,shape,minimum)}
        component_cache[component]=value;return value
    def make(label,seed,unlabeled=False):
        component=seed['component_id'] if seed else None;summary=component_summary(component) if component else None;reasons=[];status='UNRESOLVED';polygon=None;measures=None;rays=[]
        seed_native=to_native([seed['point']])[0] if seed else None;boundary_segments=copy.deepcopy(summary['boundary_segments']) if summary else [];geometry_refs=list(summary['geometry_refs']) if summary else []
        siblings=by_component.get(component,[]);names=[s['space_name'] for s in siblings]
        if label and label['seed_failure']:reasons.append(label['seed_failure'])
        alternative_diff=False;physical_component=None;physical_names=[]
        if seed:
            if summary['context_edge_contact']:reasons.append('SPACE_REACHES_CONTEXT_EDGE_NO_CLOSURE_INVENTED')
            if summary['core_cut']:reasons.append('SPACE_EXTENDS_OUTSIDE_CORE_ROI_NOT_CLOSED_AT_ROI_EDGE')
            sx,sy=seed['point'];physical_component=int(physical['components'][sy,sx]);physical_names=[l['space_name'] for l in labels if l['seed'] and int(physical['components'][l['seed']['point'][1],l['seed']['point'][0]])==physical_component]
            if caution_walls:
                alternate=int(cautious['components'][sy,sx]);base_stats=barriers['component_stats'][component];alt_stats=cautious['component_stats'][alternate]
                alternative_diff=not np.array_equal(base_stats,alt_stats)
            if len(siblings)>1:
                status='AMBIGUOUS';reasons.append('MULTIPLE_OCR_LABELS_SHARE_ONE_COMPONENT_POSSIBLE_MISSING_WALL_OR_UNSUPPORTED_SEPARATOR')
            elif alternative_diff:
                status='AMBIGUOUS';reasons.append('REGION_DEPENDS_ON_GEOMETRY_WITH_CONTRADICTING_EVIDENCE')
            elif summary['context_edge_contact']:
                status='PARTIAL_BOUNDARY' if boundary_segments else 'OPEN_BOUNDARY';reasons.append('SPACE_REACHES_CONTEXT_EDGE_NO_CLOSURE_INVENTED')
            elif summary['core_cut']:
                status='PARTIAL_BOUNDARY';reasons.append('SPACE_EXTENDS_OUTSIDE_CORE_ROI_NOT_CLOSED_AT_ROI_EDGE')
            elif not summary['component_is_closed']:
                status='PARTIAL_BOUNDARY' if boundary_segments else 'UNRESOLVED';reasons.append('GEOMETRY_COMPONENT_TOO_SMALL_FOR_EXISTING_SPACE_CRITERIA')
            else:
                local_polygon=component_polygon(component,barriers)
                if local_polygon:
                    polygon={**local_polygon,'outer_rings':[to_native(r) for r in local_polygon['outer_rings']],'holes':[to_native(r) for r in local_polygon['holes']]};measures=polygon_measures(polygon)
                if measures:status='CLOSED_CANDIDATE'
                else:polygon=None;status='UNRESOLVED';reasons.append('POLYGON_RECONSTRUCTION_INVALID_OR_EMPTY')
            if status!='CLOSED_CANDIDATE':
                # Preserved boundary search adds local evidence, never a closing box.
                ids,rays=partial_boundaries(seed,walls,barriers,settings)
                distances=[float(np.linalg.norm(np.asarray(r['hit_native'])-seed['point'])) for r in rays if r.get('wall_id')]
                cutoff=max(height*3,min(distances)*settings.partial_boundary_local_distance_factor) if distances else 0
                local_ids={r['wall_id'] for r in rays if r.get('wall_id') and np.linalg.norm(np.asarray(r['hit_native'])-seed['point'])<=cutoff}
                # A large open component's whole perimeter is not this label's boundary.
                boundary_segments=[]
                for key in local_ids:
                    wall=wall_map[key];piece=wall['pieces'][0];axis=wall['axis'];ax=0 if axis=='H' else 1;normal=1-ax
                    hits=np.asarray([r['hit_native'] for r in rays if r.get('wall_id')==key and r.get('hit_native') is not None and np.linalg.norm(np.asarray(r['hit_native'])-seed['point'])<=cutoff])
                    if not len(hits):continue
                    lo=max(piece['start'],float(hits[:,ax].min()));hi=min(piece['end'],float(hits[:,ax].max()))
                    if hi-lo<1:continue
                    sign=-1 if float(np.median(hits[:,normal]))<piece['normal'] else 1
                    face=piece['normal']+sign*piece['thickness_px']/2
                    boundary_segments.append({'geometry_id':wall['geometry_id'],'points_native':to_native(axis_points(axis,face,lo,hi)),'kind':'SAMPLED_LOCAL_BAND_FACE','claim_status':'PROPOSED','ends_are_not_physical_wall_endpoints':True})
                for index,opening in enumerate(openings,1):
                    if any(r.get('opening_index')==index and r.get('hit_native') is not None and np.linalg.norm(np.asarray(r['hit_native'])-seed['point'])<=max(cutoff,height*3) for r in rays):
                        boundary_segments.append({'geometry_id':opening['geometry_id'],'points_native':to_native(opening['logical_segment']),'kind':opening['separator_kind'],'claim_status':'PROPOSED'})
                geometry_refs=sorted(set(s['geometry_id'] for s in boundary_segments))
                if summary['context_edge_contact'] and not boundary_segments and status!='AMBIGUOUS':status='OPEN_BOUNDARY'
                for ray in rays:
                    if ray.get('hit_native') is not None:ray['hit_native']=to_native([ray['hit_native']])[0]
                    if ray.get('wall_id'):ray['geometry_id']=wall_map[ray.pop('wall_id')]['geometry_id']
        if unlabeled:reasons.append('NO_WHITELIST_OCR_NAME_UNLABELED_GEOMETRY_SPACE_HYPOTHESIS')
        if seed and not boundary_segments:reasons.append('NO_LOCAL_BOUNDARY_GEOMETRY_ASSOCIATED')
        if any(d['geometry_id'] in geometry_refs and d['contradicting_evidence'] for d in decisions):reasons.append('BOUNDARY_GEOMETRY_CONTRADICTIONS_RETAINED')
        reasons.append('ALL_GEOMETRY_AND_SPATIAL_TYPES_UNVERIFIED_PROPOSED')
        identity=f'{prefix}:space:{len(spaces):04d}';evidence_id=identity+':evidence'
        nearby_unknown=[]
        if seed_native:
            for e in geometry['entities']:
                if e['element_type']!='Unknown':continue
                b=e['geometry_native']['bbox_native'];dist=math.hypot(max(b[0]-seed_native[0],0,seed_native[0]-b[2]),max(b[1]-seed_native[1],0,seed_native[1]-b[3]))
                if dist<=height*3:nearby_unknown.append(e['entity_id'])
        evidence.append({'evidence_id':evidence_id,'source_role':role,'frame_id':binding['frame_id'],'name_observation_refs':[label['text_observation_id']] if label else [],'geometry_refs':geometry_refs,
            'component_evidence':summary,'seed_observation':{**seed,'point':seed_native} if seed else None,'boundary_rays_native':rays,'conditional_component_labels':names,'physical_passage_component_id':physical_component,'physical_component_label_names':physical_names,
            'logical_vs_physical_semantics':'Door thresholds may separate spatial regions while physical free space remains connected; no connects/adjacentTo claim.',
            'cautious_geometry_partition_differs':alternative_diff,'nearby_unknown_geometry_ids':nearby_unknown,'cause_candidates':['POSSIBLE_MISSING_OR_WRONG_GEOMETRY_NOT_PROVEN'] if nearby_unknown or status!='CLOSED_CANDIDATE' else [],'claim_status':'PROPOSED'})
        location=measures['bbox_native'] if measures else None;points=[p for s in boundary_segments for p in s['points_native']]
        roi_hit=bool(seed_native and inside_core(seed_native)) or any(intersects(s['points_native'],core) for s in boundary_segments) or bool(location and not(location[2]<core[0] or location[0]>core[2] or location[3]<core[1] or location[1]>core[3]))
        spaces.append({'space_id':identity,'source_role':role,'frame_id':binding['frame_id'],'space_name':label['space_name'] if label else 'UNLABELED_SPACE','name_evidence_refs':[label['text_observation_id']] if label else [],
            'name_observation':{k:v for k,v in label.items() if k not in ['seed']} if label else None,'seed_native':seed_native,'polygon_native':polygon,'bbox_native':location,'centroid_native':measures['centroid_native'] if measures else None,
            'area_native_px2':measures['area_native_px2'] if measures else None,'area_square_metres':None,'boundary_geometry_refs':geometry_refs,'boundary_segments_native':boundary_segments,'boundary_status':status,'evidence_refs':[evidence_id],
            'uncertainty_reason':list(dict.fromkeys(reasons)),'claim_status':'PROPOSED','roi_intersection':roi_hit,'boundary_truncated':bool(summary and(summary['context_edge_contact'] or summary['core_cut'])),
            'boundary_details':{'context_edge_contact':bool(summary and summary['context_edge_contact']),'core_roi_crossed':bool(summary and summary['core_cut']),'ROI_edge_used_as_wall':False},
            'partial_boundary_extent_native':bbox(points) if points else None})
        term=resolve_space_term(label['space_name']) if label else None
        spaces[-1]['space_type_hypothesis']={'generic_type':term[1],'ordinal':term[2],'role':term[3],'claim_status':'PROPOSED','basis':'ontology lexicon + own OCR hypothesis; not spatial verification'} if term else None
    for label in labels:make(label,label['seed'])
    unlabeled_count=0
    for component,stats in enumerate(barriers['component_stats']):
        if component==0 or component in by_component or not component_is_closed(stats,shape,minimum):continue
        a,b,w,h,_=map(int,stats);region=barriers['components'][b:b+h,a:a+w];scores=clearance[b:b+h,a:a+w].copy();valid=(region==component)&(raster['ink'][b:b+h,a:a+w]==0)&(raster['text_mask'][b:b+h,a:a+w]==0);scores[~valid]=0
        yy,xx=np.unravel_index(np.argmax(scores),scores.shape)
        if scores[yy,xx]<height*.4:continue
        point=[int(a+xx),int(b+yy)]
        if on_fixture(to_native([point])[0]):continue
        if unlabeled_count>=256:omitted_unlabeled.append(component);continue
        seed={'point':point,'component_id':component,'clearance_to_barrier_px':float(scores[yy,xx]),'method':'preserved enclosed component + clear interior, no invented name'};make(None,seed,True);unlabeled_count+=1
    # Report partition dependencies; do not call every partition a true wall.
    partition_dependencies=[]
    for index,wall in enumerate(walls,1):
        polygon=np.rint(wall['pieces'][0]['polygon']).astype(int);a=max(0,int(polygon[:,0].min())-2);b=max(0,int(polygon[:,1].min())-2);c=min(shape[1],int(polygon[:,0].max())+3);d=min(shape[0],int(polygon[:,1].max())+3)
        if c<=a or d<=b:continue
        mask=(barriers['wall_owner'][b:d,a:c]==index).astype(np.uint8);touch=(cv2.dilate(mask,np.ones((3,3),np.uint8))>0)&(mask==0);neighbors=sorted(int(i) for i in np.unique(barriers['components'][b:d,a:c][touch]) if i)
        if len(neighbors)>1:partition_dependencies.append({'geometry_id':wall['geometry_id'],'conditional_region_ids':neighbors,'label_names_by_region':{str(i):[l['space_name'] for l in by_component.get(i,[])] for i in neighbors},'meaning':'If this Wall hypothesis is false, this partition can be artificial; this is a dependency diagnostic, not a false-wall GT verdict.'})
    counts=dict(Counter(s['boundary_status'] for s in spaces));queries=[]
    for name in ['침실1','드레스룸','팬트리']:
        own=[s for s in spaces if s['space_name']==name];queries.append({'space_name':name,'OCR_candidate_count':sum(l['space_name']==name for l in labels),'seed_count':sum(s['seed_native'] is not None for s in own),'region_polygon_count':sum(s['polygon_native'] is not None for s in own),'boundary_statuses':[s['boundary_status'] for s in own],'space_ids':[s['space_id'] for s in own],'absence_is_not_proven':True})
    partial=not spaces or geometry['status']!='COMPLETED' or any(s['boundary_status']!='CLOSED_CANDIDATE' for s in spaces) or bool(omitted_unlabeled)
    understanding=reasoner.infer(spaces,evidence,barriers,physical,context,openings)
    evidence.extend(understanding['evidence'])
    return {'schema_version':'architectural_spatial_bundle/1','bundle_kind':'SPATIAL_MODEL_CANDIDATES','run_id':run,'source_role':role,'frame_id':binding['frame_id'],'source_binding':binding,'parent_geometry_run_id':geometry['run_id'],'parent_observation_run_id':geometry['parent_observation_run_id'],
        'analysis_scope':copy.deepcopy(geometry['analysis_scope']),'frame_registry':copy.deepcopy(geometry['frame_registry']),'status':'PARTIAL' if partial else 'COMPLETED','algorithm':VERSION,
        'coordinate_system':{'name':'image_native_pixels','positive_X':'image right','positive_Y':'image down','compass_orientation':'UNKNOWN_UNVERIFIED','area_units':'native_pixel_squared','square_metres_computed':False},
        'whitelist':list(SPACE_NAMES),'spaces':spaces,'evidence':evidence,'observations':copy.deepcopy(geometry['observations']),'geometry_candidates':copy.deepcopy(geometry['entities']),'geometry_evidence':copy.deepcopy(geometry['evidence']),'relations':understanding['relations'],'claims':understanding['claims'],'ontology':understanding['ontology'],'relation_attempts':understanding['attempts'],
        'summary':{'OCR_space_name_candidates':len(labels),'space_seeds':sum(s['seed_native'] is not None for s in spaces),'labeled_seeds':sum(s['seed_native'] is not None and s['space_name']!='UNLABELED_SPACE' for s in spaces),'space_region_polygons':sum(s['polygon_native'] is not None for s in spaces),'boundary_status_counts':counts,'unlabeled_space_candidates':unlabeled_count,'queries':queries,'ontology_relation_counts':understanding['ontology']['counts'],'connection_candidates':understanding['ontology']['counts'].get('Connection',0)},
        'coverage':{'input_geometry_status':geometry['status'],'input_geometry_coverage':copy.deepcopy(geometry['coverage']),'input_text_coverage':{'text_regions':len(texts),'using_current_geometry_observations_only':True},'core_ROI_is_not_a_closing_boundary':True,'context_edge_is_unknown_not_wall':True,'omitted_unlabeled_components':omitted_unlabeled,'seed_budget':seed_limit,'unresolved_seeds_due_to_budget':sum(l['seed_failure']=='SEED_WORK_BUDGET_EXCEEDED' for l in labels)},
        'diagnostics':{'boundary_input_decisions':decisions,'partition_dependencies':partition_dependencies,'same_component_label_conflicts':[{'component_id':i,'space_names':[l['space_name'] for l in ls],'observation_refs':[l['text_observation_id'] for l in ls]} for i,ls in by_component.items() if len(ls)>1],
            'conditional_free_components':int(barriers['component_count']-1),'physical_free_components':int(physical['component_count']-1),'barrier_semantics':'band occupied polygons; only supported Door thresholds are logical separators, never every Opening/Unknown',
            'cautious_geometry_hypothesis_removed_ids':sorted(caution_walls)},
        'policy':{'all_claims_PROPOSED':not understanding['claims'],'all_entity_and_relation_semantics_PROPOSED':True,'geometry_constraints_checked':True,'GT_metrics_computed':False,'semantic_relationships_confirmed':False,'Ref_Target_alignment_performed':False,'change_detection_performed':False,'OCR_or_VLM_rerun':False},'runtime_seconds':time.perf_counter()-started}
