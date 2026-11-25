"""Explicitly synthetic schema examples, never Page 4 predictions or GT.

Coordinates and operation outputs below are hand-authored contract fixtures.
There is no PDF, OCR, CV or VLM execution in this module.
"""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path

from rdflib import Graph
from rdflib.compare import isomorphic

from . import understanding_contract_v0_4 as contract
from .region_graph_v0_4 import validate_graph, to_rdf, plan_review_requests, invalidation_plan
from .schema_v0_4 import build_understanding_schema


class Fixture:
    def __init__(self, name, role):
        marker = 'SYNTHETIC_CONTRACT_TEST:'+name
        self.binding = {'source_role': role, 'frame_id': marker, 'source_sha256': hashlib.sha256((marker+':source-metadata').encode()).hexdigest(),
            'image_sha256': hashlib.sha256((marker+':unrendered-frame-metadata').encode()).hexdigest(),
            'page': 1, 'extraction_zoom': 1, 'width': 100, 'height': 100, 'origin': 'SYNTHETIC_CONTRACT_TEST'}
        self.graph = {'schema_version': contract.VERSION, 'example_id': name, 'source_binding': self.binding,
            'notice': 'Hand-authored synthetic schema fixture. No real PDF/image files, detections, GT or independent verification.',
            **{key: [] for key in ['observations', 'entities', 'relations', 'claims', 'derivations', 'alternatives', 'external_evidence']}}

    def record(self, group, identity, **fields):
        value = {'id': identity, 'source_role': self.binding['source_role'], 'frame_id': self.binding['frame_id'], **fields}
        self.graph[group].append(value)
        return value

    def geometry(self, kind, points):
        value = {'kind': kind, 'frame_id': self.binding['frame_id'], 'coordinate_system': 'image_native_pixels'}
        if kind == 'Polygon':
            value.update(outer_rings=[points], holes=[])
        else:
            value['points_native'] = points
        return value

    def entity(self, identity, kind, points=None, **extra):
        shape = 'Polygon' if points and len(points) > 2 else 'Segment'
        return self.record('entities', identity, expected_type=kind,
            geometry=self.geometry(shape, points) if points else None,
            geometry_state='POLYGON_AVAILABLE' if points and len(points) > 2 else 'PARTIAL_BOUNDARY' if points else 'UNRESOLVED',
            grounding_status='PARTIAL', **extra)

    def claim(self, identity, subject, predicate, value, refs=None):
        return self.record('claims', identity, subject_id=subject, predicate=predicate, value=value,
            status='PROPOSED', scope='Synthetic contract fixture only; architectural truth not verified', source_evidence_refs=refs or [])


def closure_fixture():
    example = Fixture('three_fragment_closure', 'Ref')
    for name, points in [('top', [[20, 20], [80, 20]]), ('left', [[20, 20], [20, 80]]), ('bottom', [[20, 80], [80, 80]])]:
        example.record('observations', name, observation_type='Line', geometry=example.geometry('Segment', points))
    example.entity('closing-edge', 'ClosureBoundary', [[80, 20], [80, 80]], boundary_origin='INFERRED_CLOSURE', gap_interpretation='UNKNOWN')
    example.entity('room-region', 'PartitionRegion', [[20, 20], [80, 20], [80, 80], [20, 80]], region_kind='PARTITIONED')
    example.record('relations', 'region-edge', kind='RegionBoundary', source_id='room-region', target_id='closing-edge')
    example.claim('region-shape-claim', 'room-region', 'region_geometry', 'PROPOSED_RECTANGLE', ['top', 'left', 'bottom'])
    example.claim('gap-claim', 'closing-edge', 'gap_meaning', 'UNKNOWN', ['top', 'left', 'bottom'])
    example.record('derivations', 'close-three-fragments', operation='FRAGMENT_CLOSURE', input_ids=['top', 'left', 'bottom'],
        output_ids=['closing-edge', 'room-region'], parameters={'coordinate_frame': 'native', 'image_processing_executed': False},
        assumptions=['Right-hand joining edge is an inferred boundary, not an observed wall or door.'])
    return validate_graph(example.graph)


def alternatives_fixture():
    graph = deepcopy(closure_fixture())
    entity = deepcopy(next(e for e in graph['entities'] if e['id'] == 'room-region'))
    entity['id'] = 'extended-region-alternative'
    entity['geometry']['outer_rings'][0] = [[20, 20], [90, 20], [90, 80], [20, 80]]
    edge = deepcopy(next(e for e in graph['entities'] if e['id'] == 'closing-edge'))
    edge['id'] = 'extended-closing-edge'
    edge['geometry']['points_native'] = [[90, 20], [90, 80]]
    graph['entities'].extend([entity, edge])
    binding = graph['source_binding']
    graph['derivations'].append({'id': 'alternative-close', 'source_role': binding['source_role'], 'frame_id': binding['frame_id'],
        'operation': 'FRAGMENT_CLOSURE', 'input_ids': ['top', 'left', 'bottom'], 'output_ids': [edge['id'], entity['id']],
        'assumptions': ['Additional ten-pixel extension is a competing synthetic assumption, not an observation.']})
    graph['alternatives'].append({'id': 'competing-regions', 'source_role': binding['source_role'], 'frame_id': binding['frame_id'],
        'member_ids': ['room-region', entity['id']], 'selection': 'UNRESOLVED'})
    graph['example_id'] = 'competing_closures'
    return validate_graph(graph)


