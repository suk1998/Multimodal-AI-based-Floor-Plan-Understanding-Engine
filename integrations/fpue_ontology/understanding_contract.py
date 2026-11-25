"""v0.3 conceptual contract for ontology-guided understanding, not detections.

Versioned independently of the operational v0.2.2 Geometry rules. Example apartment
relationships are never rules and cannot complete a newly uploaded drawing.
"""
import hashlib
import json

VERSION = 'fpue_understanding_graph/0.3'
ONTOLOGY_IRI = 'https://example.org/fpue/understanding/0.3#'
ENTITY_PARENTS = {
    'SpatialEntity': None, 'Space': 'SpatialEntity', 'ServiceSpace': 'Space',
    'PipeShaft': 'ServiceSpace', 'BuildingElement': 'SpatialEntity',
    'Wall': 'BuildingElement', 'Door': 'BuildingElement', 'FireDoor': 'Door',
    'Window': 'BuildingElement', 'Grille': 'BuildingElement',
    'ShaftEnclosure': 'BuildingElement', 'UnknownBuildingElement': 'BuildingElement',
    'Opening': 'SpatialEntity', 'Boundary': 'SpatialEntity',
    'PhysicalBoundary': 'Boundary', 'LogicalBoundary': 'Boundary',
    'Observation': None, 'TextRegion': 'Observation', 'Symbol': 'Observation',
    'DimensionAnnotation': 'Observation', 'FurnitureObservation': 'Observation',
    'PrimitiveObservation': 'Observation',
}
RELATION_CONTRACTS = {
    'Connection': {'source': ['Space'], 'target': ['Space'], 'symmetric': True,
                   'via': ['Door', 'Opening'], 'modes': ['Door', 'OpenPassage', 'OpenPlan', 'Unknown'],
                   'evidence': ['two distinct space hypotheses', 'opening/access grounding', 'own source frame'],
                   'missing_evidence': 'KEEP_HYPOTHESIS_AND_REQUEST_GROUNDING'},
    'Adjacency': {'source': ['SpatialEntity'], 'target': ['SpatialEntity'], 'symmetric': True,
                  'evidence': ['nearby geometry/shared boundary'], 'implies_connection': False},
    'ElementAssociation': {'source': ['Space'], 'target': ['BuildingElement', 'Opening'],
                           'evidence': ['association observation'], 'implies_containment': False},
    'Containment': {'source': ['Space'], 'target': ['BuildingElement'],
                    'evidence': ['observed containment scope and geometry']},
    'BoundaryAssociation': {'source': ['Space'], 'target': ['Boundary'],
                            'evidence': ['physical face or logical boundary subset, never label bbox']},
    'BoundaryOfElement': {'source': ['PhysicalBoundary'], 'target': ['Wall', 'ShaftEnclosure'],
                          'evidence': ['observed face/segment and parent hypothesis']},
    'Hosting': {'source': ['Wall'], 'target': ['Opening'],
                'evidence': ['observed opening in host boundary']},
    'Installation': {'source': ['Door', 'Window', 'Grille'], 'target': ['Opening'],
                     'evidence': ['physical filler/assembly associated with observed opening']},
    'OpeningIncidentTo': {'source': ['Opening'], 'target': ['Space'],
                          'evidence': ['same-frame side/region support']},
    'LabelAssociation': {'source': ['TextRegion'], 'target': ['Space', 'Symbol'],
                         'evidence': ['own text and grounded referent; text extent is not room geometry']},
    'ReferenceAssociation': {'source': ['Symbol'], 'target': ['BuildingElement', 'Opening'],
                             'evidence': ['actual reference/leader/corresponding opening, not nearest code']},
}
CLAIM_STATES = ('PROPOSED', 'SUPPORTED', 'VERIFIED', 'CONFLICTED', 'REJECTED', 'STALE')
GROUNDING_STATES = ('UNBOUND', 'PARTIAL', 'GROUNDED')
ENGINE_PROFILES = {
    'GEOMETRIC': {
        'purpose': 'Group actual primitives into alternative Wall/Opening/Boundary hypotheses.',
        'inputs': ['PrimitiveObservation', 'TextRegion', 'Symbol'],
        'outputs': ['Wall', 'Opening', 'PhysicalBoundary'],
        'questions': ['WALL_VS_DIMENSION_OR_FURNITURE', 'LOCATE_OPENING_AND_HOST', 'MEASURE_BOUNDARY_SUPPORT'],
        'guard': 'No fabricated closure, physical endpoints, dimensions or material from an isolated line.'},
    'SPATIAL': {
        'purpose': 'Assemble partial spaces, service spaces, boundaries and access locations.',
        'inputs': ['Wall', 'Opening', 'PhysicalBoundary', 'LogicalBoundary'],
        'outputs': ['Space', 'PipeShaft', 'BoundaryAssociation', 'OpeningIncidentTo'],
        'questions': ['GROUND_SPACE_BOUNDARIES', 'GROUND_SERVICE_SPACE_REFERENCE', 'RESOLVE_ACCESS_LOCATION'],
        'guard': 'A name bbox is not a room; an unknown PS mention is not one globally shared shaft.'},
    'SEMANTIC': {
        'purpose': 'Interpret own labels, symbols, catalog-scoped codes and functional types.',
        'inputs': ['TextRegion', 'Symbol', 'Space', 'BuildingElement'],
        'outputs': ['LabelAssociation', 'ReferenceAssociation', 'ElementAssociation'],
        'questions': ['RESOLVE_CONNECTION_MODE', 'RESOLVE_HAS_VS_CONTAINS', 'RESOLVE_CATALOG_CODE'],
        'guard': 'Examples and code prefixes cannot establish new drawing facts or physical identity.'},
    'RELATIONAL': {
        'purpose': 'Assemble evidence-backed direct connections, incidences and alternative topology.',
        'inputs': ['Space', 'Opening', 'Boundary', 'Door', 'Window'],
        'outputs': ['Connection', 'Adjacency', 'Hosting', 'Installation'],
        'questions': ['GROUND_CONNECTION_ENDPOINTS', 'RESOLVE_REFERENT_IDENTITY', 'SEPARATE_IDENTITY_FROM_STABILITY'],
        'guard': 'Adjacency/reachability is not a direct Connection; missing relations are not removals.'},
}


