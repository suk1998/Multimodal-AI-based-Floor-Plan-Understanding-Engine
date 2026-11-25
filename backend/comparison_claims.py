"""Per-claim dependency assessment for existing comparison evidence.

Diagnostic statuses describe the comparison's ability to judge a claim; they
do not mutate ontology Claim states, invent objects or certify missing facts.
No gate here requires completion of the entire drawing graph.
"""
from collections import Counter
from backend.structural_alignment import fingerprint
from backend.door_change_policy import supported_drawing_evidence
from backend.dimension_comparison import is_supported_dimension_change
from integrations.fpue_ontology.detection_contract import is_supported_symbol_change

VERSION='comparison_claim_dependencies/1'
READY={'SUPPORTED_CHANGE','NO_DIFFERENCE_WITHIN_MEASURED_SCOPE'}

def fact(status,reason,refs=()):
    return dict(status=status,reason_code=reason,evidence_refs=list(dict.fromkeys(refs)))

def registration_fact(row):
    boundary=row.get('boundary_aperture_evidence') or {}
    if boundary:
        sites=boundary.get('sites',[])
        supported=bool(sites) and all(s.get('registration',{}).get('status')=='SUPPORTED_LOCAL_COORDINATE_CORRESPONDENCE' for s in sites)
        return fact('SUPPORTED' if supported else 'HOLD','INDEPENDENT_BOUNDARY_APERTURE_LOCUS_SUPPORT' if supported else 'BOUNDARY_APERTURE_COORDINATES_UNCONFIRMED')
    door=row.get('door_geometry_evidence') or {}
    opening=row.get('opening_geometry_evidence') or {}
    if door:
        p=door.get('registration',{});ok=p.get('status')=='SUPPORTED_LOCAL_COORDINATE_CORRESPONDENCE'
        return fact('SUPPORTED' if ok else 'HOLD',p.get('reason') or ('INDEPENDENT_DOOR_LOCUS_SUPPORTED' if ok else 'DOOR_LOCUS_NOT_VERIFIED'))
    if opening:
        p=opening.get('registration',{});ok=opening.get('registration_supported') is True
        return fact('SUPPORTED' if ok else 'HOLD',p.get('reason') or ('EXTERNAL_OPENING_LOCUS_SUPPORTED' if ok else 'OPENING_LOCUS_NOT_VERIFIED'))
    if is_supported_dimension_change(row):return fact('SUPPORTED','PAIRED_DIMENSION_CHAIN_COORDINATES')
    if is_supported_symbol_change(row):return fact('SUPPORTED','INDEPENDENT_PRINTED_MARKER_LOCUS')
    p=row.get('symbol_evidence') or {}
    if p.get('registration_supported') is True:return fact('SUPPORTED','EXISTING_SYMBOL_COORDINATE_EVIDENCE')
    if row.get('coordinate_support',{}).get('status')=='SUPPORTED_COMPARISON_SCOPE':
        return fact('SUPPORTED','EXISTING_STAGE9_COMPARISON_COVERAGE')
    if row.get('automatic_confirmation')=='VECTOR_WALL_END_WITH_INDEPENDENT_RASTER_SUPPORT':
        return fact('SUPPORTED','WALL_END_INDEPENDENT_RASTER_POSITION_EVIDENCE')
    return fact('HOLD','LOCAL_COORDINATE_SUPPORT_NOT_RECORDED')

def source_contexts(row,geometries,source_objects):
    out=[]
    for role,g in zip(['Ref','Target'],geometries):
        regions={o['region_id']:o for o in g.get('space_closure',{}).get('regions',[]) if o.get('region_id')}
        a=g.get('architectural_assessment',{})
        openings={o['opening_id']:o for o in a.get('openings',[])}
        installed={o['installation_id']:o for o in a.get('installed_elements',[])}
        relations=[*a.get('relations',[]),*g.get('reference_relations' if role=='Ref' else 'target_relations',{}).get('relations',[])]
        lookup={o['id']:o for o in source_objects.get(role,[])}
        for oid in row['ref_ids' if role=='Ref' else 'target_ids']:
            obj=lookup.get(oid,{});space=regions.get(oid);item=installed.get(oid)
            opening=openings.get(item['opening_id'] if item else oid)
            current=dict(source_role=role,frame_id=g['frame_id'],source_id=oid,
                catalog_geometry_supported=obj.get('supported'),
                native_observations=obj.get('native_observations',{}),relations=[])
            if space:
                runs=space.get('boundary_runs',[]);rings=space.get('polygon_native') or {}
                current['space_boundary']=dict(closed=bool(rings.get('outer_rings')),
                    inferred_runs=sum(r.get('owner',0)<0 for r in runs),claim_status=space.get('claim_status','UNSPECIFIED'))
            if item:current['installation']=dict(claim_status=item['claim_status'],decision=item['decision'],type_candidates=item['type_candidates'])
            if opening:current['opening']=dict(decision=opening['decision'],reasons=opening.get('reasons',[]),host_parent_ids=opening.get('host_parent_ids',[]))
            own_ids={oid,*([item['opening_id']] if item else [])}
            for r in relations:
                source=r.get('source_id',r.get('source',{}).get('id'));target=r.get('target_id',r.get('target',{}).get('id'))
                if not own_ids.intersection([source,target,r.get('via_entity_id')]):continue
                current['relations'].append({k:r[k] for k in ['id','relation_id','kind','relation_type','claim_status','admissibility','evidence_refs'] if k in r})
            out.append(current)
    return out

