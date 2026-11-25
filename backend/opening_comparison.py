"""Connect independent observed opening geometry to the existing comparison.

Keeps the prior NCC coverage, object hypotheses and rejected records intact.
Adds narrowly scoped drawing observations without claiming product replacement.
"""
from collections import Counter
import numpy as np
from backend.opening_geometry import compare_openings,VERSION
from backend.structural_alignment import fingerprint,transform
from backend.wall_sections import Section,Stroke,adjoining_names
from integrations.fpue_ontology import opening_change_contract as policy

def _name_evidence(geometry,o,zoom):
    # Reuse observed space-boundary adjacency, never just the nearest room text.
    axis=o['axis'];left,right=o['left'],o['right'];lo,hi=o['normal_lo'],o['normal_hi']
    section=Section(Stroke(axis,lo,left,right,(),False),Stroke(axis,hi,left,right,(),False),(),o['id'])
    cap=Stroke(1-axis,(left+right)/2,lo,hi,(),False)
    return adjoining_names(geometry,section,cap,zoom)

def _same_observed_jamb_rows(result,pair,pages):
    """Supplement existing wall-end facts, without double-counting their gap.

    Requires the same source primitive AND same native jamb in both PDFs;
    proximity or common room names alone cannot merge observations.
    """
    objects={role:{o['id']:o for o in result['source_objects'][role]} for role in ['Ref','Target']}
    matches=[]
    for row in result['correspondences']:
        if row.get('automatic_confirmation')!='VECTOR_WALL_END_WITH_INDEPENDENT_RASTER_SUPPORT':continue
        for end in [0,1]:
            ok=True
            for i,role in enumerate(['Ref','Target']):
                ids=row['ref_ids' if i==0 else 'target_ids'];original=objects[role][ids[0]]
                points=np.asarray(pair[i]['jamb_segments_native'][end]);box=np.r_[points.min(0),points.max(0)]
                primitive_refs={r.split('pdf_path:',1)[1] for r in original['refs'] if 'pdf_path:' in r}
                own={r.split('pdf_path:',1)[1] for r in pair[i]['jamb_refs'][end]}
                if not primitive_refs&own or not np.allclose(original['bbox'],box,atol=.25*pages[i].zoom):ok=False;break
            if ok:matches.append(row);break
    return matches

