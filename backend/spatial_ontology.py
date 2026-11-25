"""Ontology constraints plus native geometry, used inside Spatial Understanding.

No global OWL completion: a missing wall, empty label or adjacent region cannot
become a Connection. Rules and SHA are shared with the exported OWL schema.
"""
from dataclasses import asdict, dataclass
from collections import Counter, defaultdict
import math

import cv2
import numpy as np

from integrations.fpue_ontology.rules import ONTOLOGY_IRI, RULE_VERSION, rule_digest, validate_signature
from integrations.fpue_ontology.vocabulary import vocabulary_digest


@dataclass(frozen=True)
class RelationSettings:
    native_tolerance_px: float = 2.0
    parallel_tolerance_degrees: float = 10.0
    minimum_boundary_support: float = .8
    side_probe_fractions: tuple = (.25, .5, .75)
    version: str = 'ontology_geometry_constraints/1-uncalibrated'


def geometry_eligibility(entity, evidence_by_id):
    """Reject explicitly invalid barriers without overwriting source hypotheses."""
    reasons = []
    assessment = entity.get('geometry_properties', {}).get('architectural_admissibility', {})
    if assessment.get('decision') in {'PARTIAL_GEOMETRIC_SUPPORT', 'HOLD', 'CONTRADICTED'}:
        reasons.append('WHOLE_CANDIDATE_WITHHELD_BY_ARCHITECTURAL_GEOMETRY')
    if entity.get('claim_status') in {'REJECTED', 'CONFLICTED', 'STALE'}:
        reasons.append('SOURCE_CLAIM_NOT_ELIGIBLE_FOR_SPATIAL_INFERENCE')
    refs = entity.get('evidence_refs', [])
    if not refs or any(ref not in evidence_by_id for ref in refs):
        reasons.append('GEOMETRY_EVIDENCE_MISSING')
    for ref in refs:
        proof = evidence_by_id.get(ref, {})
        for item in proof.get('contradicting', []):
            if any(any(word in flag for word in ('DIMENSION', 'FURNITURE', 'FIXTURE', 'TEXT_OR_LABEL')) for flag in item.get('flags', [])):
                reasons.append('EXPLICIT_NONSTRUCTURAL_CONTRADICTION')
    return {'eligible': not reasons, 'reasons': sorted(set(reasons)), 'ontology_rule_version': RULE_VERSION}


def _samples(segment, count=9):
    a, b = np.asarray(segment, float)
    return np.linspace(a, b, count)


def _point_on_ring_distance(point, polygons):
    if not polygons:
        return math.inf
    return min(abs(cv2.pointPolygonTest(np.asarray(poly, np.float32), tuple(map(float, point)), True)) for poly in polygons if len(poly) >= 3)


def _segment_distance(point, segment):
    a, b = np.asarray(segment, float)
    delta = b - a
    t = np.clip(np.dot(np.asarray(point)-a, delta)/max(float(delta@delta), 1e-12), 0, 1)
    return float(np.linalg.norm(np.asarray(point)-(a+t*delta)))