def dependencies(row,contexts):
    refs=row.get('evidence_refs',[]);both=bool(row['ref_ids'] and row['target_ids'])
    d=dict(BOTH_SOURCE_OBSERVATIONS=fact('SUPPORTED' if both and refs else 'HOLD','BOTH_SOURCES_RETAINED' if both and refs else 'MISSING_PAIRED_OBSERVATIONS',refs),
        REGISTERED_COORDINATES=registration_fact(row))
    door=row.get('door_geometry_evidence') or {};relation=door.get('relation_assessment') or {}
    identity=(supported_drawing_evidence(row) if door else row.get('identity_status') in ['SUPPORTED','SUPPORTED_AT_LOCUS'])
    d['OBJECT_CORRESPONDENCE']=fact('SUPPORTED' if identity else 'HOLD','EXISTING_SCOPED_CORRESPONDENCE' if identity else 'CORRESPONDENCE_NOT_ESTABLISHED')
    if relation.get('status')=='CONFLICT':d['OBJECT_CORRESPONDENCE']=fact('CONFLICT','VERIFIED_CONNECTION_DIFFERENCE_OR_DIFFERENT_OBJECT')
    if relation.get('status')=='REVIEW':d['OBJECT_CORRESPONDENCE']=fact('HOLD','VERIFIED_RELATION_PROVENANCE_UNRESOLVED')
    if door:geometry=door.get('absolute_geometry_support') is True
    elif row.get('opening_geometry_evidence'):geometry=bool(row['opening_geometry_evidence'].get('observations'))
    else:
        p=row.get('identity_evidence') or {}
        geometry=(bool(contexts) and all(c['catalog_geometry_supported'] is True for c in contexts)) or bool(
            p.get('ref_geometry_supported') and p.get('target_geometry_supported'))
    d['SOURCE_DRAWING_GEOMETRY']=fact('SUPPORTED' if geometry else 'HOLD','INDEPENDENT_DRAWING_OBSERVATIONS' if geometry else 'SOURCE_GEOMETRY_INCOMPLETE')
    d['INDEPENDENT_MEASUREMENT']=fact('HOLD' if row.get('alignment_fit_parent') else 'SUPPORTED',
        'COMPARISON_PARENT_USED_FOR_ALIGNMENT_FIT' if row.get('alignment_fit_parent') else 'EXISTING_MEASUREMENT_NOT_OWN_FIT_RESIDUAL')
    text=is_supported_dimension_change(row) or is_supported_symbol_change(row)
    if not text and row.get('entity_type')=='CodeMarker':
        text=(row.get('source_observations') or {}).get('complete') is True
    d['COMPLETE_TEXT_READINGS']=fact('SUPPORTED' if text else 'HOLD','COMPLETE_COMPARABLE_READINGS' if text else 'READINGS_NOT_ESTABLISHED')
    bounds=[c['space_boundary'] for c in contexts if 'space_boundary' in c]
    closed=bool(bounds) and len(bounds)==len(contexts) and all(b['closed'] and (not b['inferred_runs'] or b['claim_status']=='VERIFIED') for b in bounds)
    d['SUPPORTED_SPACE_BOUNDARIES']=fact('SUPPORTED' if closed else 'HOLD','SUPPORTED_CLOSED_BOUNDARIES' if closed else 'PARTIAL_OR_INFERRED_SPACE_BOUNDARIES')
    # Completed source relations do not by themselves establish a comparison of
    # the relation across revisions. No specification/physical proof is invented.
    d['VERIFIED_CONNECTION_COMPARISON']=fact('CONFLICT' if relation.get('status')=='CONFLICT' else 'HOLD',
        'VERIFIED_CONNECTION_DIFFERENCE_REQUIRES_IDENTITY_RESOLUTION' if relation.get('status')=='CONFLICT' else 'INDEPENDENT_SPACE_CONNECTION_COMPARISON_NOT_ESTABLISHED')
    d['PHYSICAL_INSTALLATION_COMPARISON']=fact('HOLD','PHYSICAL_INSTALLATION_AND_HOST_IDENTITY_NOT_ESTABLISHED')
    d['GROUNDED_SPECIFICATION_COMPARISON']=fact('HOLD','NO_GROUNDED_SPECIFICATION_COMPARISON')
    physical=row.get('physical_change_inferred') is True
    d['PHYSICAL_CHANGE_EVIDENCE']=fact('SUPPORTED' if physical else 'HOLD','EXPLICIT_PHYSICAL_CHANGE_EVIDENCE' if physical else 'DRAWING_DIFFERENCE_NOT_PHYSICAL_CHANGE_PROOF')
    return d

