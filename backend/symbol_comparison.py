"""Evidence-scoped decisions on existing printed-marker correspondences.

Reuses the registered matcher; does not estimate alignment, create a physical
Door/Window, require a complete room graph or compare a GT-specific code pair.
"""
from collections import Counter
import hashlib,math
import cv2
import numpy as np
from backend.structural_alignment import transform
from backend.marker_locus import measure_marker_locus,MINIMUM_OUTLINE_IOU
from backend.symbol_codes import marker_reading
from integrations.fpue_ontology.vocabulary import normalize_label
from integrations.fpue_ontology import detection_contract as policy

MINIMUM_OCR_SCORE=.85
MINIMUM_PERIMETER_SUPPORT=.85


def _reading(marker,bundle,page):
    frame=bundle['frame_id'];binding=bundle['source_binding']
    if not marker.get('symbol_path') or not marker.get('text'):return None,'MISSING_MARKER_OBSERVATION'
    if not marker['id'].startswith(frame+':'):return None,'FOREIGN_SYMBOL_ID'
    box=np.asarray(marker['bbox_native'],float)
    if box.shape!=(4,) or not np.isfinite(box).all() or not(0<=box[0]<box[2]<=page.width and 0<=box[1]<box[3]<=page.height):return None,'INVALID_NATIVE_MARKER_BOUNDS'
    shape=marker['symbol_path']
    if len(shape.get('bbox',[]))!=4 or not np.allclose(shape['bbox'],box,atol=.001):return None,'MARKER_VECTOR_BOUNDING_MISMATCH'
    if len(shape.get('center',[]))!=2:return None,'MISSING_MARKER_CENTER'
    glyphs=[t for t in (marker['text'],marker.get('number')) if t]
    reading=marker_reading(glyphs,shape)
    if not reading or reading['reading_status']!='SUPPORTED':return None,'INCOMPLETE_OR_AMBIGUOUS_TYPE_READING'
    if marker.get('reading_status') not in (None,'SUPPORTED'):return None,'INCOMPLETE_OR_AMBIGUOUS_TYPE_READING'
    if marker.get('canonical_code') not in (None,reading['canonical_code']):return None,'CODE_CONTRADICTS_OBSERVED_GLYPHS'
    raw_observations={o['observation_id']:o for o in bundle.get('observations',[])}
    relations=bundle.get('reference_relations' if bundle['source_role']=='Ref' else 'target_relations',{})
    local={o['id']:o for o in relations.get('code_reobservations',{}).get('observations',[])}
    sources=[]
    for glyph in {g['id']:g for g in glyphs}.values():
        if not glyph['id'].startswith(frame+':') or glyph.get('frame_id',frame)!=frame or glyph.get('source_role',bundle['source_role'])!=bundle['source_role']:
            return None,'FOREIGN_GLYPH_FRAME'
        value=normalize_label(glyph.get('raw',glyph.get('normalized',''))).upper()
        if value!=normalize_label(glyph.get('normalized',value)).upper():return None,'RAW_NORMALIZED_READING_CONFLICT'
        if glyph.get('source_kind')=='OCR':
            original=raw_observations.get(glyph['id'],{});e=original.get('source_evidence',{})
            if (original.get('frame_id')!=frame or original.get('source_role')!=bundle['source_role']
                or e.get('source_sha256')!=binding['source_sha256'] or e.get('image_sha256')!=binding['image_sha256']):
                return None,'OCR_EVIDENCE_SOURCE_MISMATCH'
            if normalize_label(e.get('text','')).upper()!=value:return None,'OCR_TEXT_SOURCE_MISMATCH'
            score=original.get('confidence')
        elif glyph.get('source_kind')=='OCR_LOCAL_RECOGNITION':
            original=local.get(glyph['id'],{})
            if any(original.get(k)!=binding.get(k) for k in ['frame_id','source_role','source_sha256','image_sha256']):return None,'LOCAL_OCR_EVIDENCE_SOURCE_MISMATCH'
            if normalize_label(original.get('raw','')).upper()!=value:return None,'LOCAL_OCR_TEXT_SOURCE_MISMATCH'
            score=original.get('recognition_score')
        elif glyph.get('bbox_pdf_local') is not None:
            score=None
        else:return None,'UNBOUND_TEXT_PROVENANCE'
        if score is not None and (type(score) not in (int,float) or not math.isfinite(score) or not MINIMUM_OCR_SCORE<=score<=1):
            return None,'LOW_OCR_READING_SCORE'
        if glyph.get('source_kind') in ('OCR','OCR_LOCAL_RECOGNITION') and score is None:return None,'MISSING_OCR_READING_SCORE'
        sources.append(dict(id=glyph['id'],raw=value,bbox_native=glyph['bbox_native'],source_kind=glyph.get('source_kind','OWN_PDF_TEXT'),recognition_score=score))
    return dict(canonical_code=reading['canonical_code'],semantics=policy.interpret_code(reading['canonical_code']),
        marker_id=marker['id'],frame_id=frame,bbox_native=box.tolist(),glyphs=sources,
        native_marker_observation=shape),None


