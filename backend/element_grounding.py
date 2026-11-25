"""Own-frame symbol → installation → opening → host hypotheses.

This graph adds evidence to comparison, never a new detection gate. A printed
marker near a supported drawing assembly is still an unresolved reference.
No project catalog, leader, product identity or passage is invented here.
"""
from collections import Counter
from copy import deepcopy
import hashlib
import numpy as np
from backend.structural_alignment import fingerprint
from integrations.fpue_ontology import understanding_contract_v0_5 as ontology
from integrations.fpue_ontology.detection_contract import interpret_code

VERSION = 'fpue_element_grounding/1'


def _box(points):
    p = np.asarray(points, float).reshape(-1, 2)
    return [*p.min(0).tolist(), *p.max(0).tolist()]


def _inside(point, box):
    return box[0] <= point[0] <= box[2] and box[1] <= point[1] <= box[3]


def _binding_error(page, g, role):
    b = g.get('source_binding') or {}
    expected = dict(source_role=role, frame_id=role+':'+f'{page.source_sha256}:{page.page_number}:{page.zoom:g}:{page.width}x{page.height}',
                    source_sha256=page.source_sha256, page=page.page_number, extraction_zoom=page.zoom,
                    width=page.width, height=page.height, image_sha256=hashlib.sha256(page.png).hexdigest())
    if not b:
        return 'SOURCE_BINDING_UNAVAILABLE'
    if any(b.get(k) != v for k, v in expected.items()) or g.get('source_role') != role or g.get('frame_id') != expected['frame_id']:
        return 'SOURCE_FRAME_OR_BYTES_MISMATCH'
    return None


