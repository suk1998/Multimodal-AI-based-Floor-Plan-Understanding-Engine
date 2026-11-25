"""Validate/serialize region-stage hypotheses, not architectural detections.

No gap closing, segmentation, GT generation, OWL reasoner or live API integration.
Operations describe provenance of supplied results; they do not execute geometry.
"""
import json
import math
from urllib.parse import quote
from rdflib import Graph, Literal, Namespace
from rdflib.namespace import RDF, XSD

from . import understanding_contract_v0_4 as contract
from .schema_v0_4 import U

DATA = Namespace('https://example.org/fpue/understanding/0.4/data/')
GROUPS = ('observations', 'entities', 'relations', 'claims', 'derivations', 'alternatives', 'external_evidence')


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def _index(records):
    result = {}
    if not isinstance(records, list):
        raise ValueError('record groups must be lists')
    for record in records:
        if not isinstance(record, dict) or not isinstance(record.get('id'), str) or not record['id'].strip() or record['id'] in result:
            raise ValueError('unique nonempty record ID required')
        result[record['id']] = record
    return result


def _geometry(geometry, binding):
    if geometry is None:
        return
    if not isinstance(geometry, dict) or geometry.get('coordinate_system') != 'image_native_pixels' or geometry.get('frame_id') != binding['frame_id']:
        raise ValueError('native geometry/frame required')
    kind = geometry.get('kind')
    if kind == 'Polygon':
        rings = geometry.get('outer_rings', []) + geometry.get('holes', [])
        if not geometry.get('outer_rings'):
            raise ValueError('polygon needs outer rings')
    elif kind in {'Segment', 'Polyline', 'Point'}:
        rings = [geometry.get('points_native', [])]
    else:
        raise ValueError('unsupported geometry kind')
    for ring in rings:
        if not isinstance(ring, list) or not ring or len(ring) > 1000:
            raise ValueError('bounded point list required')
        for point in ring:
            if not isinstance(point, list) or len(point) != 2 or any(type(v) not in (int, float) or not math.isfinite(v) for v in point):
                raise ValueError('finite native points required')
            if not (0 <= point[0] <= binding['width'] and 0 <= point[1] <= binding['height']):
                raise ValueError('geometry outside native image')
        if kind == 'Point' and len(ring) != 1 or kind == 'Segment' and len(ring) != 2 or kind == 'Polyline' and len(ring) < 2:
            raise ValueError('invalid point count')
        if kind == 'Segment' and ring[0] == ring[1]:
            raise ValueError('zero-length segment')
        if kind == 'Polygon':
            points = ring[:-1] if ring[0] == ring[-1] else ring
            if len(set(map(tuple, points))) < 3:
                raise ValueError('degenerate polygon')
            area = sum(p[0] * points[(i + 1) % len(points)][1] - points[(i + 1) % len(points)][0] * p[1] for i, p in enumerate(points))
            if area == 0:
                raise ValueError('zero-area polygon')
            def cross(a, b, c):
                return (b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0])
            for i, a in enumerate(points):
                b = points[(i + 1) % len(points)]
                for j in range(i + 2, len(points)):
                    if (j + 1) % len(points) == i:
                        continue
                    c, d = points[j], points[(j + 1) % len(points)]
                    if cross(a, b, c)*cross(a, b, d) < 0 and cross(c, d, a)*cross(c, d, b) < 0:
                        raise ValueError('self-intersecting polygon')


def dependencies(graph):
    """Direct premise IDs. These edges are not independent verification."""
    result = {record['id']: set(record.get('source_evidence_refs', [])) for group in GROUPS for record in graph.get(group, [])}
    for relation in graph.get('relations', []):
        result[relation['id']].update([relation['source_id'], relation['target_id']])
        if relation.get('via_entity_id'):
            result[relation['id']].add(relation['via_entity_id'])
    for claim in graph.get('claims', []):
        result[claim['id']].add(claim['subject_id'])
        result[claim['id']].update(claim.get('verification_record', {}).get('independent_evidence_ids', []))
    for derivation in graph.get('derivations', []):
        result[derivation['id']].update(derivation['input_ids'])
        for identity in derivation['output_ids']:
            result[identity].add(derivation['id'])
    for alternative in graph.get('alternatives', []):
        result[alternative['id']].update(alternative['member_ids'])
    return result


