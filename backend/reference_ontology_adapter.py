"""Native Reference result -> v0.4 region-stage graphs; no RDF dependency.

Before/after records retain original observations and explicit inferred edges.
The existing isolated ontology environment validates and exports these graphs.
"""
import copy

from integrations.fpue_ontology import understanding_contract_v0_4 as ontology


class RegionGraph:
    def __init__(self, binding, run_id):
        self.binding = {**copy.deepcopy(binding), 'source_role': 'Ref', 'origin': binding.get('origin', 'CURRENT_INPUT')}
        self.graph = {'schema_version': ontology.VERSION, 'run_id': run_id, 'source_binding': self.binding,
            'observations': [], 'entities': [], 'relations': [], 'claims': [], 'derivations': [],
            'alternatives': [], 'external_evidence': [],
            'policy': {'architectural_truth_verified': False, 'original_observations_preserved': True,
                'PDF_and_render_are_dependent_evidence': True, 'new_physical_walls_from_closure': False}}
        self.ids = set()

    def add(self, group, identity, **values):
        if identity in self.ids:
            return identity
        self.ids.add(identity)
        self.graph[group].append({'id': identity, 'source_role': 'Ref', 'frame_id': self.binding['frame_id'], **values})
        return identity

    def geometry(self, kind, points):
        return {'kind': kind, 'frame_id': self.binding['frame_id'], 'coordinate_system': 'image_native_pixels',
            **({'outer_rings': [points], 'holes': []} if kind == 'Polygon' else {'points_native': points})}

    def line(self, line, prefix=''):
        identity = prefix+line['id']
        self.add('observations', identity, observation_type='Line', geometry=self.geometry('Segment', line['points']),
            source_evidence={'layer_hints': line.get('layers', []), 'original_source_ids': line.get('source_ids', [line.get('source_primitive_id')]),
                'request_id': line.get('request_id'), 'high_resolution_visible_fraction': line.get('high_resolution_visible_fraction'),
                'native_unclipped_points': line.get('native_unclipped_points'), 'context_clipped': line.get('context_clipped', False),
                'evidence_dependency_group': self.binding['source_sha256'], 'architectural_type_verified': False})
        return identity

    def entity(self, identity, kind, geometry=None, **fields):
        shape = 'POLYGON_AVAILABLE' if geometry and geometry['kind'] == 'Polygon' else 'PARTIAL_BOUNDARY' if geometry else 'UNRESOLVED'
        return self.add('entities', identity, expected_type=kind, geometry=geometry, grounding_status='PARTIAL', geometry_state=shape, **fields)

    def claim(self, identity, subject, predicate, value, refs=(), status='PROPOSED'):
        return self.add('claims', identity, subject_id=subject, predicate=predicate, value=value, status=status,
            scope='Conditional native geometry/meaning proposal; no independent architectural GT', source_evidence_refs=list(refs))

    def relation(self, identity, kind, source, target, refs=(), **fields):
        entities = {e['id']: e for e in self.graph['entities']}
        ontology.validate_relation_signature(kind, entities[source]['expected_type'], entities[target]['expected_type'])
        return self.add('relations', identity, kind=kind, source_id=source, target_id=target, source_evidence_refs=list(refs), **fields)