def ground_source(page, g, role, opening_candidates=(), assembly_features=None):
    """Accept only ONE source. No alignment, peer drawing or GT input."""
    result = dict(schema_version=VERSION, source_role=role, frame_id=g.get('frame_id'),
        source_binding=deepcopy(g.get('source_binding')), entities=[], relations=[], marker_reviews=[], rejected_inputs=[],
        ontology_contract=dict(version=ontology.VERSION, sha256=ontology.contract_digest()),
        physical_identity_status='UNKNOWN', new_image_inference_performed=False,
        interpretation_scope='DRAWING_GEOMETRY_AND_UNRESOLVED_INSTANCE_REFERENCE',
        coverage=dict(inputs=['EXISTING_INSTALLED_ELEMENT_HYPOTHESES', 'OWN_JAMB_AND_HOST_FACE_CANDIDATES',
                              'OWN_VISIBLE_INTERIOR_PROFILE_OBSERVATIONS', 'OWN_MARKER_READINGS'],
                      exhaustive_physical_element_detection=False,
                      missing_element_is_not_absence=True, traced_instance_references_available=False,
                      project_declared_catalog_available=False))
    error = _binding_error(page, g, role)
    if error:
        result.update(status='UNAVAILABLE', reason=error)
        return result
    frame = g['frame_id']; zoom = page.zoom; features = assembly_features or {}
    own_relations = g.get('reference_relations' if role == 'Ref' else 'target_relations') or {}
    assessment = g.get('architectural_assessment') or {}
    nodes = {}; installation_nodes = []

    def own(row, recursive=True):
        binding = row.get('source_binding') or {}
        ok = (row.get('source_role', role) == role and row.get('frame_id', frame) == frame
              and row.get('source_sha256', page.source_sha256) == page.source_sha256
              and row.get('image_sha256', g['source_binding']['image_sha256']) == g['source_binding']['image_sha256']
              and binding.get('frame_id', frame) == frame and binding.get('source_sha256', page.source_sha256) == page.source_sha256
              and binding.get('source_role', role) == role and binding.get('page', page.page_number) == page.page_number
              and binding.get('extraction_zoom', zoom) == zoom)
        def foreign_ids(value, key=''):
            if isinstance(value, dict):
                return any(foreign_ids(v, k) for k, v in value.items())
            if isinstance(value, (list, tuple)):
                return any(foreign_ids(v, key) for v in value)
            if isinstance(value, str) and (key == 'id' or key.endswith(('_id', '_ids', '_ref', '_refs'))):
                if value.startswith(('Ref:', 'Target:')):
                    return not value.startswith((frame+':', role+':opening:')) and value != frame
            return False
        if recursive and foreign_ids(row):
            ok = False
        if row.get('id') and not row['id'].startswith((frame+':', role+':opening:')):
            # Old unbound marker IDs stay in their original comparison path;
            # they cannot acquire a fabricated source identity in this graph.
            ok = False
        if not ok:
            result['rejected_inputs'].append(dict(id=row.get('id', row.get('opening_id')), reason='FOREIGN_SOURCE_ENTRY'))
        return ok

    if not own(assessment, False):
        assessment = {}
    if not own(own_relations, False):
        own_relations = {}

    def refs(values):
        # Raw PDF IDs are local primitives; namespacing never uses the peer frame.
        out = []
        for value in values:
            if not isinstance(value, str):
                continue
            if value.startswith('pdf_path:'):
                value = frame+':'+value
            if not value.startswith(frame+':'):
                raise ValueError('element evidence must be own-frame evidence')
            out.append(value)
        return list(dict.fromkeys(out))

    def node(identity, kind, geometry, evidence, **fields):
        if not identity.startswith(frame+':'):
            raise ValueError('foreign element ID')
        row = dict(id=identity, kind=kind, source_role=role, frame_id=frame,
                   bbox_native=geometry, evidence_refs=refs(evidence), claim_status='PROPOSED', **fields)
        nodes[identity] = row; result['entities'].append(row)
        return row

    def link(kind, source, target, evidence, supported=False, **fields):
        st = source.get('type_candidates') or [source['kind']]
        valid = []
        for typ in st:
            ontology.validate_relation_signature(kind, typ, target['kind'])
            valid.append(typ)
        row = dict(id=frame+':element-relation:'+fingerprint([VERSION, kind, source['id'], target['id']])[:20],
            kind=kind, source_role=role, frame_id=frame, source_id=source['id'], target_id=target['id'],
            evidence_refs=refs(evidence), claim_status='SUPPORTED' if supported else 'PROPOSED',
            claim_scope='OWN_DRAWN_GEOMETRY' if supported else 'UNRESOLVED_PHYSICAL_RELATION',
            physical_truth_verified=False, signature_types=valid, **fields)
        result['relations'].append(row)
        return row

    # Independently observed opposed jambs and paired outward wall faces. The
    # two flanks remain separate wall hypotheses; no wall is filled across a gap.
    for opening in opening_candidates:
        if not own(opening):
            continue
        ident = frame+':'+VERSION+':'+opening['id']
        observed = node(ident, 'Opening', opening['bbox_native'], opening['source_refs'],
            geometry_status=opening['claim_status'], geometry_native=dict(polygons_native=[deepcopy(opening['polygon_native'])]),
            source_geometry_candidate_key=opening['id'], physical_passage_status='UNKNOWN')
        observed['comparison_source_id'] = frame+':opening_drawing_geometry/1:'+opening['id']
        for side, indices in [('left', [0, 1]), ('right', [2, 3])]:
            segments = [opening['host_face_segments_native'][i] for i in indices]
            evidence = [r for i in indices for r in opening['host_face_refs'][i]]
            wall = node(ident+':wall-flank:'+side, 'Wall', _box(segments), evidence,
                geometry_native=dict(polylines_native=deepcopy(segments)), geometry_status='SUPPORTED_PAIRED_DRAWN_FACES',
                physical_wall_status='PROPOSED', fills_opening=False)
            link('Hosting', wall, observed, [*wall['evidence_refs'], *observed['evidence_refs']], True,
                 reason='PAIRED_WALL_FACES_TERMINATE_AT_OBSERVED_JAMB', semantic_host_status='PROPOSED')
        # A line on a wall face is not an installation rail. Keep all original
        # profile observations, but require >=2 distinct interior normals before
        # proposing the ambiguous physical assembly alternatives.
        interior = [f for f in features.get(opening['id'], [])
                    if opening['normal_lo']+.1*zoom < f['normal_native'] < opening['normal_hi']-.1*zoom]
        normals = {round(f['normal_native']/zoom, 2) for f in interior}
        if len(normals) < 2:
            observed['installation_status'] = 'INSUFFICIENT_INTERIOR_PROFILE_EVIDENCE'
            continue
        evidence = [f['source_ref'] for f in interior]
        element = node(ident+':profile-assembly', 'BuildingElement', _box([f['points_native'] for f in interior]), evidence,
            type_candidates=['Window', 'Grille', 'Door'], geometry_status='SUPPORTED_INTERIOR_PARALLEL_PROFILE',
            geometry_native=dict(polylines_native=[f['points_native'] for f in interior]), matching_envelope_native=opening['bbox_native'],
            opening_id=observed['id'], profile_observations=deepcopy(interior),
            type_resolution='UNKNOWN_WINDOW_GRILLE_OR_SLIDING_DOOR', physical_assignment_status='UNBOUND')
        installation_nodes.append(element)
        link('Installation', element, observed, [*element['evidence_refs'], *observed['evidence_refs']],
             reason='INTERIOR_PROFILE_IN_OWN_JAMB_INTERVAL', geometry_relation_status='SUPPORTED',
             semantic_type_status='PROPOSED')

    # Reuse prior swing/leaf/jamb checks even if host/space closure is on hold.
    evidence_by_id = {e['evidence_id']: e for e in g.get('evidence', []) if own(e)}
    observations_by_id = {o['observation_id']:o for o in g.get('observations',[]) if o.get('observation_type')=='PdfCurve' and own(o)}
    openings = {o['opening_id']: o for o in assessment.get('openings', []) if own(o)}
    parents = {p['parent_structure_id']: p for p in assessment.get('structural_parents', []) if own(p, False)}
    doors = {d['door_id']: d for d in own_relations.get('door_reviews', []) if own(d)}
    for installed in assessment.get('installed_elements', []):
        if not own(installed) or installed['opening_id'] not in openings:
            continue
        opening = openings[installed['opening_id']]
        observed = nodes.get(opening['opening_id']) or node(opening['opening_id'], 'Opening',
            opening['geometry_native']['bbox_native'], opening['evidence_refs'], geometry_status=opening['decision'],
            geometry_native=deepcopy(opening['geometry_native']), unresolved_reasons=opening['reasons'], physical_passage_status='UNKNOWN')
        for parent_id in opening.get('host_parent_ids', []):
            parent = parents.get(parent_id)
            fragments = [f for f in (parent or {}).get('fragments', []) if own(f, False)]
            faces = [face for f in fragments for face in f.get('boundary_faces_native', [])]
            if not faces:
                continue
            wall = nodes.get(parent_id)
            if wall is None:
                wall = node(parent_id, 'Wall', _box(faces),
                    [ref for f in fragments for ref in f.get('source_observation_refs', [])],
                    geometry_native=dict(polylines_native=deepcopy(faces)), geometry_status='PROPOSED_SOURCE_HOST',
                    source_parent_decision=parent['decision'], physical_wall_status='PROPOSED', fills_opening=False,
                    fragment_decisions=[dict(fragment_id=f['fragment_id'], decision=f['decision'], reasons=f['reasons']) for f in fragments])
            link('Hosting', wall, observed, [*wall['evidence_refs'], *observed['evidence_refs']],
                 reason='EXISTING_HOST_HYPOTHESIS_REUSED_WITH_UNRESOLVED_GEOMETRY',
                 semantic_host_status='PROPOSED', original_opening_decision=opening['decision'])
        supported = []
        profile_support=[]
        for ref in installed['evidence_refs']:
            source_support=evidence_by_id.get(ref, {}).get('supporting', [])
            drawing_only=installed.get('installation_shape_only') is True
            shape_proof=next((e for e in source_support if e['kind']=='INSTALLATION_SHAPE_RECOVERY' and e['claim_scope']=='DRAWING_ONLY'),None)
            for e in source_support:
                checks = doors.get(installed['installation_id'], {}).get('reobservation', {}).get('arc_checks', [])
                source_arc=observations_by_id.get(e.get('arc_observation_ref'),{})
                observed_points=np.asarray(source_arc.get('geometry_native',{}).get('points_native',[]))
                claimed_points=np.asarray(e.get('swing_polyline_native',[]))
                same_curve=(observed_points.ndim==2 and len(observed_points)>=3 and observed_points.shape==claimed_points.shape
                    and np.all(np.isfinite(observed_points)) and np.allclose(observed_points,claimed_points,atol=1e-3))
                full_source_swing=(drawing_only and shape_proof is not None and source_arc.get('observation_type')=='PdfCurve'
                    and same_curve
                    and e.get('curve_visibility',0)>=.8 and len(e.get('jamb_connections',[]))==2
                    and all(j.get('connected') and j.get('observation_refs') for j in e['jamb_connections']))
                if (e.get('kind') == 'SWING_LEAF_AND_BOTH_JAMBS'
                    and (full_source_swing or any(c.get('arc_observation_ref') == e.get('arc_observation_ref')
                            and c.get('both_jambs_supported') and c.get('visible_fraction', 0) >= .8 for c in checks))
                    and any(l.get('visible_fraction', 0) >= .8 for l in e.get('leaf_candidates', []))):
                    supported.append(e)
                if (drawing_only and shape_proof is not None and e['kind']=='GAP_BRACKETS_TRACKS'
                    and len(e.get('tracks_native',[]))>=3 and len(e.get('cap_visibility',[]))==2
                    and min(e['cap_visibility'])>=.8):profile_support.append(e)
        types = ['Door' if t == 'SlidingDoor' else t for t in installed['type_candidates']]
        element = node(installed['installation_id'], 'Door' if types == ['Door'] else 'BuildingElement',
            installed['geometry_native']['bbox_native'], installed['evidence_refs'],
            type_candidates=types, original_type_candidates=installed['type_candidates'],
            geometry_native=deepcopy(installed['geometry_native']), opening_id=observed['id'],
            geometry_status='SUPPORTED_SWING_LEAF_AND_JAMBS' if supported else
                'SUPPORTED_FRAME_STILES_AND_TRANSVERSE_RIB' if profile_support else 'PROPOSED_SOURCE_HYPOTHESIS',
            matching_envelope_native=installed['geometry_native']['bbox_native'],
            swing_support=deepcopy(supported),profile_support=deepcopy(profile_support),
            installation_shape_only=installed.get('installation_shape_only',False),
            opening_segment_native=opening.get('opening_segment_native'), physical_assignment_status='UNBOUND')
        installation_nodes.append(element)
        evidence = [*element['evidence_refs'], *observed['evidence_refs']]
        if supported:
            if installed['installation_id'] in doors:evidence.append(doors[installed['installation_id']]['id'])
        link('Installation', element, observed, evidence, bool(supported),
            reason='OWN_VISIBLE_SWING_LEAF_AND_BOTH_JAMBS' if supported else 'SOURCE_INSTALLATION_HYPOTHESIS_UNRESOLVED',
             host_status=opening['decision'], passage_status='UNKNOWN',
             geometry_relation_status='SUPPORTED' if supported or profile_support else 'PROPOSED')

    for marker in own_relations.get('code_reviews', []):
        if not own(marker) or any(not own(marker[key]) for key in ['text', 'number'] if marker.get(key)):
            continue
        symbol = node(marker['id'], 'Symbol', marker['bbox_native'], [marker['id'], *marker.get('reading_evidence_refs', [])],
                      canonical_code=marker.get('canonical_code'), reading_status=marker.get('reading_status', 'LEGACY_UNSPECIFIED'))
        interpretation = interpret_code(marker['canonical_code']) if marker.get('canonical_code') else None
        category = ((interpretation or {}).get('family_interpretation') or {}).get('element_category')
        review = dict(id=symbol['id']+':instance-review', marker_id=symbol['id'], source_role=role, frame_id=frame,
            canonical_code=marker.get('canonical_code'), bbox_native=marker['bbox_native'],
            interpretation=interpretation, candidate_relation_ids=[], incompatible_candidates=[],
            status='HOLD', physical_assignment_status='UNBOUND', catalog_assignment_status='NOT_ASSERTED',
            reason_codes=['NO_TRACED_INSTANCE_REFERENCE', 'NO_PROJECT_DECLARED_CATALOG'],
            material_status='UNKNOWN', fire_rating_status='UNKNOWN', physical_identity_status='UNKNOWN')
        x, y, u, v = marker['bbox_native']; center = [(x+u)/2, (y+v)/2]
        diameter = max(u-x, v-y)
        for element in installation_nodes:
            envelope = element['matching_envelope_native']; contained = _inside(center, envelope)
            # A perpendicular projection onto an observed gap is a search
            # constraint, not a reference proof and never nearest-neighbor ID.
            raw_opening = next((o for o in opening_candidates if frame+':'+VERSION+':'+o['id'] == element['opening_id']), None)
            normal_gap = None
            if raw_opening:
                axis = raw_opening['axis']; normal = center[1-axis]
                normal_gap = max(raw_opening['normal_lo']-normal, normal-raw_opening['normal_hi'], 0)
                plausible = raw_opening['left'] <= center[axis] <= raw_opening['right'] and normal_gap <= diameter
            else:
                plausible = contained
                if element.get('profile_support'):
                    segment=np.asarray(element['opening_segment_native']);axis=int(np.ptp(segment[:,1])>np.ptp(segment[:,0]))
                    low,high=sorted(segment[:,axis]);normal_gap=max(envelope[1-axis]-center[1-axis],center[1-axis]-envelope[3-axis],0)
                    # Same bounded review neighborhood used by code_attempts;
                    # the actual profile/stiles/rib are required first. This
                    # remains a PROPOSED reference with all competitors kept.
                    plausible=low<=center[axis]<=high and normal_gap<=1.5*diameter
            if not plausible:
                continue
            compatible = category is None or any(ontology.is_a(t, category) or ontology.is_a(category, t)
                                                for t in element['type_candidates'])
            if not compatible:
                review['incompatible_candidates'].append(dict(element_id=element['id'], reason='GLOSSARY_GEOMETRY_CATEGORY_CONFLICT'))
                continue
            candidate = link('ReferenceAssociation', symbol, element, [*symbol['evidence_refs'], *element['evidence_refs']],
                reason='MARKER_IN_DRAWN_ASSEMBLY_ENVELOPE' if contained else 'MARKER_NORMAL_PROJECTION_IN_OBSERVED_GAP',
                reference_method='UNTRACED_SPATIAL_CONTEXT', reference_status='HOLD',
                normal_gap_native_px=normal_gap, marker_center_native=center,
                geometry_candidate_supported=element['geometry_status'].startswith('SUPPORTED'),
                definition_status=(interpretation or {}).get('definition_status', 'UNRESOLVED'))
            review['candidate_relation_ids'].append(candidate['id'])
        count = len(review['candidate_relation_ids'])
        review['reason_codes'].append('COMPETING_GEOMETRY_CANDIDATES' if count > 1 else
                                      'SPATIAL_CANDIDATE_NOT_INSTANCE_PROOF' if count else 'NO_COMPATIBLE_OBSERVED_ASSEMBLY')
        if marker.get('reading_status') != 'SUPPORTED':
            review['reason_codes'].append('INCOMPLETE_OR_AMBIGUOUS_READING')
        result['marker_reviews'].append(review)
    result.update(status='REVIEW_AVAILABLE', summary=dict(entity_types=dict(Counter(n['kind'] for n in result['entities'])),
        relation_states=dict(Counter(r['claim_status'] for r in result['relations'])),
        relation_kinds=dict(Counter(r['kind'] for r in result['relations'])),
        markers=len(result['marker_reviews']), markers_with_candidates=sum(bool(m['candidate_relation_ids']) for m in result['marker_reviews']),
        supported_physical_type_assignments=0, verified_facts=0))
    return result