def attach_opening_comparison(result,pages,geometries,alignment,strokes,vectors):
    if vectors is None or strokes is None or len(vectors)!=2 or len(strokes)!=2:
        result['opening_geometry_comparison']={'status':'UNAVAILABLE','reason':'SOURCE_PDF_GEOMETRY_NOT_PROVIDED'}
        return result
    audit=compare_openings(pages,strokes,vectors,alignment)
    result['opening_geometry_comparison']=dict(schema_version=VERSION,policy_version=policy.VERSION,
        status='UNAVAILABLE' if audit.get('reason') else 'AVAILABLE',
        **audit,global_graph_completion_required=False,physical_assignment_status='UNBOUND')
    lookups=[]
    for role,items,g,p in zip(['Ref','Target'],audit['source_candidates'],geometries,pages):
        lookup={}
        for o in items:
            ident=g['frame_id']+':'+VERSION+':'+o['id']
            names=_name_evidence(g,o,p.zoom)
            evidence=[g['frame_id']+':'+ref for ref in o['source_refs']]
            obj=dict(id=ident,kind='Opening',name=(' / '.join(names)+' (경계 인접 후보)') if names else None,bbox=o['bbox_native'],
                refs=evidence,context_refs=list(names.values()),hosts=[],spaces=[],parent=None,supported=True,
                native_observations=dict(claim_scope=policy.CLAIM_SCOPE,geometry_candidate=o,
                    semantic_status='PROPOSED',physical_assignment_status='UNBOUND',physical_passage_status='UNKNOWN'))
            result['source_objects'][role].append(obj);lookup[o['id']]=obj
        lookups.append(lookup)
    for candidate in audit.get('candidates',[]):
        pair=[candidate[r] for r in ['Ref','Target']];objects=[lookups[i][o['id']] for i,o in enumerate(pair)]
        registration=candidate['registration'];supported=registration['status']=='SUPPORTED_LOCAL_COORDINATE_CORRESPONDENCE'
        changed=candidate['state']=='SUPPORTED_DRAWING_GEOMETRY_CHANGE'
        types=[]
        if candidate['observed_jamb_change']:types.append('OPENING_DRAWN_JAMB_POSITION')
        if candidate['observed_profile_change']:types.append('OPENING_DRAWN_PROFILE')
        proof=dict(validation_status='SUPPORTED' if changed else 'UNCONFIRMED',policy_version=policy.VERSION,
            claim_scope=policy.CLAIM_SCOPE,registration_supported=supported,unique_correspondence=True,
            core_used_for_fit=False,independent_global_holdout_supported=supported,
            external_witness_axes=sorted(w['axis'] for w in registration.get('witnesses',[])),
            physical_assignment_status='UNBOUND',jamb_difference_supported=candidate['observed_jamb_change'],
            profile_difference_supported=candidate['observed_profile_change'],
            ontology_version=policy.ontology.VERSION,ontology_contract_sha256=policy.ontology.contract_digest(),
            sources={role:o['source_binding'] for role,o in zip(['Ref','Target'],pair)},
            geometry_pair_id=candidate['id'],registration=registration,observations=candidate)
        bbox=None
        if supported:
            points=np.r_[pair[1]['polygon_native'],transform(pair[0]['polygon_native'],np.linalg.inv(alignment['matrix']))]
            bbox=[*points.min(0).tolist(),*points.max(0).tolist()]
        refs=list(dict.fromkeys([r for o in objects for r in o['refs']+o['context_refs']]+[
            geometries[i]['frame_id']+':'+ref for witness in registration.get('witnesses',[])
            for i,role in enumerate(['Ref','Target']) for ref in witness[role]['source_refs']]))
        description='개구부의 '+('양쪽 경계 위치와 내부 선 구성이' if len(types)==2 else '경계 위치가' if types==['OPENING_DRAWN_JAMB_POSITION'] else '내부 선 구성이')+' 도면에서 달라졌습니다. 실제 설치 요소의 교체 여부는 미확인입니다.'
        row=dict(correspondence_id='OG'+fingerprint([VERSION,*[o['id'] for o in objects]])[:14],
            entity_type='Opening',domain='STRUCTURAL',ref_ids=[objects[0]['id']],target_ids=[objects[1]['id']],
            cardinality='1:1',identity_status='SUPPORTED' if supported else 'UNDETERMINED',
            identity_evidence={'scope':'REGISTERED_GEOMETRIC_LOCUS_NOT_PHYSICAL_PRODUCT_IDENTITY','mutually_preferred':True},
            judgment='CHANGED' if changed else 'UNCHANGED_WITHIN_SCOPE' if supported and not types else 'UNDETERMINED',
            state='CONFIRMED' if changed else 'REVIEW',change_types=types,
            alignment_fit_parent=False,verified_target_bbox_native=bbox,
            ref_bbox_native=pair[0]['bbox_native'],target_observed_bbox_native=pair[1]['bbox_native'],
            space_name=objects[1]['name'] or objects[0]['name'],
            scope_status='EXTERNAL_WALL_SUPPORTED_OPENING_LOCUS' if supported else 'UNCONFIRMED',
            change_description=description if changed else '관찰한 개구부 기하 범위를 비교했습니다. 미측정 부분과 실제 설치 요소의 변화는 미확인입니다.',
            decision_reason=('변경 부위 밖 두 방향의 벽면과 기존 독립 검증으로 좌표 대응을 확인했습니다. 양쪽 원본의 선과 상대 위치의 지지 차이를 비교했으며 실제 제품 동일성·교체는 미확인입니다.' if supported else registration.get('reason','국소 좌표 검증 근거가 부족합니다.')),
            measured_facets=['DRAWN_JAMB_POSITION','DRAWN_ASSEMBLY_PROFILE'],measurements=candidate,
            evidence_refs=refs,opening_geometry_evidence=proof,physical_change_inferred=False)
        related=_same_observed_jamb_rows(result,pair,pages) if changed and types==['OPENING_DRAWN_JAMB_POSITION'] else []
        if changed and not related:
            row['automatic_confirmation']=policy.CONFIRMATION
            assert policy.is_supported_opening_change(row)
        elif related:
            row.update(state='REVIEW',output_disposition='SUPPLEMENTARY_EVIDENCE',
                represented_by_correspondence_ids=[r['correspondence_id'] for r in related],
                decision_reason='같은 원본 경계의 벽 끝선 변경이 기존 결과에 표시됩니다. 개구부 관찰과 국소 좌표 근거를 해당 결과의 보충 근거로 보존하며 별도 변경 건수에 중복 합산하지 않습니다.')
            for existing in related:
                existing.setdefault('supplemental_opening_correspondence_ids',[]).append(row['correspondence_id'])
                existing['evidence_refs']=list(dict.fromkeys(existing['evidence_refs']+refs))
        result['correspondences'].append(row)
    result['judgments']=dict(Counter(r['judgment'] for r in result['correspondences']))
    if result['judgments'].get('CHANGED'):result['status']='REVIEW_AVAILABLE'
    result['run_id']=fingerprint([VERSION,policy.VERSION,result['run_id'],audit.get('settings'),
                                 [(c['id'],c['state']) for c in audit.get('candidates',[])]])
    return result