def shared_fixture():
    example = Fixture('shared_region_unknown_individual_polygons', 'Target')
    example.entity('shared-region', 'PartitionRegion', [[10, 10], [90, 10], [90, 90], [10, 90]], region_kind='SHARED')
    for identity, label in [('living', '거실'), ('kitchen', '주방/식당'), ('hall', '미확정 통행 영역')]:
        example.entity(identity, 'FunctionalSpace', label=label)
        example.record('relations', identity+'-membership', kind='RegionMembership', source_id=identity, target_id='shared-region')
    example.record('relations', 'living-kitchen', kind='Connection', source_id='living', target_id='kitchen', mode='OpenPlan',
        via_entity_id=None, existence_claim_id='connection-exists', mode_claim_id='connection-mode')
    example.claim('connection-exists', 'living-kitchen', 'connection_existence', 'UNKNOWN')
    example.claim('connection-mode', 'living-kitchen', 'connection_mode', 'OpenPlan')
    example.claim('shared-shape', 'shared-region', 'region_geometry', 'SYNTHETIC_PROPOSAL')
    return validate_graph(example.graph)


def residual_fixture():
    example = Fixture('residual_with_unresolved_room', 'Ref')
    example.record('observations', 'source-boundary', observation_type='Line', geometry=example.geometry('Segment', [[0, 0], [100, 0]]))
    example.record('observations', 'unresolved-label', observation_type='TextRegion', text='팬트리', geometry=example.geometry('Point', [[75, 50]]))
    for identity, kind, ring, region_kind in [
        ('domain', 'DrawingRegion', [[0, 0], [100, 0], [100, 100], [0, 100]], 'UNKNOWN'),
        ('occupied', 'OccupiedRegion', [[0, 0], [10, 0], [10, 100], [0, 100]], 'UNKNOWN'),
        ('free', 'PhysicalFreeRegion', [[10, 0], [100, 0], [100, 100], [10, 100]], 'UNKNOWN'),
        ('known-room', 'PartitionRegion', [[10, 0], [50, 0], [50, 100], [10, 100]], 'PARTITIONED'),
        ('residual', 'UnassignedRegion', [[50, 0], [100, 0], [100, 100], [50, 100]], 'UNASSIGNED'),
    ]:
        example.entity(identity, kind, ring, region_kind=region_kind, source_evidence_refs=['source-boundary'])
        example.claim(identity+'-geometry', identity, 'region_geometry', 'SYNTHETIC_PROPOSAL', ['source-boundary'])
    example.record('derivations', 'free-domain', operation='FREE_DOMAIN', input_ids=['domain', 'occupied'], output_ids=['free'],
        operands={'domain': 'domain', 'occupied': ['occupied'], 'cores': []}, assumptions=['Input occupancy is a synthetic hypothesis.'])
    example.record('derivations', 'difference', operation='RESIDUAL_DIFFERENCE', input_ids=['free', 'known-room'], output_ids=['residual'],
        operands={'free_region': 'free', 'partitions': ['known-room']}, inventory_complete=False,
        unknown_coverage=[{'reason': 'Label is unresolved; its room was not subtracted.', 'observation_ids': ['unresolved-label']}])
    return validate_graph(example.graph)


def generate_examples(output):
    output = Path(output)
    if output.exists():
        raise FileExistsError('Choose a new output directory; old examples are preserved')
    examples = [closure_fixture(), alternatives_fixture(), shared_fixture(), residual_fixture()]
    prepared = []
    for example in examples:
        rdf = to_rdf(example)
        ttl = rdf.serialize(format='turtle')
        if not isomorphic(rdf, Graph().parse(data=ttl, format='turtle')):
            raise ValueError('RDF roundtrip mismatch')
        prepared.append((example, ttl))
    output.mkdir(parents=True, exist_ok=False)
    (output/'fpue_understanding_v0_4.ttl').write_text(build_understanding_schema().serialize(format='turtle'))
    (output/'contract.json').write_text(json.dumps(contract.contract_spec(), ensure_ascii=False, indent=2))
    audits = []
    for example, ttl in prepared:
        name = example['example_id']
        (output/(name+'.json')).write_text(json.dumps(example, ensure_ascii=False, indent=2))
        (output/(name+'.ttl')).write_text(ttl)
        (output/(name+'_requests.json')).write_text(json.dumps(plan_review_requests(example), ensure_ascii=False, indent=2))
        audits.append({'example': name, 'origin': 'SYNTHETIC_CONTRACT_TEST', 'entities': len(example['entities']),
            'relations': len(example['relations']), 'claim_states': sorted({c['status'] for c in example['claims']}),
            'actual_image_analysis_performed': False, 'actual_GT_created': False})
    (output/'invalidation_plan.json').write_text(json.dumps(invalidation_plan(examples[-1], ['occupied']), ensure_ascii=False, indent=2))
    (output/'example_audit.json').write_text(json.dumps(audits, ensure_ascii=False, indent=2))
    return audits


def main():
    parser = argparse.ArgumentParser(description='Generate explicitly synthetic v0.4 contract examples')
    parser.add_argument('--output-dir', required=True, type=Path)
    args = parser.parse_args()
    try:
        print(json.dumps(generate_examples(args.output_dir), ensure_ascii=False, indent=2))
    except (OSError, ValueError) as error:
        parser.exit(1, str(error)+'\n')


if __name__ == '__main__':
    main()