def validate_graph(graph):
    if not isinstance(graph, dict) or graph.get('schema_version') != contract.VERSION:
        raise ValueError('v0.4 region-stage contract required')
    _json(graph)
    binding = graph.get('source_binding', {})
    required = {'source_role', 'frame_id', 'source_sha256', 'image_sha256', 'page', 'extraction_zoom', 'width', 'height', 'origin'}
    if not isinstance(binding, dict) or not required <= binding.keys():
        raise ValueError('complete own-source binding required')
    if binding['source_role'] not in {'Ref', 'Target'} or not isinstance(binding['frame_id'], str) or not binding['frame_id']:
        raise ValueError('role/frame required')
    if binding['origin'] not in {'CURRENT_INPUT', 'SYNTHETIC_CONTRACT_TEST'}:
        raise ValueError('explicit actual input or synthetic fixture origin required')
    for name in ['source_sha256', 'image_sha256']:
        if not isinstance(binding[name], str) or len(binding[name]) != 64 or any(c not in '0123456789abcdef' for c in binding[name]):
            raise ValueError('SHA-256 binding required')
    if any(type(binding[name]) is not int or binding[name] <= 0 for name in ['page', 'width', 'height']):
        raise ValueError('positive integer page/image dimensions required')
    if type(binding['extraction_zoom']) not in (int, float) or not math.isfinite(binding['extraction_zoom']) or binding['extraction_zoom'] <= 0:
        raise ValueError('positive extraction zoom required')
    groups = {group: _index(graph.get(group, [])) for group in GROUPS}
    records = {identity: record for group in groups.values() for identity, record in group.items()}
    if len(records) != sum(len(group) for group in groups.values()):
        raise ValueError('IDs collide between groups')
    for record in records.values():
        if record.get('source_role') != binding['source_role'] or record.get('frame_id') != binding['frame_id']:
            raise ValueError('mixed Ref/Target/native frame')
        _geometry(record.get('geometry'), binding)
        for reference in record.get('source_evidence_refs', []):
            if reference not in groups['observations'] and reference not in groups['external_evidence']:
                raise ValueError('source evidence must resolve to own observation/external evidence')
    entities, claims = groups['entities'], groups['claims']
    for entity in entities.values():
        kind = entity.get('expected_type')
        if kind not in contract.ENTITY_PARENTS or entity.get('grounding_status') not in contract.GROUNDING_STATES:
            raise ValueError('entity type and grounding required')
        if entity.get('geometry_state') not in contract.GEOMETRY_STATES:
            raise ValueError('geometry state is separate from claim/region kind')
        if entity['geometry_state'] == 'POLYGON_AVAILABLE' and (entity.get('geometry') or {}).get('kind') != 'Polygon':
            raise ValueError('available polygon geometry required')
        if entity.get('grounding_status') == 'UNBOUND' and entity.get('geometry') is not None:
            raise ValueError('native geometry cannot be UNBOUND')
        if contract.is_a(kind, 'SpatialRegion') and entity.get('region_kind') not in contract.REGION_KINDS:
            raise ValueError('region kind required')
        if kind == 'UnassignedRegion' and entity['region_kind'] != 'UNASSIGNED':
            raise ValueError('residual region must remain unassigned')
        if contract.is_a(kind, 'Boundary'):
            if entity.get('boundary_origin') not in contract.BOUNDARY_ORIGINS:
                raise ValueError('boundary origin required')
            if contract.is_a(kind, 'InferredBoundary') and entity['boundary_origin'] == 'OBSERVED_FACE':
                raise ValueError('inferred boundary is not an observed physical face')
        if kind == 'ClosureBoundary' and (entity['boundary_origin'] != 'INFERRED_CLOSURE' or entity.get('gap_interpretation') not in contract.GAP_INTERPRETATIONS):
            raise ValueError('closure and gap interpretation must be explicit')
    for relation in groups['relations'].values():
        a, b = relation.get('source_id'), relation.get('target_id')
        if a not in entities or b not in entities or a == b:
            raise ValueError('distinct own-source entity endpoints required')
        contract.validate_relation_signature(relation.get('kind'), entities[a]['expected_type'], entities[b]['expected_type'])
        if relation['kind'] == 'Connection':
            mode = relation.get('mode')
            if mode not in contract.RELATION_CONTRACTS['Connection']['modes']:
                raise ValueError('connection mode required')
            for field, predicate in [('existence_claim_id', 'connection_existence'), ('mode_claim_id', 'connection_mode')]:
                claim = claims.get(relation.get(field), {})
                if claim.get('subject_id') != relation['id'] or claim.get('predicate') != predicate:
                    raise ValueError('separate connection existence/mode claims required')
            if claims[relation['mode_claim_id']].get('value') != mode:
                raise ValueError('mode/claim mismatch')
            via = relation.get('via_entity_id')
            if via is not None and (via not in entities or not any(contract.is_a(entities[via]['expected_type'], t) for t in ['Door', 'Opening'])):
                raise ValueError('connection via cannot be Window/Grille or a code literal')
            if mode == 'Door' and (via is None or not contract.is_a(entities[via]['expected_type'], 'Door')):
                raise ValueError('Door-mode hypothesis requires a separate Door hypothesis')
    for claim in claims.values():
        if claim.get('subject_id') not in entities.keys() | groups['relations'].keys() or claim.get('status') not in contract.CLAIM_STATES:
            raise ValueError('claim subject/status required')
        if not isinstance(claim.get('predicate'), str) or 'value' not in claim or not claim.get('scope'):
            raise ValueError('scoped claim predicate/value required')
        if claim['predicate'] == 'connection_existence' and claim['value'] not in contract.EXISTENCE_VALUES:
            raise ValueError('existence is separate from connection mode')
        if claim['status'] == 'SUPPORTED' and not claim.get('source_evidence_refs'):
            raise ValueError('SUPPORTED claim needs source evidence')
        if claim['status'] == 'VERIFIED' or claim.get('verification_record'):
            proof = claim.get('verification_record', {})
            refs = proof.get('independent_evidence_ids', [])
            if not isinstance(proof.get('criteria_version'), str) or not proof['criteria_version'] or not isinstance(proof.get('verified_scope'), str) or not proof['verified_scope'] or not refs or any(ref not in groups['external_evidence'] for ref in refs):
                raise ValueError('VERIFIED needs criteria/scope and separate external evidence, not its own derivation')
            if any(groups['external_evidence'][ref].get('evidence_origin') != 'INDEPENDENT_REVIEW' or groups['external_evidence'][ref].get('source_evidence_refs') for ref in refs):
                raise ValueError('independent review provenance required; derived evidence cannot verify its own inputs')
        subject = entities.get(claim['subject_id'], {})
        if contract.is_a(subject.get('expected_type'), 'InferredBoundary') and claim['predicate'] == 'wall_exists' and claim['value'] is True:
            raise ValueError('closing boundary cannot assert a physical wall')
    for alternative in groups['alternatives'].values():
        members = alternative.get('member_ids', [])
        if len(set(members)) < 2 or any(identity not in entities and identity not in groups['relations'] for identity in members):
            raise ValueError('alternative set needs at least two distinct own hypotheses')
    for derivation in groups['derivations'].values():
        operation = derivation.get('operation')
        if operation not in contract.OPERATIONS:
            raise ValueError('known derivation operation required')
        inputs, outputs = derivation.get('input_ids', []), derivation.get('output_ids', [])
        if not inputs or not outputs or len(inputs) != len(set(inputs)) or len(outputs) != len(set(outputs)) or any(i not in records for i in inputs) or any(i not in entities for i in outputs):
            raise ValueError('resolved unique derivation input/output IDs required')
        permitted = contract.OPERATION_CONTRACTS[operation]['outputs']
        if any(entities[i]['expected_type'] not in permitted for i in outputs):
            raise ValueError('operation output violates physical/region semantics')
        if operation == 'FRAGMENT_CLOSURE':
            if len(inputs) < 2 or any(i not in groups['observations'] and not (i in entities and contract.is_a(entities[i]['expected_type'], 'Boundary')) for i in inputs):
                raise ValueError('closure inputs are observed fragments or boundary hypotheses')
            if any(i in groups['observations'] and groups['observations'][i].get('observation_type') not in {'Line', 'Polyline', 'Segment', 'BoundaryFragment'} for i in inputs):
                raise ValueError('text boxes/symbols alone cannot be closure fragments')
            if not any(entities[i]['expected_type'] == 'ClosureBoundary' for i in outputs):
                raise ValueError('closure must expose inferred closing edges, not hide them in a polygon')
        if operation in {'FREE_DOMAIN', 'RESIDUAL_DIFFERENCE'}:
            operands = derivation.get('operands', {})
            referenced = []
            for field, expected in contract.OPERATION_CONTRACTS[operation]['operands'].items():
                values = operands.get(field)
                if field in {'domain', 'free_region'} and not isinstance(values, str) or field in {'occupied', 'cores', 'partitions'} and not isinstance(values, list):
                    raise ValueError('scalar domain and explicit operand lists required')
                values = values if isinstance(values, list) else [values]
                if values == [None]:
                    raise ValueError('named operation operands required')
                for identity in values:
                    if identity not in entities or not contract.is_a(entities[identity]['expected_type'], expected):
                        raise ValueError('typed operation operands required')
                referenced.extend(values)
            if set(referenced) != set(inputs):
                raise ValueError('operation input list must match named operands')
        if operation == 'RESIDUAL_DIFFERENCE':
            if derivation.get('inventory_complete') is not False or 'unknown_coverage' not in derivation:
                raise ValueError('prototype residuals require explicit partial inventory and unknown coverage')
    graph_dependencies = dependencies(graph)
    visiting, visited = set(), set()
    def visit(identity):
        if identity in visiting:
            raise ValueError('cyclic premises or self-supporting closure')
        if identity in visited:
            return
        visiting.add(identity)
        for parent in graph_dependencies[identity]:
            visit(parent)
        visiting.remove(identity)
        visited.add(identity)
    for identity in graph_dependencies:
        visit(identity)
    return graph


