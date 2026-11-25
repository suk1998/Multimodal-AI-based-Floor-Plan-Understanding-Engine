"""Native source identity and bundle descriptors shared by staged understanding.

Frame aliases are permitted only as explicit same-image evidence. They never
make different roles, pages or analysis scopes interchangeable.
"""
import hashlib
import json
import math

NATIVE_KEYS = ('source_role', 'source_sha256', 'image_sha256', 'page',
               'extraction_zoom', 'width', 'height')
SCOPE_KEYS = ('core_bbox_native', 'context_bbox_native')
CONTRACTS = {
    'target_understanding_bundle/1': {'groups': {'cases':'id'}, 'parents': []},
    'target_understanding_model/1': {'groups': {'inventory':'id','relations':'id'}, 'parents': []},
    'reference_understanding_model/1': {'groups': {'inventory': 'id', 'relations': 'id'}, 'parents': []},
    'architectural_observation_bundle/1': {'groups': {'observations': 'observation_id'}, 'parents': []},
    'architectural_geometry_bundle/1': {'groups': {'entities': 'entity_id', 'observations': 'observation_id'}, 'parents': ['parent_observation_run_id']},
    'architectural_spatial_bundle/1': {'groups': {'spaces': 'space_id'}, 'parents': ['parent_geometry_run_id', 'parent_observation_run_id']},
    'reference_understanding_bundle/1': {'groups': {'cases': 'id'}, 'parents': []},
    'fpue_region_hypotheses/0.4': {'groups': {'entities': 'id', 'relations': 'id', 'observations': 'id'}, 'parents': []},
}


def canonical_image_id(page_image):
    return f'{page_image.source_sha256}:{page_image.page_number}:{page_image.zoom:g}:{page_image.width}x{page_image.height}'


def request_frame(page_image, role):
    if role not in {'Ref', 'Target'}:
        raise ValueError('Ref/Target role required')
    identity = canonical_image_id(page_image)
    return {'source_sha256': page_image.source_sha256, 'image_id': identity,
        'frame_id': role+':'+identity,
        'pdf_frame_id': f'pdf:{page_image.source_sha256}:page:{page_image.page_number}:pymupdf_unrotated_local',
        'width': page_image.width, 'height': page_image.height, 'extraction_zoom': page_image.zoom}


def validate_binding(binding):
    if not isinstance(binding, dict) or not set(NATIVE_KEYS+('frame_id',)) <= binding.keys():
        raise ValueError('Complete native source binding required')
    if binding['source_role'] not in {'Ref', 'Target'} or not isinstance(binding['frame_id'], str) or not binding['frame_id']:
        raise ValueError('Explicit native role/frame required')
    for key in ['source_sha256', 'image_sha256']:
        value = binding[key]
        if not isinstance(value, str) or len(value) != 64 or any(c not in '0123456789abcdef' for c in value):
            raise ValueError('Source and image SHA256 required')
    if any(type(binding[key]) is not int or binding[key] <= 0 for key in ['page', 'width', 'height']):
        raise ValueError('Integer page/image dimensions required')
    zoom = binding['extraction_zoom']
    if type(zoom) not in (int, float) or not math.isfinite(zoom) or zoom <= 0:
        raise ValueError('Finite positive extraction zoom required')
    for key in SCOPE_KEYS:
        if key not in binding:
            continue
        box = binding[key]
        if not isinstance(box, list) or len(box) != 4 or any(type(v) not in (int, float) or not math.isfinite(v) for v in box):
            raise ValueError('Finite native scope required')
        if not (0 <= box[0] < box[2] <= binding['width'] and 0 <= box[1] < box[3] <= binding['height']):
            raise ValueError('Scope outside native frame')
    if all(key in binding for key in SCOPE_KEYS):
        a, b = binding[SCOPE_KEYS[0]], binding[SCOPE_KEYS[1]]
        if not (b[0] <= a[0] < a[2] <= b[2] and b[1] <= a[1] < a[3] <= b[3]):
            raise ValueError('Core must be contained by context')
    return binding


def source_fingerprint(binding, *, include_scope=True):
    validate_binding(binding)
    keys = NATIVE_KEYS + ('frame_id',) + (SCOPE_KEYS if include_scope else ())
    values = {key: binding.get(key) for key in keys}
    values['extraction_zoom'] = float(values['extraction_zoom'])
    if include_scope:
        for key in SCOPE_KEYS:
            if values[key] is not None:
                values[key] = list(map(float, values[key]))
    return hashlib.sha256(json.dumps(values, sort_keys=True, allow_nan=False).encode()).hexdigest()


def assert_same_source(expected, actual, *, allow_verified_frame_alias=False, include_scope=True):
    validate_binding(expected)
    validate_binding(actual)
    if any(expected[key] != actual[key] for key in NATIVE_KEYS):
        raise ValueError('Different PDF/page/role/scale/native image')
    alias = expected['frame_id'] != actual['frame_id']
    if alias and not allow_verified_frame_alias:
        raise ValueError('Native frame IDs differ; explicit same-image alias evidence required')
    if include_scope and any(expected.get(key) != actual.get(key) for key in SCOPE_KEYS):
        raise ValueError('Analysis scope differs; native alias is not scope equivalence')
    return {'same_native_source': True, 'frame_alias': alias,
        'alias_from': actual['frame_id'] if alias else None,
        'alias_to': expected['frame_id'] if alias else None,
        'validated_fields': list(NATIVE_KEYS), 'analysis_scope_checked': include_scope,
        'architectural_identity_or_truth_verified': False}


def describe_bundle(bundle):
    version = bundle.get('schema_version')
    contract = CONTRACTS.get(version)
    if not contract:
        raise ValueError('Unsupported understanding bundle contract')
    binding = validate_binding(bundle.get('source_binding'))
    if bundle.get('source_role', binding['source_role']) != binding['source_role'] or bundle.get('frame_id', binding['frame_id']) != binding['frame_id']:
        raise ValueError('Bundle root role/frame differs from source binding')
    groups = {}
    for name, identity_key in contract['groups'].items():
        records = bundle.get(name, [])
        identities = [record.get(identity_key) for record in records]
        if any(not isinstance(identity, str) or not identity for identity in identities) or len(set(identities)) != len(identities):
            raise ValueError('Missing/duplicate IDs in '+name)
        for record in records:
            if record.get('source_role', binding['source_role']) != binding['source_role'] or record.get('frame_id', binding['frame_id']) != binding['frame_id']:
                raise ValueError('Mixed role/frame in '+name)
        groups[name] = {'count': len(records), 'identity_key': identity_key}
    return {'schema_version': version, 'source_role': binding['source_role'],
        'frame_id': binding['frame_id'], 'native_fingerprint': source_fingerprint(binding, include_scope=False),
        'scope_fingerprint': source_fingerprint(binding), 'groups': groups,
        'parent_revisions': {key: bundle.get(key) for key in contract['parents']},
        'execution_status': bundle.get('status', 'NOT_DECLARED_BY_CONTRACT'),
        'architectural_truth_verified_by_this_check': False}
