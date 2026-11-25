"""Validate/export actual Ref graphs using the existing isolated RDF environment."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from rdflib import Graph
from rdflib.compare import isomorphic
from integrations.fpue_ontology.region_graph_v0_4 import validate_graph, to_rdf, invalidation_plan, plan_review_requests


def export(before_path, after_path, output):
    output = Path(output)
    if output.exists():
        raise FileExistsError('Choose a new output directory; earlier RDF and review history are preserved')
    before = json.loads(Path(before_path).read_text())
    after = json.loads(Path(after_path).read_text())
    reports, serialized = [], []
    for name, data in [('before', before), ('after', after)]:
        validate_graph(data)
        graph = to_rdf(data)
        ttl = graph.serialize(format='turtle')
        if not isomorphic(graph, Graph().parse(data=ttl, format='turtle')):
            raise ValueError('RDF roundtrip failed')
        serialized.append((name, ttl))
        reports.append({'revision': name, 'triples': len(graph), 'observations': len(data['observations']),
            'entities': len(data['entities']), 'relations': len(data['relations']),
            'claim_states': {state: sum(c['status'] == state for c in data['claims']) for state in ['PROPOSED', 'STALE', 'VERIFIED']},
            'origin': data['source_binding']['origin'], 'original_frame': data['source_binding']['frame_id'],
            'source_role': data['source_binding']['source_role'], 'native_contract_validated': True,
            'architectural_ground_truth_verified': False})
    changed = after.get('revision_lineage', {}).get('changed_previous_region_ids', [])
    plan = invalidation_plan(before, changed)
    pending = plan_review_requests(after)
    # A prior revision is historical, not a fresh unresolved request for the user.
    current = [request for request in pending if ':r1:' in request['subject_id'] or ':relation:' in request['subject_id']]
    output.mkdir(parents=True, exist_ok=False)
    for name, ttl in serialized:
        (output/(name+'.ttl')).write_text(ttl)
    (output/'validation.json').write_text(json.dumps(reports, ensure_ascii=False, indent=2))
    (output/'invalidation_plan.json').write_text(json.dumps(plan, ensure_ascii=False, indent=2))
    (output/'unresolved_requests.json').write_text(json.dumps(current, ensure_ascii=False, indent=2))
    return reports


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--before', required=True, type=Path)
    parser.add_argument('--after', required=True, type=Path)
    parser.add_argument('--output-dir', required=True, type=Path)
    args = parser.parse_args()
    try:
        print(json.dumps(export(args.before, args.after, args.output_dir), ensure_ascii=False, indent=2))
    except (OSError, ValueError) as error:
        parser.exit(1, str(error)+'\n')


if __name__ == '__main__':
    main()