def _revision(builder, case, result, revision, lines, prefix, stale=False):
    if not result.get('polygon_native'):
        return None
    base = case['id']+':'+revision
    source_map = {line['id']: line for line in lines}
    region_sources = []
    closure_ids = []
    region_id = base+':region'
    for edge in result.get('edges', []):
        refs = [builder.line(source_map[identity], prefix) for identity in edge['source_line_ids'] if identity in source_map]
        region_sources.extend(refs)
        for index, (a, b) in enumerate(edge['observed_intervals']):
            if b-a < 1e-5:
                continue
            points = [[a, edge['normal']], [b, edge['normal']]] if edge['axis'] == 'H' else [[edge['normal'], a], [edge['normal'], b]]
            identity = base+':'+edge['side']+f':observed:{index}'
            builder.entity(identity, 'BoundaryFragment', builder.geometry('Segment', points), boundary_origin='OBSERVED_FACE',
                source_evidence_refs=refs, physical_wall_type_unverified=True)
        for index, (a, b) in enumerate(edge['inferred_intervals']):
            if b-a < 1e-5:
                continue
            points = [[a, edge['normal']], [b, edge['normal']]] if edge['axis'] == 'H' else [[edge['normal'], a], [edge['normal'], b]]
            identity = base+':'+edge['side']+f':closure:{index}'
            interpretation = 'UNKNOWN'
            for assessment in case.get('gap_assessments', []) if revision == 'r1' else []:
                if assessment['side'] == edge['side'] and abs(assessment['interval'][0]-a) < 1 and abs(assessment['interval'][1]-b) < 1:
                    interpretation = assessment['interpretation']
            builder.entity(identity, 'ClosureBoundary', builder.geometry('Segment', points), boundary_origin='INFERRED_CLOSURE',
                gap_interpretation=interpretation, source_evidence_refs=refs, physical_wall_existence='UNKNOWN')
            closure_ids.append(identity)
            builder.claim(identity+':meaning', identity, 'gap_meaning', interpretation, refs, 'STALE' if stale else 'PROPOSED')
    region_sources = sorted(set(region_sources))
    builder.entity(region_id, 'PartitionRegion', builder.geometry('Polygon', result['polygon_native']), region_kind='PARTITIONED',
        source_evidence_refs=region_sources, revision=revision, resolution_status=result['status'],
        active_revision=True, full_space_extent_verified=False,
        operation_semantics='FRAGMENT_CLOSURE' if closure_ids else 'OBSERVED_BOUNDARY_ASSEMBLY',
        inferred_perimeter_fraction=result.get('inferred_perimeter_fraction'),
        ranking_score=result.get('ranking_score'), ranking_is_probability=False)
    builder.claim(region_id+':geometry', region_id, 'region_geometry', result['polygon_native'], region_sources,
        'STALE' if stale else 'PROPOSED')
    for entity in builder.graph['entities']:
        if entity['id'].startswith(base+':') and entity['id'] != region_id and ontology.is_a(entity['expected_type'], 'Boundary'):
            builder.relation(entity['id']+':bounds', 'RegionBoundary', region_id, entity['id'], entity.get('source_evidence_refs', []))
    if closure_ids and len(region_sources) >= 2:
        builder.add('derivations', base+':closure-derivation', operation='FRAGMENT_CLOSURE', input_ids=region_sources,
            output_ids=[region_id]+closure_ids, parameters={'settings': case.get('algorithm_settings', {}), 'revision': revision},
            assumptions=['Unknown connecting segments are spatial boundary hypotheses, not physical walls.',
                'Candidate region does not establish complete architectural space extent.'])
    if result['status'] == 'REGION_PROPOSED':
        membership = builder.relation(base+':membership', 'RegionMembership', case['id'], region_id, [case['label']['id']])
        builder.claim(membership+':claim', membership, 'region_membership', 'CONDITIONAL', [case['label']['id']],
            'STALE' if stale else 'PROPOSED')
    return region_id


