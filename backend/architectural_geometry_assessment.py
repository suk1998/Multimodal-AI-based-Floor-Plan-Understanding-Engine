"""Evidence screening of observed geometry, never stable/verified architecture.

Same-input vector/raster agreement is dependent evidence. Parent walls keep
separate observed fragments; inferred gaps and functional zones are not walls.
"""
from bisect import bisect_left,bisect_right
from collections import Counter
from poc.space_grounding_geometry import axis_segment
from integrations.fpue_ontology import understanding_contract_v0_4 as ontology

VERSION='architectural_geometry_assessment/1'


def layer_hint(layer):
    tail=(layer or '').split('|')[-1].upper()
    if any(x in tail for x in ['DIM','CEN','HID','SHE','A-TE','실명']):return 'ANNOTATION_HINT'
    if any(x in tail for x in ['FUR','TOI']):return 'FURNITURE_FIXTURE_HINT'
    if 'A-FIN2' in tail:return 'SURFACE_PATTERN_HINT'
    if 'A-FIN' in tail:return 'FINISH_OR_WALL_FACE_HINT'
    if any(x in tail for x in ['WIN','DOR','VENT']):return 'OPENING_FRAME_HINT'
    if 'WAL' in tail:return 'WALL_LAYER_HINT'
    return 'UNKNOWN_LAYER'


class VectorIndex:
    def __init__(self,observations):
        self.rows={'H':[],'V':[]}
        for o in observations:
            if o['observation_type']!='PdfLine':continue
            p=o['geometry_native']['points_native'];ax=axis_segment(p)
            if not ax:continue
            axis,n,start,end=ax;source=o['source_evidence']
            self.rows[axis].append({'normal':n,'start':start,'end':end,'id':o['observation_id'],
                'primitive_id':source.get('source_primitive_id'),'layer':source.get('layer',''),
                'hint':layer_hint(source.get('layer',''))})
        for axis in self.rows:self.rows[axis].sort(key=lambda r:r['normal'])
        self.normals={axis:[r['normal'] for r in rows] for axis,rows in self.rows.items()}

    def match(self,segment,tolerance,minimum_overlap):
        ax=axis_segment(segment)
        if not ax:return []
        axis,n,start,end=ax;rows=self.rows[axis];normals=self.normals[axis]
        lo,hi=bisect_left(normals,n-tolerance),bisect_right(normals,n+tolerance)
        result=[]
        for row in rows[lo:hi]:
            fraction=max(0,min(end,row['end'])-max(start,row['start']))/max(end-start,1e-9)
            if fraction>=minimum_overlap:result.append({**row,'overlap_fraction':fraction,
                'semantics':'coincident source style hint, not independent confirmation or architectural truth'})
        return result