def contract_spec():
    return {'version': VERSION, 'ontology_iri': ONTOLOGY_IRI, 'entity_parents': ENTITY_PARENTS,
            'relation_contracts': RELATION_CONTRACTS, 'engine_profiles': ENGINE_PROFILES,
            'claim_states': CLAIM_STATES, 'grounding_states': GROUNDING_STATES}


def contract_digest():
    return hashlib.sha256(json.dumps(contract_spec(), sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def is_a(kind, parent):
    while kind is not None:
        if kind == parent:
            return True
        kind = ENTITY_PARENTS.get(kind)
    return False


def validate_relation_signature(kind, source_kind, target_kind):
    contract = RELATION_CONTRACTS.get(kind)
    if not contract or not any(is_a(source_kind, p) for p in contract['source']) or not any(is_a(target_kind, p) for p in contract['target']):
        raise ValueError(f'{kind}: invalid v0.3 signature {source_kind} -> {target_kind}')


def plan_evidence_requests(graph):
    """Prepare per-engine questions only. No observations, geometry or facts added."""
    requests = []; seen = {}
    def add(engine, question, subjects, reason):
        key=(engine,question,tuple(subjects))
        if key in seen:
            record=seen[key]
            if reason!=record['reason'] and reason not in record.setdefault('additional_reasons',[]):record['additional_reasons'].append(reason)
            return
        requests.append({'id': graph['graph_id']+f':request:{len(requests):04d}',
                         'engine': engine, 'question': question, 'subject_ids': subjects,
                         'source_role': graph['context']['source_role'],
                         'source_binding': graph['context'].get('source_binding'),
                         'scope': 'SOURCE_DRAWING_ONLY', 'status': 'PENDING_EVIDENCE',
                         'executable': False, 'reason': reason,
                         'contract_version': VERSION})
        seen[key]=requests[-1]
    for entity in graph['entities']:
        if is_a(entity['expected_type'], 'Space') and not entity.get('geometry'):
            add('SPATIAL', 'GROUND_SPACE_BOUNDARIES', [entity['id']], 'Need own raster/vector boundary evidence; no OCR-box expansion.')
        if entity.get('referent_status') == 'UNRESOLVED':
            add('RELATIONAL', 'RESOLVE_REFERENT_IDENTITY', [entity['id']], 'Resolve PS/element mentions using own geometry, not label/code equality.')
        if entity.get('catalog_status') == 'UNREGISTERED':
            add('SEMANTIC', 'RESOLVE_CATALOG_CODE', [entity['id']], 'Keep raw code; request own schedule/catalog evidence.')
        if any(is_a(entity['expected_type'],kind) for kind in ['Door','Window','Grille']) and not entity.get('geometry'):
            add('GEOMETRIC', 'LOCATE_OPENING_AND_HOST', [entity['id']], 'An element/type-code mention has not localized its physical opening or host.')
        if is_a(entity['expected_type'],'Door') and not any(r.get('via_entity_id')==entity['id'] for r in graph['relations'] if r['kind']=='Connection'):
            add('RELATIONAL', 'GROUND_CONNECTION_ENDPOINTS', [entity['id']], 'A door association alone does not identify both spaces; no exterior/other room is invented.')
    for relation in graph['relations']:
        if relation['kind'] == 'Connection':
            if relation.get('mode', 'Unknown') == 'Unknown':
                add('SEMANTIC', 'RESOLVE_CONNECTION_MODE', [relation['id']], 'Unknown mode is not automatically an open passage.')
            add('RELATIONAL', 'GROUND_CONNECTION_ENDPOINTS', [relation['id']], 'Need same-frame opening and both space sides; polygon completion is not a prerequisite for a hypothesis.')
            add('GEOMETRIC', 'LOCATE_OPENING_AND_HOST', [relation.get('via_entity_id') or relation['id']], 'Locate physical opening/threshold; a type code is not a physical endpoint or opening.')
        if relation.get('association_semantics') == 'HAS_OR_CONTAINS_UNRESOLVED':
            add('SEMANTIC', 'RESOLVE_HAS_VS_CONTAINS', [relation['id']], 'Association is stated; physical containment has not been established.')
    for statement in graph['source_statements']:
        if statement.get('interpretation') == 'AMBIGUOUS_CHANGE_NARRATIVE':
            add('SPATIAL', 'RESOLVE_ACCESS_LOCATION', [statement['subject_id']], 'Preserve pantry existence claim and raw removal/direction wording; direction frame and changed property remain unresolved.')
    return requests