def build_region_graphs(bundle):
    if bundle.get('source_role') != 'Ref' or bundle.get('schema_version') != 'reference_understanding_bundle/1':
        raise ValueError('Own Reference restoration bundle required')
    builder = RegionGraph(bundle['source_binding'], bundle['run_id'])
    for text in bundle['ocr_observations']:
        builder.add('observations', text['id'], observation_type='TextRegion', text=text.get('text'),
            geometry=builder.geometry('Polygon', text['polygon_native']), source_evidence={'kind': 'REUSED_PP_OCRV5',
                'recognition_score': text.get('recognition_score'), 'source_frame_checked': True,
                'evidence_dependency_group': bundle['source_binding']['source_sha256']})
    initial = bundle['initial_lines']
    for case in bundle['cases']:
        label = case['label']
        kind = 'PipeShaft' if label['space_type'] == 'PipeShaft' else 'FunctionalSpace' if label['space_type'] in {'LivingRoom', 'KitchenDining'} else 'Space'
        builder.entity(case['id'], kind, label=label['name'], source_evidence_refs=[label['id']],
            seed_native=label['seed'], space_type_hypothesis=label['space_type'], full_space_extent_verified=False)
        label_id = case['id']+':label'
        builder.entity(label_id, 'TextRegion', builder.geometry('Polygon', label['observation']['polygon_native']), source_evidence_refs=[label['id']])
        relation = builder.relation(case['id']+':label-association', 'LabelAssociation', label_id, case['id'], [label['id']])
        builder.claim(relation+':claim', relation, 'label_association', label['name'], [label['id']])
        _revision(builder, case, case['before'], 'r0', initial, 'initial:')
    before = copy.deepcopy(builder.graph)
    for entity in builder.graph['entities']:
        if entity.get('revision') == 'r0':
            entity['active_revision'] = False
    changed = []
    for case in bundle['cases']:
        changed_geometry = case['before'].get('bbox_native') != case['after'].get('bbox_native') or case['before']['status'] != case['after']['status']
        if changed_geometry:
            region_id = case['id']+':r0:region'
            if region_id in builder.ids:
                changed.append(region_id)
            for record in builder.graph['claims']:
                if record['subject_id'] == region_id or record['subject_id'].startswith(case['id']+':r0:'):
                    record['status'] = 'STALE'
        fresh = case.get('reobservation', {})
        lines = fresh.get('lines', initial)
        for line in lines:
            builder.line(line)
        for gap in case.get('gap_assessments', []):
            for arc in gap['arc_evidence']:
                identity = case['id']+':arc:'+arc['source_primitive_id']
                builder.add('observations', identity, observation_type='Arc', geometry=builder.geometry('Polyline', arc['polyline_native']),
                    source_evidence={'source_primitive_id': arc['source_primitive_id'], 'layer': arc['layer'],
                        'radius_native_px': arc['radius_native_px'], 'evidence_dependency_group': bundle['source_binding']['source_sha256']})
        case_with_settings = {**case, 'algorithm_settings': bundle['algorithm']}
        _revision(builder, case_with_settings, case['after'], 'r1', lines, '')
    after = builder.graph
    for index, attempt in enumerate(bundle.get('relation_attempts', [])):
        identity = bundle['run_id']+f':relation:{index}'
        refs = []
        for case in bundle['cases']:
            if case['id'] in {attempt['source_space_id'], attempt['target_space_id']}:
                refs += [line['id'] for line in case.get('reobservation', {}).get('lines', [])]
        refs = sorted(set(refs))
        fields = {'geometric_attempt': attempt, 'existence': 'UNKNOWN'}
        if attempt['kind'] == 'Connection':
            fields.update(mode='Unknown', via_entity_id=None, existence_claim_id=identity+':existence', mode_claim_id=identity+':mode')
        builder.relation(identity, attempt['kind'], attempt['source_space_id'], attempt['target_space_id'], refs, **fields)
        if attempt['kind'] == 'Connection':
            builder.claim(identity+':existence', identity, 'connection_existence', 'UNKNOWN', refs)
            builder.claim(identity+':mode', identity, 'connection_mode', 'Unknown', refs)
        else:
            builder.claim(identity+':claim', identity, 'adjacency', 'PROPOSED_FROM_ALIGNED_BOUNDARIES', refs)
    after['revision_lineage'] = {'before_run_id': before['run_id'], 'changed_previous_region_ids': changed,
        'raw_observations_overwritten': False, 'stale_claim_count': sum(c['status'] == 'STALE' for c in after['claims']),
        'interpretation': 'r1 is a proposed replacement; architectural correctness not independently verified'}
    return before, after