def attach_element_grounding(result, pages, geometries):
    audit = result.get('opening_geometry_comparison') or {}
    candidates = audit.get('source_candidates') or [[], []]
    features = audit.get('source_assembly_features') or [{}, {}]
    graphs = {role: ground_source(p, g, role, own, profile)
              for role, p, g, own, profile in zip(['Ref', 'Target'], pages, geometries, candidates, features)}
    result['element_grounding'] = dict(schema_version=VERSION, sources=graphs,
        detection_decision_changed=False, global_graph_completion_required=False,
        supported_type_assignment_requires=list(ontology.REFERENCE_METHODS)+['PROJECT_DECLARED_CATALOG'])
    for role in ['Ref', 'Target']:
        graph = graphs[role]
        index = {m['marker_id']: m for m in graph['marker_reviews']}
        for obj in result['source_objects'][role]:
            entities = [e['id'] for e in graph['entities'] if e.get('comparison_source_id',e['id']) == obj['id']]
            if entities:
                obj['element_grounding_entity_ids'] = entities
            if obj['id'] in index:
                obj['element_grounding_review_id'] = index[obj['id']]['id']
        for row in result['correspondences']:
            own_ids = row['ref_ids' if role == 'Ref' else 'target_ids']
            entities = [e['id'] for e in graph['entities'] if e.get('comparison_source_id',e['id']) in own_ids]
            if entities:
                row.setdefault('element_grounding_entity_ids', {})[role] = entities
            related = [m['id'] for i in own_ids if (m := index.get(i))]
            if related:
                row.setdefault('element_grounding_review_ids', {})[role] = related
    return result