def verified_connection_comparison(row,rows):
    """Read independently verified connections through existing Space matches.

    Relationship differences remain competing identity/change hypotheses; an
    incomplete mapping is not a contradiction and equal room names are unused.
    """
    relation=(row.get('door_geometry_evidence') or {}).get('relation_assessment') or {}
    if relation.get('status')=='CONFLICT':
        return fact('CONFLICT','VERIFIED_CONNECTION_DIFFERENCE_REQUIRES_IDENTITY_RESOLUTION')
    if relation.get('status')=='REVIEW':return fact('HOLD','VERIFIED_CONNECTION_PROVENANCE_UNRESOLVED')
    sources=relation.get('sources',[])
    if len(sources)!=2 or not all(s.get('verified') for s in sources):
        return fact('HOLD','INDEPENDENT_SPACE_CONNECTION_COMPARISON_NOT_ESTABLISHED')
    forward={};back={}
    for r in rows:
        if r['entity_type']!='Space' or r['identity_status']!='SUPPORTED' or len(r['ref_ids'])!=1 or len(r['target_ids'])!=1:continue
        a,b=r['ref_ids'][0],r['target_ids'][0]
        forward.setdefault(b,set()).add(a);back.setdefault(a,set()).add(b)
    mapped=[]
    for connection in sources[1]['verified']:
        ids=connection['space_ids']
        if not all(len(forward.get(i,set()))==1 for i in ids):return fact('HOLD','INCOMPLETE_VERIFIED_SPACE_CONNECTION_MAPPING')
        own={next(iter(forward[i])) for i in ids}
        if not all(len(back.get(i,set()))==1 for i in own):return fact('HOLD','AMBIGUOUS_VERIFIED_SPACE_CONNECTION_MAPPING')
        mapped.append(frozenset(own))
    if set(mapped)!={frozenset(c['space_ids']) for c in sources[0]['verified']}:
        return fact('CONFLICT','VERIFIED_CONNECTION_DIFFERENCE_REQUIRES_IDENTITY_RESOLUTION')
    return fact('SUPPORTED','VERIFIED_CONNECTIONS_MATCH_UNDER_SUPPORTED_SPACE_CORRESPONDENCE',
        [c['id'] for s in sources for c in s['verified']])

def assess_claim(row,name,required,d,*,applicable_difference=True):
    blockers=[dict(dependency=k,**d[k]) for k in required if d[k]['status']!='SUPPORTED']
    if blockers:status='DEFERRED'
    elif applicable_difference and row['judgment']=='CHANGED':status='SUPPORTED_CHANGE'
    elif applicable_difference and row['judgment']=='UNCHANGED_WITHIN_SCOPE' and row.get('measured_facets'):status='NO_DIFFERENCE_WITHIN_MEASURED_SCOPE'
    else:status='DEFERRED'
    return dict(claim_type=name,status=status,required_dependencies=list(required),blocking_dependencies=blockers,
        reason_codes=[b['reason_code'] for b in blockers] or ([] if status in READY else ['NO_SUPPORTED_DIFFERENCE_CONCLUSION_FOR_THIS_CLAIM']),
        physical_change_inferred=name=='PHYSICAL_GEOMETRY_DIFFERENCE' and status=='SUPPORTED_CHANGE')

