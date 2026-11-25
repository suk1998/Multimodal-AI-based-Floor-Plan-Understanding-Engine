"""Decision lineage for all comparison records; automatic decisions only.

This ledger covers records that entered comparison, not every upstream pixel.
Review revisions remain owned by ComparisonReview and are not overwritten here.
"""
from backend.structural_alignment import fingerprint
from backend.change_policy import is_final_change_kind
from integrations.fpue_ontology import detection_contract as ontology_policy

VERSION='comparison_candidate_lineage/1'


def attach_candidate_audit(result):
    entries=[]
    for row in result['correspondences']:
        candidate_id='candidate:'+fingerprint([row['domain'],row['entity_type'],row['ref_ids'],row['target_ids']])
        row['candidate_id']=candidate_id
        symbol=row.get('symbol_decision',{})
        readings=symbol.get('readings',{})
        interpretation_status=('PARTIAL' if any(v.get('semantics',{}).get('definition_status')=='UNRESOLVED' for v in readings.values())
                               else 'AVAILABLE' if readings or row['entity_type']=='DimensionText' else 'PARTIAL')
        ontology_type=('TypeDesignationHypothesis' if row['entity_type']=='CodeMarker' and row['domain']=='ANNOTATION'
                       else 'Symbol' if row['entity_type']=='CodeMarker' else 'DimensionAnnotation' if row['entity_type']=='DimensionText'
                       else 'Door' if row['entity_type']=='SlidingDoor' else row['entity_type'] if row['entity_type'] in ontology_policy.ontology.ENTITY_PARENTS
                       else 'UnknownBuildingElement')
        claim_assessment=row.get('claim_assessment',{})
        registration=(claim_assessment.get('dependencies',{}).get('REGISTERED_COORDINATES',{}).get('status')=='SUPPORTED'
            if claim_assessment else (row.get('symbol_evidence') or {}).get('registration_supported') or
            row.get('coordinate_support',{}).get('status')=='SUPPORTED_COMPARISON_SCOPE')
        automatic=bool(row.get('automatic_confirmation')) and is_final_change_kind(row)
        entries.append(dict(candidate_id=candidate_id,correspondence_id=row['correspondence_id'],
            channel='PRINTED_TYPE_CODE' if row['entity_type']=='CodeMarker' else 'DIMENSION_TEXT' if row['entity_type']=='DimensionText' else 'OBJECT_GEOMETRY_OR_RELATION',
            ontology_type=ontology_type,
            source_ids={'Ref':row['ref_ids'],'Target':row['target_ids']},evidence_refs=row['evidence_refs'],
            stages=dict(observation={'status':'AVAILABLE' if row['evidence_refs'] else 'UNCONFIRMED'},
                semantic_interpretation={'status':interpretation_status,'readings':readings},
                correspondence={'status':row['identity_status']},registration={'status':'SUPPORTED_LOCAL' if registration else 'UNCONFIRMED'},
                difference={'status':row['judgment'],'reason':row['decision_reason']},
                automatic_output={'status':row.get('output_disposition') or ('ELIGIBLE' if automatic else 'REVIEW_OR_NOT_CHANGED'),
                    'represented_by_correspondence_ids':row.get('represented_by_correspondence_ids',[]),
                    'reason_codes':symbol.get('reason_codes') or (['SCOPED_CHANGE_EVIDENCE_AVAILABLE'] if automatic else [row.get('output_disposition') or 'NO_AUTOMATIC_SCOPED_CHANGE_DECISION'])}),
            claim_scope=(row.get('door_geometry_evidence') or row.get('opening_geometry_evidence') or {}).get('claim_scope') or (ontology_policy.CLAIM_SCOPE if row['entity_type']=='CodeMarker' and row['domain']=='ANNOTATION' else row['domain']),
            claim_assessment=claim_assessment,
            decision_constraints=row.get('decision_constraints',[]),
            physical_change_inferred=row.get('physical_change_inferred'),decision_origin='ANALYSIS_SNAPSHOT_NOT_USER_REVIEW'))
    result['candidate_audit']=dict(schema_version=VERSION,ontology_version=ontology_policy.ontology.VERSION,
        ontology_contract_sha256=ontology_policy.ontology.contract_digest(),glossary_sha256=ontology_policy.glossary_digest(),
        comparison_record_count=len(entries),records=entries,
        scope='All existing comparison records; upstream exclusions remain in excluded_upstream',
        global_graph_completion_required=False)
    return result