def invalidation_plan(graph, changed_ids):
    """Compute a STALE plan without modifying observations, claims or the graph."""
    validate_graph(graph)
    links = dependencies(graph)
    changed = set(changed_ids)
    if not changed <= links.keys():
        raise ValueError('unknown changed record')
    stale = set(changed)
    # A changed property claim conservatively invalidates its subject snapshot.
    # This is a plan; the runtime may later recompute only the affected property.
    stale.update(claim['subject_id'] for claim in graph.get('claims', []) if claim['id'] in changed)
    while True:
        additions = {identity for identity, parents in links.items() if parents & stale} - stale
        if not additions:
            break
        stale.update(additions)
    return {'changed_ids': sorted(changed), 'stale_ids': sorted(stale-changed),
        'state': 'STALE', 'applied': False, 'source_role': graph['source_binding']['source_role']}


def plan_review_requests(graph):
    validate_graph(graph)
    requests = []
    for entity in graph.get('entities', []):
        if entity['expected_type'] == 'ClosureBoundary' and entity['gap_interpretation'] == 'UNKNOWN':
            requests.append({'subject_id': entity['id'], 'engine': 'GEOMETRIC', 'question': 'INTERPRET_CLOSURE_GAP'})
        if entity['expected_type'] == 'UnassignedRegion':
            requests.append({'subject_id': entity['id'], 'engine': 'SPATIAL', 'question': 'CHECK_RESIDUAL_COVERAGE'})
    for relation in graph.get('relations', []):
        if relation['kind'] == 'Connection' and relation['mode'] == 'OpenPlan':
            requests.append({'subject_id': relation['id'], 'engine': 'RELATIONAL', 'question': 'CHECK_SHARED_REGION_AND_DIRECT_CONNECTION'})
    for request in requests:
        request.update(source_role=graph['source_binding']['source_role'], frame_id=graph['source_binding']['frame_id'],
                       executable=False, status='PENDING_EVIDENCE')
    return requests