def enrich_marker_changes(result,pages,bundles,alignment,scope):
    """Annotate each existing CodeMarker record with a retained decision trace."""
    marker_rows=[r for r in result['correspondences'] if r['entity_type']=='CodeMarker']
    if not marker_rows:return result
    bound=True
    for role,page,g in zip(['Ref','Target'],pages,bundles):
        b=g['source_binding'];required=['source_sha256','page','extraction_zoom','width','height','image_sha256','source_role']
        if not all(k in b for k in required):bound=False;continue
        if (g['source_role']!=role or b['source_role']!=role or b['source_sha256']!=page.source_sha256
            or b['page']!=page.page_number or b['extraction_zoom']!=page.zoom
            or [b['width'],b['height']]!=[page.width,page.height]
            or b['image_sha256']!=hashlib.sha256(page.png).hexdigest()):raise ValueError('Printed type source/native frame mismatch')
        if b.get('frame_id',g['frame_id'])!=g['frame_id'] or ('image_id' in b and g['frame_id']!=role+':'+b['image_id']):
            raise ValueError('Printed type image/frame identity mismatch')
    lookups=[]
    for role,g in zip(['Ref','Target'],bundles):
        lookups.append({m['id']:m for m in g.get('reference_relations' if role=='Ref' else 'target_relations',{}).get('code_reviews',[])})
    uses=[Counter(i for r in marker_rows if r['domain']=='ANNOTATION' for i in r[k]) for k in ['ref_ids','target_ids']]
    distances=[]
    for p in pages:
        gray=cv2.imdecode(np.frombuffer(p.png,np.uint8),0)
        distances.append(cv2.distanceTransform((gray>=210).astype(np.uint8),cv2.DIST_L2,cv2.DIST_MASK_PRECISE))
    def perimeter(box,index):
        x,y,u,v=box;angles=np.linspace(0,2*np.pi,96,endpoint=False)
        points=np.rint(np.c_[(x+u)/2+(u-x)/2*np.cos(angles),(y+v)/2+(v-y)/2*np.sin(angles)]).astype(int)
        if np.any(points<0) or np.any(points[:,0]>=pages[index].width) or np.any(points[:,1]>=pages[index].height):return 0.
        return float(np.mean(distances[index][points[:,1],points[:,0]]<=max(1,.65*pages[index].zoom)))
    for row in marker_rows:
        trace={'policy_version':policy.VERSION,'claim_scope':policy.CLAIM_SCOPE,'status':'REVIEW','reason_codes':[],
               'readings':{},'physical_change_inferred':False}
        row['symbol_decision']=trace
        def defer(reason):trace['reason_codes'].append(reason)
        if row['domain']!='ANNOTATION':defer('PHYSICAL_SPECIFICATION_REMAINS_UNCONFIRMED');continue
        if not bound:defer('NATIVE_SOURCE_BINDING_UNAVAILABLE');continue
        if len(row['ref_ids'])!=1 or len(row['target_ids'])!=1:defer('NO_ONE_TO_ONE_SYMBOL_CORRESPONDENCE');continue
        pair=[lookups[i].get(row[k][0]) for i,k in enumerate(['ref_ids','target_ids'])]
        if not all(pair):defer('ORIGINAL_SYMBOL_EVIDENCE_UNAVAILABLE');continue
        readings=[]
        for i,m in enumerate(pair):
            reading,reason=_reading(m,bundles[i],pages[i])
            if reason:defer(('Ref' if i==0 else 'Target')+':'+reason)
            else:trace['readings']['Ref' if i==0 else 'Target']=reading;readings.append(reading)
        if len(readings)!=2:continue
        if readings[0]['canonical_code']==readings[1]['canonical_code']:
            trace.update(status='UNCHANGED',reason_codes=['SAME_PRINTED_TYPE_CODE']);continue
        if row['judgment']!='CHANGED' or row['identity_status']!='SUPPORTED' or not row.get('identity_evidence',{}).get('mutually_preferred'):
            defer('EXISTING_CORRESPONDENCE_NOT_SUPPORTED');continue
        if any(uses[i][row[k][0]]!=1 for i,k in enumerate(['ref_ids','target_ids'])):
            defer('NON_UNIQUE_MARKER_CORRESPONDENCE');continue
        if (alignment['status'] not in ('PARTIAL','VERIFIED_ROI') or scope.matrix is None
            or row.get('alignment_fit_parent') or row.get('verified_target_bbox_native') is None):
            defer('NO_INDEPENDENT_LOCAL_REGISTRATION');continue
        boxes=[r['bbox_native'] for r in readings]
        locus=measure_marker_locus(*boxes,scope,pages[0].zoom,alignment['settings']['normal_tolerance_ref_px'])
        if not locus['supported']:defer(locus['reason']);continue
        iou=locus['marker_outline_iou'];center_distance=locus['center_distance_ref_px'];tolerance=locus['center_tolerance_ref_px']
        supports=[perimeter(b,i) for i,b in enumerate(boxes)]
        if iou<MINIMUM_OUTLINE_IOU or center_distance>tolerance or min(supports)<MINIMUM_PERIMETER_SUPPORT:
            defer('MARKER_LOCUS_OR_ORIGINAL_PERIMETER_UNSUPPORTED');continue
        evidence=dict(validation_status='SUPPORTED',policy_version=policy.VERSION,claim_scope=policy.CLAIM_SCOPE,
            registration_supported=True,complete_readings=True,unique_correspondence=True,
            marker_locus_evidence=locus,
            marker_outline_iou=iou,center_distance_ref_px=center_distance,center_tolerance_ref_px=tolerance,
            perimeter_support=supports,minimum_perimeter_support=MINIMUM_PERIMETER_SUPPORT,
            alignment_status=alignment['status'],matrix_target_to_ref=alignment['matrix'],
            sources=trace['readings'],ontology_version=policy.ontology.VERSION,
            ontology_contract_sha256=policy.ontology.contract_digest(),glossary_sha256=policy.glossary_digest(),
            original_comparison={'change_types':row['change_types'],'measured_facets':row.get('measured_facets',[]),'decision_reason':row['decision_reason']},
            physical_assignment_status='UNBOUND',physical_change_inferred=False)
        before,after=[r['canonical_code'] for r in readings]
        row.update(automatic_confirmation=policy.CONFIRMATION,state='CONFIRMED',change_types=[policy.CHANGE_TYPE],
            before=before,after=after,ref_bbox_native=boxes[0],target_observed_bbox_native=boxes[1],
            symbol_evidence=evidence,physical_change_inferred=False,space_name=None,
            change_description=f'창호 기호 표기가 {before}에서 {after}로 변경되었습니다.',
            decision_reason='양쪽 원본의 문자·번호와 원형 기호의 유일한 위치 대응, 검증 범위 및 외곽 지지를 확인했습니다. 실제 문·창문 교체와 상세 사양은 미확인입니다.',
            measured_facets=['PRINTED_TYPE_CODE'],scope_status='SUPPORTED_REGISTERED_MARKER_LOCUS')
        trace.update(status='SUPPORTED_PRINTED_CHANGE',reason_codes=['COMPLETE_TYPE_READINGS_AT_REGISTERED_LOCUS'])
    return result