def attach_claim_assessment(result,geometries):
    source_objects=result.get('source_objects',{});counts=Counter();blocked=Counter();mixed=0
    for row in result['correspondences']:
        # Preserve the exact producer reasons; do not infer them from narrative.
        constraints=list(row.get('decision_constraints',[]))
        door=row.get('door_geometry_evidence') or {}
        constraints.extend(door.get('reason_codes',[]))
        opening=row.get('opening_geometry_evidence') or {}
        registration=opening.get('registration') or {}
        if opening and not opening.get('registration_supported') and registration.get('reason'):
            constraints.append(registration['reason'])
        row['decision_constraints']=list(dict.fromkeys(constraints))
        contexts=source_contexts(row,geometries,source_objects);d=dependencies(row,contexts)
        d['VERIFIED_CONNECTION_COMPARISON']=verified_connection_comparison(row,result['correspondences'])
        common=['BOTH_SOURCE_OBSERVATIONS','REGISTERED_COORDINATES','OBJECT_CORRESPONDENCE','INDEPENDENT_MEASUREMENT']
        kind=row['entity_type'];annotation=kind in ['CodeMarker','DimensionText'];claims=[]
        if annotation:
            claims.append(assess_claim(row,'TEXT_DESIGNATION_DIFFERENCE',common+['COMPLETE_TEXT_READINGS'],d,
                applicable_difference=row['domain']=='ANNOTATION'))
        elif kind=='Space':
            claims.append(assess_claim(row,'SPACE_BOUNDARY_DIFFERENCE',common+['SUPPORTED_SPACE_BOUNDARIES'],d))
        else:
            claims.append(assess_claim(row,'DRAWING_GEOMETRY_DIFFERENCE',common+['SOURCE_DRAWING_GEOMETRY'],d))
        if kind in ['Door','Opening','Window','SlidingDoor','WindowOrSlidingDoor','BuildingElement']:
            connection_row=dict(row,judgment='UNCHANGED_WITHIN_SCOPE',measured_facets=['VERIFIED_CONNECTED_SPACES'])
            claims.append(assess_claim(connection_row,'SPACE_CONNECTION_DIFFERENCE',common+['VERIFIED_CONNECTION_COMPARISON'],d))
        if kind!='Space':
            claims.append(assess_claim(row,'PHYSICAL_GEOMETRY_DIFFERENCE',common+['PHYSICAL_CHANGE_EVIDENCE'],d))
        if kind in ['CodeMarker','Door','Window','SlidingDoor','WindowOrSlidingDoor','BuildingElement']:
            claims.append(assess_claim(row,'INSTALLATION_SPECIFICATION_DIFFERENCE',common+['PHYSICAL_INSTALLATION_COMPARISON','GROUNDED_SPECIFICATION_COMPARISON'],d,applicable_difference=False))
        primary=claims[0];limited=[c['claim_type'] for c in claims[1:] if c['status']=='DEFERRED']
        if primary['status'] in READY and limited:mixed+=1
        reassociated=row.get('geometric_reassociation_ids',[])
        row['claim_assessment']=dict(schema_version=VERSION,claims=claims,dependencies=d,
            source_contexts=contexts,decision_constraints=row.get('decision_constraints',[]),
            search_diagnostics=row.get('search_diagnostics',[]),
            supported_scope=primary['claim_type'] if primary['status'] in READY else None,
            interpretation_limits=limited,graph_completion_required=False,
            reassessed_by_correspondence_ids=reassociated,
            scope_note='Comparison assessment only; original ontology states and review decisions are unchanged')
        for claim in claims:
            counts[(claim['claim_type'],claim['status'])]+=1
            for blocker in claim['blocking_dependencies']:blocked[(claim['claim_type'],blocker['dependency'])]+=1
    result['claim_dependency_assessment']=dict(schema_version=VERSION,comparison_row_count=len(result['correspondences']),
        claim_counts=[dict(claim_type=c,status=s,count=n) for (c,s),n in sorted(counts.items())],
        blocking_dependency_counts=[dict(claim_type=c,dependency=d,count=n) for (c,d),n in sorted(blocked.items())],
        supported_local_scope_with_unresolved_interpretation_count=mixed,
        count_unit='CLAIMS_PER_COMPARISON_ROW_NOT_UNIQUE_CHANGES_OR_OBJECTS',
        ontology_states_modified=False,graph_completion_required=False)
    result['run_id']=fingerprint([VERSION,result['run_id']])
    return result
