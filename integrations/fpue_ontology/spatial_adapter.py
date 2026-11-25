"""Native adapter for the current architectural_spatial_bundle/1 contract."""
import copy
from collections import Counter

if __package__:
    from .rules import rule_digest
    from .bridge import ELEMENT_CLASSES, OBSERVATION_CLASSES, REVIEW, validate_relation
else:
    from rules import rule_digest
    from bridge import ELEMENT_CLASSES, OBSERVATION_CLASSES, REVIEW, validate_relation


def normalize_spatial_bundle(bundle, role, *, context=None, catalog_scope=None):
    if __package__:
        from .adapter import _common, _index, _validate_binding, ROLE_NAMES
    else:
        from adapter import _common, _index, _validate_binding, ROLE_NAMES
    if role not in ROLE_NAMES or bundle.get('source_role') != role:
        raise ValueError('Spatial bundle role must match the requested independent drawing')
    binding = bundle.get('source_binding')
    _validate_binding(binding, role)
    if binding.get('source_role') != role or binding['frame_id'] != bundle.get('frame_id'):
        raise ValueError('Spatial bundle native source/frame mismatch')
    if bundle.get('coordinate_system', {}).get('name') != 'image_native_pixels':
        raise ValueError('Spatial bundle must declare native pixel coordinates')
    if bundle.get('bundle_kind') != 'SPATIAL_MODEL_CANDIDATES':
        raise ValueError('Spatial candidate model required')
    context = context or {}
    frame = binding['frame_id']; warnings = []
    metadata = {key: copy.deepcopy(bundle.get(key)) for key in ('run_id', 'parent_geometry_run_id', 'parent_observation_run_id', 'analysis_scope', 'status', 'algorithm', 'ontology', 'coverage', 'policy', 'diagnostics', 'relation_attempts')}
    metadata['source_schema'] = bundle['schema_version']
    metadata['source_artifacts'] = copy.deepcopy(context.get('artifacts', []))
    metadata['evidence_registry'] = copy.deepcopy(bundle['evidence'])
    metadata['geometry_evidence_registry'] = copy.deepcopy(bundle['geometry_evidence'])
    metadata['no_detection_or_Geometry_verification_by_exporter'] = True
    data = {'schema_version': 'fpue_ontology_normalized/1', 'document': {'id': f"pdf:{binding['source_sha256']}:page:{binding['page']}", 'role': ROLE_NAMES[role], 'catalog_scope': catalog_scope or 'unresolved-catalog:'+binding['source_sha256'], 'source_binding': copy.deepcopy(binding), 'frame_registry': copy.deepcopy(bundle.get('frame_registry')), 'coordinate_frame': {'id': frame, 'kind': 'image_native_pixels', 'width': binding['width'], 'height': binding['height'], 'axis_definition': 'origin=top-left;+x=right;+y=down;geographic-north=unknown'}, 'metadata': metadata}, 'spaces': [], 'elements': [], 'observations': [], 'relations': [], 'claims': [], 'deferred_records': []}

    def common(record, key):
        if record.get('source_role') != role or record.get('frame_id') != frame:
            raise ValueError(f'{record.get(key)}: mixed Spatial role/frame')
        normalized = _common({**record, 'id': record[key]}, role, frame, warnings)
        normalized['source_record'] = copy.deepcopy(record)
        normalized['evidence_ids'] = copy.deepcopy(record.get('evidence_refs', record.get('observation_refs', [])))
        return normalized

    def extent(record, bounds):
        if bounds is None:
            return None
        if len(bounds) == 4 and bounds[0] < bounds[2] and bounds[1] < bounds[3]:
            return copy.deepcopy(bounds)
        warnings.append({'id': record['id'], 'reason': 'line/point extent has no positive area; native geometry retained, no rectangle invented'})
        return None

    for space in bundle['spaces']:
        record = common(space, 'space_id')
        record['geometry_native'] = {key: copy.deepcopy(space[key]) for key in ('seed_native', 'polygon_native', 'centroid_native', 'bbox_native', 'boundary_segments_native', 'partial_boundary_extent_native') if key in space}
        record['geometry_native']['coordinate_system'] = 'image_native_pixels'
        record['bbox'] = extent(record, space.get('bbox_native'))
        name = space.get('name_observation')
        if name and name.get('raw_text'):
            record['raw_label'] = name['raw_text']
        record['label_state'] = 'UNKNOWN'
        record['boundary_status'] = space.get('boundary_status')
        data['spaces'].append(record)
    for entity in bundle['geometry_candidates']:
        record = common(entity, 'entity_id')
        record['geometry_native'] = copy.deepcopy(entity['geometry_native'])
        record['bbox'] = extent(record, entity['geometry_native'].get('bbox_native'))
        kind = entity['element_type']
        if kind in ELEMENT_CLASSES:
            record['category'] = kind
            if entity.get('type_code') is not None: record['type_code'] = entity['type_code']
            data['elements'].append(record)
        else:
            record['category'] = 'UnclassifiedObservation'
            record['source_category'] = kind
            data['observations'].append(record)
    for observation in bundle['observations']:
        record = common(observation, 'observation_id')
        record['geometry_native'] = copy.deepcopy(observation['geometry_native'])
        kind = observation['observation_type']
        record['category'] = kind if kind in OBSERVATION_CLASSES else 'GeometryObservation'
        if kind == 'TextRegion':
            record['raw_text'] = observation['source_evidence'].get('text')
            record['recognition_confidence'] = observation.get('confidence')
            record.pop('confidence', None)
        data['observations'].append(record)
    spaces, elements, observations = (_index(data[k], k) for k in ('spaces', 'elements', 'observations'))
    for relation in bundle.get('relations', []):
        record = common(relation, 'relation_id')
        record.update(kind=relation['relation_type'], source_id=relation['source_id'], target_id=relation['target_id'])
        for key in ('via_element_id', 'mode', 'geometry_status', 'uncertainty_reasons'):
            if key in relation: record[key] = copy.deepcopy(relation[key])
        try:
            validate_relation(record, spaces, elements, observations)
        except ValueError as error:
            data['deferred_records'].append({'id': record['id'], 'reason': str(error), 'source_record': copy.deepcopy(relation)})
        else:
            data['relations'].append(record)
    subjects = {r['id'] for key in ('spaces', 'elements', 'observations', 'relations') for r in data[key]}
    for claim in bundle.get('claims', []):
        if claim.get('subject') not in subjects:
            data['deferred_records'].append({'id': claim['id'], 'reason': 'claim subject unavailable or deferred', 'source_record': copy.deepcopy(claim)})
            continue
        status = claim.get('status', 'UNKNOWN')
        data['claims'].append({**copy.deepcopy(claim), 'status': status if status in REVIEW else 'UNKNOWN', 'source_record': copy.deepcopy(claim)})
    # All groups must be unique, not silently overwritten by dict construction.
    records = [r for key in ('spaces', 'elements', 'observations', 'relations', 'claims', 'deferred_records') for r in data[key]]
    _index(records, 'current Spatial records')
    counts = {key: len(data[key]) for key in ('spaces', 'elements', 'observations', 'relations', 'claims', 'deferred_records')}
    report = {'adapter_version': 'fpue_spatial_ontology_adapter/1', 'source_role': role, 'frame_id': frame, 'source_binding': copy.deepcopy(binding), 'source_files': metadata['source_artifacts'], 'counts': counts, 'source_schema': bundle['schema_version'], 'source_entities': len(bundle['spaces'])+len(bundle['geometry_candidates'])+len(bundle['observations']), 'source_relations': len(bundle.get('relations', [])), 'geometry': {'native_coordinates_transformed': False, 'original_records_preserved': True, 'null_space_polygons_preserved': True}, 'relation_statuses': dict(Counter(r['status'] for r in data['relations'])), 'warnings': warnings, 'deferred': [{'id': r['id'], 'reason': r['reason']} for r in data['deferred_records']], 'ontology_rule_sha256': bundle.get('ontology', {}).get('rule_sha256'), 'current_exporter_rule_sha256': rule_digest(), 'verified_by_adapter': False, 'GT_metrics': None}
    return data, report