class SpatialReasoner:
    def __init__(self, binding, entities, geometry_evidence, observations, settings=None):
        self.binding = binding
        self.settings = settings or RelationSettings()
        self.entities = self._index(entities, 'entity_id')
        self.proofs = self._index(geometry_evidence, 'evidence_id')
        self.observations = self._index(observations, 'observation_id')
        self.decisions = {key: geometry_eligibility(entity, self.proofs) for key, entity in self.entities.items()}

    def _index(self, records, key):
        index = {}
        for record in records:
            identity = record[key]
            if identity in index:
                raise ValueError(f'duplicate ontology input {key}: {identity}')
            if record.get('frame_id', self.binding['frame_id']) != self.binding['frame_id'] or record.get('source_role', self.binding['source_role']) != self.binding['source_role']:
                raise ValueError('mixed ontology source role/frame')
            index[identity] = record
        return index

    def resolved_evidence(self, entity):
        refs = entity.get('evidence_refs', [])
        if not refs:
            return False
        for ref in refs:
            proof = self.proofs.get(ref)
            if not proof or proof.get('unresolved_source_ids') or not proof.get('source_observation_refs'):
                return False
            if any(o not in self.observations for o in proof['source_observation_refs']):
                return False
        return True

    def door_support(self, entity):
        for ref in entity.get('evidence_refs', []):
            proof = self.proofs.get(ref, {})
            for support in proof.get('supporting', []):
                if support.get('kind') != 'SWING_LEAF_AND_BOTH_JAMBS':
                    continue
                arc = support.get('arc_observation_ref')
                observation = self.observations.get(arc, {})
                if arc not in proof.get('source_observation_refs', []) or observation.get('observation_type') not in {'PdfCurve', 'RasterCurve', 'RasterArc'} or observation.get('source_evidence', {}).get('context_truncated') is True:
                    continue
                jambs = support.get('jamb_connections', [])
                if len(jambs) == 2 and all(j.get('connected') is True for j in jambs) and support.get('curve_visibility', 0) >= .8 and any(leaf.get('visible_fraction', 0) >= .8 for leaf in support.get('leaf_candidates', [])):
                    return True
        return False

    def host_assessment(self, opening):
        props = opening['geometry_properties']
        segment = props.get('opening_segment_native')
        result = {'opening_id': opening['entity_id'], 'eligible': False, 'hosts': [], 'reasons': [], 'endpoint_semantics': 'observed jamb/threshold samples, not correspondence landmarks'}
        if not segment or len(segment) != 2 or np.asarray(segment).shape != (2, 2) or not np.isfinite(segment).all():
            result['reasons'].append('NO_VALID_OBSERVED_THRESHOLD')
            return result
        if not self.decisions[opening['entity_id']]['eligible'] or not self.resolved_evidence(opening):
            result['reasons'].append('OPENING_EVIDENCE_UNRESOLVED_OR_CONTRADICTED')
        if opening.get('boundary_truncated'):
            result['reasons'].append('OPENING_TRUNCATED_NO_COMPLETE_PASSAGE_INFERENCE')
        if props.get('gap_status') == 'REJECTED_SOLID_OVERLAP':
            result['reasons'].append('SOLID_WALL_CONTRADICTS_OPENING')
        direction = np.asarray(segment[1], float) - segment[0]
        length = float(np.linalg.norm(direction))
        if length <= self.settings.native_tolerance_px:
            result['reasons'].append('DEGENERATE_THRESHOLD')
            return result
        direction /= length
        declared = list(dict.fromkeys(props.get('host_wall_candidate_ids', [])))
        jambs = list(dict.fromkeys(props.get('jamb_bracket_candidate_ids', [])))
        candidates = list(dict.fromkeys(declared+jambs))
        endpoint_supported = [False, False]
        thickness = []
        for identity in candidates:
            wall = self.entities.get(identity)
            detail = {'wall_id': identity, 'declared_host': identity in declared, 'supported': False}
            if not wall or wall['element_type'] != 'Wall' or not self.decisions[identity]['eligible']:
                detail['reason'] = 'HOST_NOT_ELIGIBLE_WALL'
            elif not self.resolved_evidence(wall):
                detail['reason'] = 'HOST_EVIDENCE_UNRESOLVED'
            else:
                wall_props = wall['geometry_properties']
                axis = wall_props.get('orientation')
                tangent = np.array([1, 0]) if axis == 'H' else np.array([0, 1]) if axis == 'V' else None
                if tangent is None:
                    detail['reason'] = 'HOST_DIRECTION_UNRESOLVED'
                else:
                    angle = math.degrees(math.acos(min(1., abs(float(direction@tangent)))))
                    local_thickness = wall_props.get('thickness_estimate_px')
                    if local_thickness is None:
                        bounds = wall['geometry_native'].get('bbox_native')
                        local_thickness = min(bounds[2]-bounds[0], bounds[3]-bounds[1]) if bounds else 0
                    tolerance = self.settings.native_tolerance_px + float(local_thickness)/2
                    distances = [_point_on_ring_distance(point, wall['geometry_native'].get('polygons_native', [])) for point in segment]
                    supported = [distance <= tolerance for distance in distances]
                    detail.update(direction_error_degrees=angle, endpoint_to_band_distance_native_px=[d if math.isfinite(d) else None for d in distances], tolerance_native_px=tolerance)
                    detail['supported'] = angle <= self.settings.parallel_tolerance_degrees and any(supported)
                    if detail['supported']:
                        endpoint_supported = [a or b for a, b in zip(endpoint_supported, supported)]
                        thickness.append(float(local_thickness))
                    else:
                        detail['reason'] = 'HOST_DIRECTION_OR_JAMB_LOCATION_MISMATCH'
            result['hosts'].append(detail)
        result['endpoint_supported'] = endpoint_supported
        result['local_host_thickness_px'] = max(thickness, default=0.)
        if not declared or not all(endpoint_supported) or not any(d['supported'] and d['declared_host'] for d in result['hosts']):
            result['reasons'].append('BOTH_JAMBS_AND_DECLARED_HOST_NOT_GEOMETRICALLY_SUPPORTED')
        result['eligible'] = not result['reasons']
        return result

    def infer(self, spaces, spatial_evidence, barriers, physical, context, active_openings):
        """Use opposite-side ownership and sampled physical passage, never proximity."""
        x0, y0, _, _ = context
        evidence_by_id = {e['evidence_id']: e for e in spatial_evidence}
        space_by_id = {s['space_id']: s for s in spaces}
        by_component = defaultdict(list)
        for space in spaces:
            for ref in space['evidence_refs']:
                component = evidence_by_id.get(ref, {}).get('component_evidence') or {}
                if component.get('component_id'):
                    by_component[component['component_id']].append(space['space_id'])
        relations, claims, evidence, attempts = [], [], [], []
        prefix = f"{self.binding['frame_id']}:spatial:{self.binding.get('analysis_run_id', 'derived')}:ontology"

        def add(kind, source, target, status, geometry_proof, refs, **extras):
            if source == target:
                raise ValueError('self spatial relation')
            def category(identity):
                if identity in space_by_id: return 'Space'
                if identity in self.entities: return self.entities[identity]['element_type']
                return self.observations[identity]['observation_type']
            validate_signature(kind, category(source), category(target))
            ident = prefix+f':relation:{len(relations):05d}'
            eid = ident+':evidence'
            evidence.append({'evidence_id': eid, 'source_role': self.binding['source_role'], 'frame_id': self.binding['frame_id'], 'geometry_evidence': geometry_proof, 'source_evidence_refs': sorted(set(refs)), 'algorithm': self.settings.version, 'ontology_rule': ONTOLOGY_IRI+kind, 'architectural_GT_verified': False})
            relation = {'relation_id': ident, 'source_role': self.binding['source_role'], 'frame_id': self.binding['frame_id'], 'relation_type': kind, 'source_id': source, 'target_id': target, 'evidence_refs': [eid], 'claim_status': 'PROPOSED', 'geometry_status': status, 'confidence': None, 'confidence_semantics': 'not a calibrated probability', 'verification_status': 'PROPOSED', 'rule_version': RULE_VERSION, **extras}
            relations.append(relation)
            claims.append({'id': ident+':claim:geometry', 'subject': ident, 'predicate': 'geometry_supports_relation', 'value': True if status == 'SUPPORTED' else None, 'status': status, 'scope': 'same-frame geometry under source hypotheses; architectural relation remains PROPOSED', 'evidence_ids': [eid], 'criteria_version': self.settings.version})
            return relation

        for space in spaces:
            name = space.get('name_observation')
            if name and space.get('seed_native') and name['text_observation_id'] in self.observations:
                add('LabelAssociation', name['text_observation_id'], space['space_id'], 'PROPOSED', {'seed_native': space['seed_native'], 'text_bbox_native': name['ocr_bbox_native'], 'space_boundary_is_not_text_bbox': True, 'boundary_status': space['boundary_status']}, space['evidence_refs']+space['name_evidence_refs'])
            grouped = defaultdict(list)
            for segment in space['boundary_segments_native']:
                grouped[segment['geometry_id']].append(segment)
            for identity, segments in grouped.items():
                entity = self.entities.get(identity)
                if not entity or entity['element_type'] not in {'Wall', 'Door', 'FireDoor', 'Opening', 'Window'}:
                    attempts.append({'kind': 'BoundaryAssociation', 'space_id': space['space_id'], 'geometry_id': identity, 'status': 'DEFERRED', 'reasons': ['BOUNDARY_TARGET_NOT_SUPPORTED_ONTOLOGY_TYPE']})
                    continue
                distances = []
                for segment in segments:
                    for point in _samples(segment['points_native']):
                        if entity['element_type'] == 'Wall':
                            distance = _point_on_ring_distance(point, entity['geometry_native'].get('polygons_native', []))
                        else:
                            threshold = entity['geometry_properties'].get('opening_segment_native')
                            distance = _segment_distance(point, threshold) if threshold else math.inf
                        distances.append(distance)
                support = sum(d <= self.settings.native_tolerance_px for d in distances)/max(1, len(distances))
                resolved = self.resolved_evidence(entity) and self.decisions[identity]['eligible']
                if support < self.settings.minimum_boundary_support or not resolved:
                    attempts.append({'kind': 'BoundaryAssociation', 'space_id': space['space_id'], 'geometry_id': identity, 'status': 'DEFERRED', 'reasons': ['BAND_FACE_OR_EVIDENCE_NOT_SUPPORTED'], 'boundary_sample_support': support})
                    continue
                add('BoundaryAssociation', space['space_id'], identity, 'SUPPORTED', {'sample_support_fraction': support, 'sample_count': len(distances), 'maximum_distance_native_px': max(distances), 'segments_native': segments, 'is_complete_space_boundary': space['polygon_native'] is not None}, space['evidence_refs']+entity['evidence_refs'])

        active = {o['geometry_id'] for o in active_openings}
        for opening in self.entities.values():
            kind = opening['element_type']
            if kind not in {'Door', 'FireDoor', 'Opening', 'Window'}:
                if kind == 'Unknown' and opening['geometry_properties'].get('classification') == 'WINDOW_OR_SLIDING_TRACK':
                    attempts.append({'kind': 'Connection', 'via_element_id': opening['entity_id'], 'status': 'DEFERRED', 'reasons': ['TRACK_SUBTYPE_UNKNOWN_NOT_A_CONFIRMED_PASSAGE']})
                continue
            host = self.host_assessment(opening)
            for detail in host['hosts']:
                if detail.get('supported') and detail['declared_host'] and host['eligible']:
                    wall = self.entities[detail['wall_id']]
                    add('HostingRelation', wall['entity_id'], opening['entity_id'], 'SUPPORTED', detail, wall['evidence_refs']+opening['evidence_refs'])
            if kind == 'Window':
                attempts.append({'kind': 'Connection', 'via_element_id': opening['entity_id'], 'status': 'NOT_APPLICABLE', 'reasons': ['WINDOW_IS_NOT_A_PASSAGE']})
                continue
            if kind == 'Opening':
                attempts.append({'kind': 'Connection', 'via_element_id': opening['entity_id'], 'status': 'DEFERRED', 'reasons': ['OPENING_SUBTYPE_OR_WALK_THROUGH_SEMANTICS_NOT_OBSERVED'], 'host_geometry': host})
                continue
            attempt = {'kind': 'Connection', 'via_element_id': opening['entity_id'], 'status': 'DEFERRED', 'host_geometry': host, 'reasons': list(host['reasons'])}
            if opening['entity_id'] not in active:
                attempt['reasons'].append('NO_SUPPORTED_DOOR_THRESHOLD_SEPARATOR')
            if not host['eligible'] or opening['entity_id'] not in active:
                attempts.append(attempt)
                continue
            segment = np.asarray(opening['geometry_properties']['opening_segment_native'], float)
            tangent = segment[1]-segment[0]; tangent /= np.linalg.norm(tangent)
            normal = np.array([-tangent[1], tangent[0]])
            offset = max(self.settings.native_tolerance_px*2, host['local_host_thickness_px']/2+self.settings.native_tolerance_px+1)
            sides = [[], []]; physical_support = []; probes = []
            mask = physical['total']; height, width = mask.shape
            for fraction in self.settings.side_probe_fractions:
                center = segment[0]+fraction*(segment[1]-segment[0])
                pair = [center-normal*offset, center+normal*offset]
                identities = []
                for index, point in enumerate(pair):
                    px, py = np.rint(point-[x0, y0]).astype(int)
                    component = int(barriers['components'][py, px]) if 0 <= px < width and 0 <= py < height else 0
                    owners = by_component.get(component, [])
                    good = [sid for sid in owners if space_by_id[sid]['boundary_status'] not in {'AMBIGUOUS', 'UNRESOLVED'}]
                    owner = good[0] if len(owners) == len(good) == 1 else None
                    sides[index].append(owner); identities.append({'point_native': point.tolist(), 'component_id': component, 'space_ids': list(owners), 'unique_owner': owner})
                samples = _samples(pair, max(3, int(offset*4)+1))
                pixels = np.rint(samples-[x0, y0]).astype(int)
                in_frame = (pixels[:, 0]>=0)&(pixels[:, 0]<width)&(pixels[:, 1]>=0)&(pixels[:, 1]<height)
                free = bool(in_frame.all() and np.all(mask[pixels[:, 1], pixels[:, 0]] == 0))
                physical_support.append(free); probes.append({'fraction': fraction, 'sides': identities, 'physical_wall_free': free})
            owners = [set(side) for side in sides]
            unique = all(len(group) == 1 and None not in group for group in owners)
            distinct = unique and next(iter(owners[0])) != next(iter(owners[1]))
            attempt.update(probes=probes, native_normal_offset_px=offset, no_physical_wall_at_threshold=all(physical_support))
            if not distinct:
                attempt['reasons'].append('OPPOSITE_SIDES_DO_NOT_HAVE_TWO_DISTINCT_UNIQUE_SPACE_OWNERS')
            if not all(physical_support):
                attempt['reasons'].append('PHYSICAL_BAND_OR_CONTEXT_EDGE_BLOCKS_THRESHOLD')
            if not attempt['reasons']:
                a, b = next(iter(owners[0])), next(iter(owners[1]))
                incomplete = any(space_by_id[sid]['boundary_status'] != 'CLOSED_CANDIDATE' or space_by_id[sid]['boundary_truncated'] for sid in (a, b))
                status = 'UNCERTAIN' if incomplete else 'SUPPORTED'
                relation = add('Connection', a, b, status, {'host_assessment': host, 'probes': probes, 'native_normal_offset_px': offset, 'space_ownership': 'three threshold samples; distinct single conditional owners; never nearest label', 'partial_space_association': incomplete}, opening['evidence_refs']+space_by_id[a]['evidence_refs']+space_by_id[b]['evidence_refs'], via_element_id=opening['entity_id'], mode='Door', uncertainty_reasons=['PARTIAL_SPACE_OWNERSHIP'] if incomplete else ['ARCHITECTURAL_TYPES_AND_RELATION_NOT_GT_VERIFIED'])
                attempt.update(status='PROPOSED', relation_id=relation['relation_id'])
            attempts.append(attempt)
        return {'relations': relations, 'claims': claims, 'evidence': evidence, 'attempts': attempts, 'ontology': {'iri': ONTOLOGY_IRI, 'rule_version': RULE_VERSION, 'rule_sha256': rule_digest(), 'vocabulary_sha256':vocabulary_digest(), 'inference': 'CLOSED_WORLD_GEOMETRY_GUARDED_RULES_NOT_OWL_COMPLETION', 'settings': asdict(self.settings), 'counts': dict(Counter(r['relation_type'] for r in relations)), 'geometry_status_counts': dict(Counter(r['geometry_status'] for r in relations)), 'GT_verified_relations': 0}}