def assess_geometry(binding,entities,evidence,observations,settings):
    index=VectorIndex(observations);proofs={e['evidence_id']:e for e in evidence};parents=[];openings=[];installations=[];relations=[];requests=[]
    prefix=binding['frame_id']+':structural:'+binding['analysis_run_id']
    minimum_overlap=settings.min_pair_overlap;tolerance=settings.pixel_tolerance
    def relation(kind,a,b,refs,status='PROPOSED'):
        relations.append({'relation_id':prefix+':relation:'+str(len(relations)),'relation_type':kind,
            'source_id':a,'target_id':b,'source_role':binding['source_role'],'frame_id':binding['frame_id'],
            'evidence_refs':refs,'claim_status':'PROPOSED','admissibility':status,'confidence':None})
    for entity in entities:
        props=entity['geometry_properties'];kind=entity['element_type']
        if kind!='Wall':continue
        fragments=[]
        for i,part in enumerate(props.pop('observed_wall_fragments',[])):
            matches=[index.match(face,tolerance,minimum_overlap) for face in part['faces_native']]
            hints=sorted({m['hint'] for group in matches for m in group})
            negative=[m for group in matches for m in group if m['hint'] in ['ANNOTATION_HINT','FURNITURE_FIXTURE_HINT','SURFACE_PATTERN_HINT']]
            reasons=[]
            if negative:reasons.append('COINCIDENT_VECTOR_NONSTRUCTURAL_STYLE_HINT_REQUIRES_REVIEW')
            if len(part['faces_native'])!=2:reasons.append('PAIRED_FACES_MISSING')
            if min(part['raster_support'])<settings.min_visibility:reasons.append('INSUFFICIENT_VISIBLE_FACE_SUPPORT')
            if not part['endpoint_contact_ids']:reasons.append('NO_OBSERVED_JUNCTION_SUPPORT')
            if part['context_truncated']:reasons.append('ANALYSIS_WINDOW_CUT_NOT_PHYSICAL_TERMINATION')
            if part['interior_text_ids']:reasons.append('TEXT_OR_ANNOTATION_INSIDE_BAND')
            # A CAD wall name alone never admits a fragment. Untagged raster
            # candidates can pass the same pair/visibility/contact constraints.
            decision='HOLD' if reasons else 'ADMISSIBLE_GEOMETRIC_HYPOTHESIS'
            fragment={'fragment_id':entity['entity_id']+':fragment:'+str(i),'source_role':binding['source_role'],
                'frame_id':binding['frame_id'],'parent_structure_id':entity['entity_id'],'segment_id':part['segment_id'],
                'occupancy_polygon_native':part['polygon_native'],'boundary_faces_native':part['faces_native'],
                'centerline_native':part['centerline_native'],'centerline_is_observed_wall_face':False,
                'face_side_assignments':['UNKNOWN','UNKNOWN'],'thickness_px':part['thickness_px'],
                'thickness_semantics':'paired-face separation hypothesis, not material specification',
                'observed_interval_endpoints_native':part['centerline_native'],'physical_endpoints_status':'UNKNOWN',
                'interval_arbitration_cut':part['arbitration_cut'],'boundary_truncated':part['context_truncated'],
                'source_observation_refs':part['observation_refs'],'raster_support':part['raster_support'],
                'endpoint_contact_ids':part['endpoint_contact_ids'],'vector_style_matches':matches,
                'layer_hypotheses':hints,'decision':decision,'reasons':reasons,'claim_status':'PROPOSED'}
            fragments.append(fragment)
            # All cited vector observations must survive the export closure.
            for group in matches:
                for match in group:
                    if match['id'] not in entity['observation_refs']:
                        entity['observation_refs'].append(match['id'])
            for m in negative:
                for ref in entity['evidence_refs']:
                    proofs[ref]['contradicting'].append({'kind':'COINCIDENT_VECTOR_SOURCE_STYLE','flags':[m['hint']],
                        'source_observation_refs':[m['id']],'fragment_id':fragment['fragment_id'],'layer':m['layer'],
                        'style_is_not_sufficient_for_architectural_rejection':True})
        eligible=[f for f in fragments if f['decision']=='ADMISSIBLE_GEOMETRIC_HYPOTHESIS']
        decision='ADMISSIBLE_GEOMETRIC_HYPOTHESIS' if fragments and len(eligible)==len(fragments) else 'PARTIAL_GEOMETRIC_SUPPORT' if eligible else 'HOLD'
        props['architectural_admissibility']={'version':VERSION,'decision':decision,
            'eligible_as_whole_physical_barrier':bool(fragments) and len(eligible)==len(fragments),
            'eligible_fragment_ids':[f['fragment_id'] for f in eligible],'architectural_type_verified':False}
        parents.append({'parent_structure_id':entity['entity_id'],'source_role':binding['source_role'],
            'frame_id':binding['frame_id'],'parent_kind':'WALL_HYPOTHESIS','claim_status':'PROPOSED',
            'decision':decision,'fragments':fragments,'unobserved_intervals_filled':False,
            'parent_identity_is_not_geometric_invariance':True})
        if decision!='ADMISSIBLE_GEOMETRIC_HYPOTHESIS':entity['evidence_status']='REVIEW_REQUIRED'
    parent_map={p['parent_structure_id']:p for p in parents}
    for entity in entities:
        props=entity['geometry_properties']
        if 'opening_width_px' not in props:continue
        proof=[proofs[r] for r in entity['evidence_refs']]
        support=[s for p in proof for s in p['supporting']]
        bracket=next((s for s in support if s['kind']=='GAP_BRACKETS_TRACKS'),{})
        hosts=list(dict.fromkeys(props.get('host_wall_candidate_ids',[])));reasons=[]
        if props.get('gap_status')=='REJECTED_SOLID_OVERLAP':reasons.append('SOLID_OCCUPANCY_CONTRADICTS_GAP')
        if props.get('installation_shape_only'):reasons.append('INSTALLATION_DRAWING_DOES_NOT_ESTABLISH_PHYSICAL_WALL_GAP')
        if not hosts or any(h not in parent_map for h in hosts):reasons.append('WALL_HOST_NOT_RESOLVED')
        elif not all(parent_map[h]['decision']=='ADMISSIBLE_GEOMETRIC_HYPOTHESIS' for h in hosts):reasons.append('HOST_WALL_GEOMETRY_NOT_ADMISSIBLE')
        if len(bracket.get('cap_visibility',[]))!=2 or min(bracket.get('cap_visibility',[0]))<settings.min_visibility:reasons.append('BOTH_BRACKETS_NOT_VISUALLY_SUPPORTED')
        if entity['boundary_truncated']:reasons.append('CONTEXT_CUT_OPENING_NOT_COMPLETE')
        decision='CONTRADICTED' if 'SOLID_OCCUPANCY_CONTRADICTS_GAP' in reasons else 'HOLD' if reasons else 'ADMISSIBLE_OPENING_GEOMETRY'
        identity=entity['entity_id']+':opening'
        opening={'opening_id':identity,'source_geometry_entity_id':entity['entity_id'],'source_role':binding['source_role'],
            'frame_id':binding['frame_id'],'element_type':'Opening','geometry_native':entity['geometry_native'],
            'opening_segment_native':props['opening_segment_native'],'host_parent_ids':[h for h in hosts if h in parent_map],'unresolved_host_ids':[h for h in hosts if h not in parent_map],
            'width_px':props['opening_width_px'],'decision':decision,'reasons':reasons,'evidence_refs':entity['evidence_refs'],
            'claim_status':'PROPOSED','traversability':'UNKNOWN','logical_partition_created':False}
        if props.get('installation_shape_only'):
            opening.update(installation_shape_only=True,physical_wall_gap_status='UNKNOWN')
        openings.append(opening)
        props['architectural_admissibility']={'version':VERSION,'decision':decision,'architectural_type_verified':False,
            'eligible_as_whole_physical_barrier':False,'opening_geometry_admissible':decision=='ADMISSIBLE_OPENING_GEOMETRY'}
        for h in opening['host_parent_ids']:relation('hosts',h,identity,entity['evidence_refs'],decision)
        swing=[s for s in support if s['kind']=='SWING_LEAF_AND_BOTH_JAMBS']
        types=[]
        if entity['element_type']=='Door' and swing:types=['Door']
        elif props.get('classification')=='WINDOW_OR_SLIDING_TRACK':types=props.get('installation_type_alternatives',['Window','SlidingDoor'])
        if types:
            installation={'installation_id':entity['entity_id']+':installation','source_geometry_entity_id':entity['entity_id'],
                'source_role':binding['source_role'],'frame_id':binding['frame_id'],'type_candidates':types,
                'type_assignment_status':'GEOMETRIC_TYPE_HYPOTHESIS' if len(types)==1 else 'UNRESOLVED_ALTERNATIVES',
                'opening_id':identity,'geometry_native':entity['geometry_native'],'evidence_refs':entity['evidence_refs'],
                'claim_status':'PROPOSED','decision':decision,'not_a_window_code_association':True}
            if props.get('installation_shape_only'):
                installation.update(installation_shape_only=True,physical_wall_gap_status='UNKNOWN',
                                    recovery_method=props['recovery_method'])
            installations.append(installation);relation('installedIn',installation['installation_id'],identity,entity['evidence_refs'],decision)
        if decision!='ADMISSIBLE_OPENING_GEOMETRY':entity['evidence_status']='REVIEW_REQUIRED'
    for entity in entities:
        props=entity['geometry_properties'];decision=props.get('architectural_admissibility',{}).get('decision')
        if decision not in ['ADMISSIBLE_GEOMETRIC_HYPOTHESIS','ADMISSIBLE_OPENING_GEOMETRY']:
            requests.append({'request_id':prefix+':review:'+str(len(requests)),'subject_id':entity['entity_id'],
                'source_role':binding['source_role'],'frame_id':binding['frame_id'],'bbox_native':entity['geometry_native']['bbox_native'],
                'ontology_profile':'GEOMETRIC','question':'LOCATE_OPENING_AND_HOST' if 'opening_width_px' in props else 'WALL_VS_DIMENSION_OR_FURNITURE',
                'status':'PENDING_EVIDENCE','executed':False,'decision':decision or 'HOLD','evidence_refs':entity['evidence_refs']})
    settings_data={'version':VERSION,'pixel_tolerance':tolerance,'min_visibility':settings.min_visibility,'min_pair_overlap':minimum_overlap}
    return {'schema_version':'architectural_geometry_assessment/1','source_binding':binding,'source_role':binding['source_role'],
        'frame_id':binding['frame_id'],'settings':settings_data,'ontology_contract':{'version':ontology.VERSION,'sha256':ontology.contract_digest()},
        'structural_parents':parents,'openings':openings,'installed_elements':installations,'relations':relations,'review_requests':requests,
        'summary':{'parent_decisions':dict(Counter(p['decision'] for p in parents)),
            'fragment_decisions':dict(Counter(f['decision'] for p in parents for f in p['fragments'])),
            'opening_decisions':dict(Counter(o['decision'] for o in openings)),
            'installation_type_hypotheses':dict(Counter(t for e in installations for t in e['type_candidates'])),
            'verified_architectural_elements':0},
        'policy':{'claim_status':'PROPOSED','dependent_vector_raster_support':True,'alignment_stability_assessed':False,
            'functional_zones_require_separate_semantic_evidence':True,'circulation_does_not_create_a_corridor_room':True,
            'living_kitchen_functional_divider_is_not_a_wall':True,'residual_area_is_not_automatically_living':True,
            'no_space_polygons_or_connections_created':True}}
