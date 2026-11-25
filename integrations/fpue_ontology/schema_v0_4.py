"""Versioned ontology extension for fragment closure and region semantics.

Reuse v0.3 conceptual declarations in a new namespace, preserving all old files.
No imports into the running FPUE backend, no equivalentClass migration, no CV.
"""
import argparse
import json
from pathlib import Path
from rdflib import Graph, Literal, Namespace, URIRef
from rdflib.namespace import OWL, RDF, RDFS, XSD

from .schema_v0_3 import U as OLD, build_understanding_schema as build_previous
from . import understanding_contract_v0_4 as contract

U = Namespace(contract.ONTOLOGY_IRI)


def build_understanding_schema():
    previous = build_previous()
    graph = Graph()
    for prefix, namespace in [('u', U), ('owl', OWL), ('rdf', RDF), ('rdfs', RDFS), ('xsd', XSD)]:
        graph.bind(prefix, namespace)

    def rebase(term):
        if isinstance(term, URIRef) and str(term).startswith(str(OLD)):
            return U[str(term)[len(str(OLD)):]]
        return term

    for triple in previous:
        graph.add(tuple(rebase(term) for term in triple))
    graph.set((U[''], OWL.versionInfo, Literal(contract.SCHEMA_VERSION)))
    graph.set((U[''], RDFS.label, Literal('FPUE fragment and region understanding v0.4', lang='en')))
    graph.set((U[''], U.applicationContractJSON, Literal(json.dumps(contract.contract_spec(), sort_keys=True))))
    graph.set((U[''], U.applicationContractSHA256, Literal(contract.contract_digest())))
    classes = {**contract.ENTITY_PARENTS, 'RegionHypothesis': 'EntityHypothesis',
        'Derivation': 'Record', 'ClosureDerivation': 'Derivation',
        'FreeDomainDerivation': 'Derivation', 'ResidualDifferenceDerivation': 'Derivation',
        'RegionInterpretationDerivation': 'Derivation',
        'ObservationRecord': 'Record', 'ExternalEvidenceRecord': 'Record',
        'VerificationRecord': 'Record', 'ExistenceClaim': 'Claim', 'ConnectionModeClaim': 'Claim',
        'RegionKind': None, 'GeometryState': None, 'BoundaryOrigin': None,
        'GapInterpretation': None, 'ExistenceValue': None, 'DerivationOperation': None}
    classes.update({kind + 'Hypothesis': 'RelationHypothesis' for kind in contract.RELATION_CONTRACTS})
    for name, parent in classes.items():
        graph.add((U[name], RDF.type, OWL.Class))
        if parent:
            graph.add((U[name], RDFS.subClassOf, U[parent]))
    for left, right in [('SpatialRegion', 'Space'), ('InferredBoundary', 'BuildingElement')]:
        graph.add((U[left], OWL.disjointWith, U[right]))
    objects = [
        ('regionKind', 'RegionHypothesis', 'RegionKind'),
        ('geometryState', 'EntityHypothesis', 'GeometryState'),
        ('boundaryOrigin', 'BoundaryHypothesis', 'BoundaryOrigin'),
        ('gapInterpretation', 'BoundaryHypothesis', 'GapInterpretation'),
        ('existenceValue', 'ExistenceClaim', 'ExistenceValue'),
        ('existenceClaim', 'ConnectionHypothesis', 'ExistenceClaim'),
        ('modeClaim', 'ConnectionHypothesis', 'ConnectionModeClaim'),
        ('derivationOperation', 'Derivation', 'DerivationOperation'),
        ('derivationInput', 'Derivation', 'Record'),
        ('derivationOutput', 'Derivation', 'EntityHypothesis'),
        ('dependsOn', 'Record', 'Record'), ('invalidatedBy', 'Record', 'Record'),
        ('hasVerification', 'Claim', 'VerificationRecord'),
        ('independentEvidence', 'VerificationRecord', 'ExternalEvidenceRecord'),
    ]
    for name, domain, range_ in objects:
        graph.add((U[name], RDF.type, OWL.ObjectProperty))
        graph.add((U[name], RDFS.domain, U[domain]))
        graph.add((U[name], RDFS.range, U[range_]))
    for name, domain, range_ in [
        ('observationType', 'ObservationRecord', XSD.string),
        ('sourceEvidenceJSON', 'Record', XSD.string),
        ('operationParametersJSON', 'Derivation', XSD.string),
        ('namedOperandsJSON', 'Derivation', XSD.string),
        ('assumptionsJSON', 'Derivation', XSD.string),
        ('inventoryComplete', 'ResidualDifferenceDerivation', XSD.boolean),
        ('unknownCoverageJSON', 'ResidualDifferenceDerivation', XSD.string),
        ('criteriaVersion', 'VerificationRecord', XSD.string),
        ('verifiedScope', 'VerificationRecord', XSD.string),
    ]:
        graph.add((U[name], RDF.type, OWL.DatatypeProperty))
        graph.add((U[name], RDFS.domain, U[domain]))
        graph.add((U[name], RDFS.range, range_))
    for kind, values in {'RegionKind': contract.REGION_KINDS,
                         'GeometryState': contract.GEOMETRY_STATES,
                         'BoundaryOrigin': contract.BOUNDARY_ORIGINS,
                         'GapInterpretation': contract.GAP_INTERPRETATIONS,
                         'ExistenceValue': contract.EXISTENCE_VALUES,
                         'DerivationOperation': contract.OPERATIONS}.items():
        for value in values:
            graph.add((U[kind + '_' + value], RDF.type, U[kind]))
    for name in contract.ENTITY_PARENTS:
        node = U['EntityType_' + name]
        graph.add((node, RDF.type, U.EntityType))
        graph.add((node, U.denotesClass, U[name]))
    for engine, profile in contract.ENGINE_PROFILES.items():
        graph.set((U['Engine_' + engine], U.profileJSON, Literal(json.dumps(profile, sort_keys=True))))
    comments = {
        'ClosureBoundary': 'Inferred closing edge, possibly a door/passage/missed wall. Its physical meaning may remain unknown.',
        'ClosureDerivation': 'May propose a closed region from fragments before door recognition or four complete observed walls. Never outputs a Wall.',
        'PhysicalFreeRegion': 'Physical free domain excluding occupied walls/cores. May connect rooms through doorways; not a functional room.',
        'PartitionRegion': 'Region formed with observed boundaries and explicitly inferred logical separators.',
        'UnassignedRegion': 'Residual difference; can contain missed rooms/corridors/unknown areas. Not automatically a living room.',
        'FunctionalSpace': 'Semantic space associated with a region. An individual polygon may be unknown while a shared region is referenced.',
        'RegionMembershipHypothesis': 'Association with a region, not an inferred boundary, containment fact, or all-pairs connection.',
        'ConnectionHypothesis': 'Existence and mode are separate claims. OpenPlan need not have a single physical Opening.',
        'dependsOn': 'Dependency for explicit invalidation, not independent supporting evidence or a truth implication.',
    }
    for name, comment in comments.items():
        graph.set((U[name], RDFS.comment, Literal(comment)))
    return graph


def main():
    parser = argparse.ArgumentParser(description='Generate v0.4 ontology; old runtime stays unchanged')
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        parser.error('Output exists; choose a new path')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x', encoding='utf-8') as stream:
        stream.write(build_understanding_schema().serialize(format='turtle'))
    print(f'Created {args.output}')


if __name__ == '__main__':
    main()