def to_rdf(graph):
    validate_graph(graph)
    result = Graph()
    result.bind('u', U)
    result.bind('data', DATA)
    binding = graph['source_binding']
    def iri(identity, category='record'):
        return DATA[quote(binding['source_role']+':'+binding['frame_id'], safe='')+'/'+category+'/'+quote(identity, safe='')]
    snapshot = iri('snapshot', 'snapshot')
    result.add((snapshot, RDF.type, U.GraphSnapshot))
    result.add((snapshot, U.snapshotContextJSON, Literal(_json(binding))))
    def common(record, kind):
        node = iri(record['id'])
        result.add((node, RDF.type, U[kind]))
        result.add((node, U.recordId, Literal(record['id'])))
        result.add((node, U.inSnapshot, snapshot))
        result.add((node, U.recordJSON, Literal(_json(record))))
        result.add((node, U.sourceRole, Literal(record['source_role'])))
        result.add((node, U.frameId, Literal(record['frame_id'])))
        result.add((node, U.sourceEvidenceJSON, Literal(_json(record.get('source_evidence_refs', [])))))
        return node
    for observation in graph.get('observations', []):
        node = common(observation, 'ObservationRecord')
        result.add((node, U.observationType, Literal(observation.get('observation_type', 'Unknown'))))
    for evidence in graph.get('external_evidence', []):
        common(evidence, 'ExternalEvidenceRecord')
    for entity in graph.get('entities', []):
        kind = entity['expected_type']
        record_type = 'RegionHypothesis' if contract.is_a(kind, 'SpatialRegion') else 'SpaceHypothesis' if contract.is_a(kind, 'Space') else 'BoundaryHypothesis' if contract.is_a(kind, 'Boundary') else 'OpeningHypothesis' if kind == 'Opening' else 'ElementHypothesis' if contract.is_a(kind, 'BuildingElement') else 'ObservationHypothesis'
        node = common(entity, record_type)
        result.add((node, U.proposedEntityType, U['EntityType_'+kind]))
        result.add((node, U.groundingStatus, U['GroundingStatus_'+entity['grounding_status']]))
        result.add((node, U.geometryState, U['GeometryState_'+entity['geometry_state']]))
        for field, predicate, prefix in [('region_kind', 'regionKind', 'RegionKind_'),
            ('boundary_origin', 'boundaryOrigin', 'BoundaryOrigin_'), ('gap_interpretation', 'gapInterpretation', 'GapInterpretation_')]:
            if field in entity:
                result.add((node, U[predicate], U[prefix+entity[field]]))
        if entity.get('label'):
            result.add((node, U.labelHypothesis, Literal(entity['label'])))
        if entity.get('geometry') is not None:
            geo = iri(entity['id'], 'geometry')
            frame = iri('native-frame', 'frame')
            result.add((geo, RDF.type, U.GeometryEvidence))
            result.add((geo, U.geometryJSON, Literal(_json(entity['geometry']))))
            result.add((node, U.hypothesisGeometry, geo))
            result.add((geo, U.inFrame, frame))
            result.add((frame, RDF.type, U.CoordinateFrame))
            result.add((frame, U.frameId, Literal(binding['frame_id'])))
            result.add((frame, U.coordinateUnit, Literal('image_native_pixels')))
    for relation in graph.get('relations', []):
        node = common(relation, relation['kind']+'Hypothesis')
        result.add((node, U.relationSource, iri(relation['source_id'])))
        result.add((node, U.relationTarget, iri(relation['target_id'])))
        if relation['kind'] == 'Connection':
            result.add((node, U.connectionMode, U['ConnectionMode_'+relation['mode']]))
            result.add((node, U.existenceClaim, iri(relation['existence_claim_id'])))
            result.add((node, U.modeClaim, iri(relation['mode_claim_id'])))
            if relation.get('via_entity_id'):
                result.add((node, U.viaEntityHypothesis, iri(relation['via_entity_id'])))
    for claim in graph.get('claims', []):
        predicate = claim['predicate']
        record_type = 'ExistenceClaim' if predicate == 'connection_existence' else 'ConnectionModeClaim' if predicate == 'connection_mode' else 'Claim'
        node = common(claim, record_type)
        result.add((node, U.claimSubject, iri(claim['subject_id'])))
        result.add((iri(claim['subject_id']), U.hasClaim, node))
        result.add((node, U.claimStatus, U['ClaimStatus_'+claim['status']]))
        result.add((node, U.claimPredicate, Literal(predicate)))
        result.add((node, U.claimValueJSON, Literal(_json(claim['value']))))
        result.add((node, U.claimScope, Literal(claim['scope'])))
        if record_type == 'ExistenceClaim':
            result.add((node, U.existenceValue, U['ExistenceValue_'+claim['value']]))
        if claim.get('verification_record'):
            proof = claim['verification_record']
            verification = iri(claim['id'], 'verification')
            result.add((verification, RDF.type, U.VerificationRecord))
            result.add((node, U.hasVerification, verification))
            result.add((verification, U.criteriaVersion, Literal(proof['criteria_version'])))
            result.add((verification, U.verifiedScope, Literal(proof['verified_scope'])))
            for identity in proof['independent_evidence_ids']:
                result.add((verification, U.independentEvidence, iri(identity)))
    operation_types = {'FRAGMENT_CLOSURE': 'ClosureDerivation', 'FREE_DOMAIN': 'FreeDomainDerivation',
                       'RESIDUAL_DIFFERENCE': 'ResidualDifferenceDerivation', 'REGION_INTERPRETATION': 'RegionInterpretationDerivation'}
    for derivation in graph.get('derivations', []):
        node = common(derivation, operation_types[derivation['operation']])
        result.add((node, U.derivationOperation, U['DerivationOperation_'+derivation['operation']]))
        for identity in derivation['input_ids']:
            result.add((node, U.derivationInput, iri(identity)))
        for identity in derivation['output_ids']:
            result.add((node, U.derivationOutput, iri(identity)))
        for field, predicate in [('operands', 'namedOperandsJSON'), ('parameters', 'operationParametersJSON'), ('assumptions', 'assumptionsJSON')]:
            result.add((node, U[predicate], Literal(_json(derivation.get(field, {})))))
        if derivation['operation'] == 'RESIDUAL_DIFFERENCE':
            result.add((node, U.inventoryComplete, Literal(False, datatype=XSD.boolean)))
            result.add((node, U.unknownCoverageJSON, Literal(_json(derivation['unknown_coverage']))))
    for alternative in graph.get('alternatives', []):
        node = common(alternative, 'AlternativeHypothesisSet')
        for identity in alternative['member_ids']:
            result.add((node, U.memberHypothesis, iri(identity)))
    for identity, parents in dependencies(graph).items():
        for parent in parents:
            result.add((iri(identity), U.dependsOn, iri(parent)))
    return result
