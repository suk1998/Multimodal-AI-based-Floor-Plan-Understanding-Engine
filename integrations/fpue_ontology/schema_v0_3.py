"""Versioned v0.3 ontology for the user's apartment understanding graphs.

Physical concepts, hypotheses and evidence are separate. This module is not
imported by the running FPUE engines and does not change v0.2.2 behaviour.
"""
import argparse
import json
from pathlib import Path
from rdflib import BNode, Graph, Literal, Namespace
from rdflib.namespace import OWL, RDF, RDFS, XSD

from .understanding_contract import (ONTOLOGY_IRI, ENTITY_PARENTS, RELATION_CONTRACTS,
                                     ENGINE_PROFILES, CLAIM_STATES, GROUNDING_STATES,
                                     contract_spec, contract_digest)
from .vocabulary import BASE_SPACE_TERMS

U = Namespace(ONTOLOGY_IRI)


def build_understanding_schema():
    g = Graph()
    for prefix, value in [('u',U),('owl',OWL),('rdf',RDF),('rdfs',RDFS),('xsd',XSD)]: g.bind(prefix,value)
    g.add((U[''],RDF.type,OWL.Ontology))
    g.add((U[''],OWL.versionInfo,Literal('0.3.0-apartment-understanding')))
    g.add((U[''],RDFS.label,Literal('FPUE ontology-guided understanding v0.3',lang='en')))
    for key,value in [('applicationContractJSON',json.dumps(contract_spec(),sort_keys=True)),('applicationContractSHA256',contract_digest()),('denotesClass',None)]:
        g.add((U[key],RDF.type,OWL.AnnotationProperty))
        if value is not None:g.add((U[''],U[key],Literal(value)))
    classes = {'ArchitecturalEntity':None, **ENTITY_PARENTS,
        'Record':None,'Hypothesis':'Record','EntityHypothesis':'Hypothesis',
        'SpaceHypothesis':'EntityHypothesis','ElementHypothesis':'EntityHypothesis',
        'OpeningHypothesis':'EntityHypothesis','BoundaryHypothesis':'EntityHypothesis',
        'ObservationHypothesis':'EntityHypothesis','RelationHypothesis':'Hypothesis',
        'Claim':'Record','StabilityClaim':'Claim','PropertyChangeHypothesis':'Hypothesis',
        'EntityCorrespondenceHypothesis':'Hypothesis','AccessLocationHypothesis':'Hypothesis',
        'AlternativeHypothesisSet':'Record','ObservationGroup':'Record',
        'SourceDocument':None,'SourceStatement':'Record','GraphSnapshot':None,
        'Catalog':None,'ElementType':None,'CatalogEntry':'ElementType','MaterialConcept':None,
        'EntityType':None,'SpaceType':None,'SpaceTerm':None,'ClaimStatus':None,
        'GroundingStatus':None,'ConnectionMode':None,'PropertyKind':None,
        'DirectionFrameKind':None,'GeometryEvidence':'Record','CoordinateFrame':None,
        'EngineProfile':None,'EvidenceRequest':'Record','ComparisonContext':None,
        'Connection':None,'Adjacency':None,'Containment':None,'ReachabilityHypothesis':'Hypothesis',
    }
    for kind in RELATION_CONTRACTS:classes[kind+'Hypothesis']='RelationHypothesis'
    for name,parent in classes.items():
        g.add((U[name],RDF.type,OWL.Class))
        if parent:g.add((U[name],RDFS.subClassOf,U[parent]))
    g.add((U.SpatialEntity,RDFS.subClassOf,U.ArchitecturalEntity))
    for left,right in [('ArchitecturalEntity','Hypothesis'),('Space','BuildingElement'),('Opening','BuildingElement'),('Observation','SpatialEntity')]:
        g.add((U[left],OWL.disjointWith,U[right]))
    # Physical concepts are not asserted on ungrounded hypothesis records.
    obj = [
        ('inSnapshot','Record','GraphSnapshot'),('hasSourceDocument','GraphSnapshot','SourceDocument'),
        ('proposedEntityType','EntityHypothesis','EntityType'),('proposedSpaceType','SpaceHypothesis','SpaceType'),
        ('proposedCatalogEntry','EntityHypothesis','CatalogEntry'),('inCatalog','CatalogEntry','Catalog'),
        ('definedInDocument','Catalog','SourceDocument'),('declaredEntityType','CatalogEntry','EntityType'),
        ('declaredMaterial','CatalogEntry','MaterialConcept'),('hasSpaceType','Space','SpaceType'),
        ('relationSource','RelationHypothesis','EntityHypothesis'),('relationTarget','RelationHypothesis','EntityHypothesis'),
        ('viaEntityHypothesis','ConnectionHypothesis','EntityHypothesis'),('viaOpeningHypothesis','ConnectionHypothesis','OpeningHypothesis'),
        ('connectionMode','ConnectionHypothesis','ConnectionMode'),
        ('connectionSpace','Connection','Space'),('physicalOpening','Connection','Opening'),
        ('claimSubject','Claim','Record'),('hasClaim','Record','Claim'),
        ('claimStatus','Record','ClaimStatus'),('groundingStatus','Record','GroundingStatus'),
        ('supportedByStatement','Record','SourceStatement'),('supportedByGeometry','Record','GeometryEvidence'),
        ('refutedBy','Hypothesis','Record'),('derivedFrom','Record','Record'),
        ('memberHypothesis','AlternativeHypothesisSet','Hypothesis'),
        ('memberObservation','ObservationGroup','Observation'),
        ('hypothesisGeometry','EntityHypothesis','GeometryEvidence'),('inFrame','GeometryEvidence','CoordinateFrame'),
        ('boundaryOf','PhysicalBoundary','BuildingElement'),('separatesSpace','Boundary','Space'),
        ('accessConnection','AccessLocationHypothesis','ConnectionHypothesis'),
        ('accessSpace','AccessLocationHypothesis','SpaceHypothesis'),('accessBoundary','AccessLocationHypothesis','BoundaryHypothesis'),
        ('directionFrameKind','AccessLocationHypothesis','DirectionFrameKind'),
        ('refHypothesis','EntityCorrespondenceHypothesis','EntityHypothesis'),
        ('targetHypothesis','EntityCorrespondenceHypothesis','EntityHypothesis'),
        ('aboutProperty','Claim','PropertyKind'),('requestSubject','EvidenceRequest','Record'),
        ('requestedEngine','EvidenceRequest','EngineProfile'),
    ]
    for name,domain,range_ in obj:
        g.add((U[name],RDF.type,OWL.ObjectProperty));g.add((U[name],RDFS.domain,U[domain]));g.add((U[name],RDFS.range,U[range_]))
    for name in ['connectedTo','adjacentTo']:
        g.add((U[name],RDF.type,OWL.ObjectProperty));g.add((U[name],RDF.type,OWL.SymmetricProperty))
        g.add((U[name],RDFS.domain,U.Space if name=='connectedTo' else U.SpatialEntity))
        g.add((U[name],RDFS.range,U.Space if name=='connectedTo' else U.SpatialEntity))
        g.add((U[name],RDFS.comment,Literal('Physical fact vocabulary only. Never materialized from a candidate or made transitive.')))
    cardinality=BNode()
    g.add((U.Connection,RDFS.subClassOf,cardinality));g.add((cardinality,RDF.type,OWL.Restriction))
    g.add((cardinality,OWL.onProperty,U.connectionSpace));g.add((cardinality,OWL.onClass,U.Space))
    g.add((cardinality,OWL.qualifiedCardinality,Literal(2,datatype=XSD.nonNegativeInteger)))
    data = [
        ('recordId',None,XSD.string),('sourceRole',None,XSD.string),('variant','GraphSnapshot',XSD.string),
        ('sourceSHA256','SourceDocument',XSD.string),('sourcePath','SourceDocument',XSD.string),
        ('sourceLine','SourceStatement',XSD.integer),('sourceText','SourceStatement',XSD.string),
        ('sourceKind','SourceDocument',XSD.string),('frameId',None,XSD.string),
        ('labelHypothesis','EntityHypothesis',RDFS.Literal),('spaceOrdinalHypothesis','SpaceHypothesis',XSD.integer),
        ('spaceRoleHypothesis','SpaceHypothesis',XSD.string),('canonicalLabel','SpaceTerm',RDFS.Literal),
        ('typeCode','CatalogEntry',XSD.string),('rawTypeCode','EntityHypothesis',XSD.string),
        ('catalogScope','Catalog',XSD.string),('definitionStatus','CatalogEntry',XSD.string),
        ('catalogTypeName','CatalogEntry',XSD.string),('declaredFunction','CatalogEntry',XSD.string),('catalogDefinitionJSON','CatalogEntry',XSD.string),
        ('claimPredicate','Claim',XSD.string),('claimValueJSON','Claim',XSD.string),('claimScope','Claim',XSD.string),
        ('recordJSON','Record',XSD.string),('snapshotContextJSON','GraphSnapshot',XSD.string),
        ('predicateJSON','RelationHypothesis',XSD.string),('origin',None,XSD.string),
        ('geometryJSON','GeometryEvidence',XSD.string),('coordinateUnit','CoordinateFrame',XSD.string),
        ('engineName','EngineProfile',XSD.string),('profileJSON','EngineProfile',XSD.string),
        ('question','EvidenceRequest',XSD.string),('requestStatus','EvidenceRequest',XSD.string),
        ('requestExecutable','EvidenceRequest',XSD.boolean),('requestReason','EvidenceRequest',XSD.string),
    ]
    for name,domain,range_ in data:
        g.add((U[name],RDF.type,OWL.DatatypeProperty))
        if domain:g.add((U[name],RDFS.domain,U[domain]))
        g.add((U[name],RDFS.range,range_))
    enums={'ClaimStatus':CLAIM_STATES,'GroundingStatus':GROUNDING_STATES,
           'ConnectionMode':['Door','OpenPassage','OpenPlan','Unknown'],
           'DirectionFrameKind':['DRAWING_LOCAL','GEOGRAPHIC','UNKNOWN'],
           'PropertyKind':['Existence','Label','CatalogTypeCode','Geometry','Connectivity','AccessLocation','PhysicalIdentity','GeometricStability']}
    for kind,names in enums.items():
        for name in names:g.add((U[kind+'_'+name],RDF.type,U[kind]))
    for name in ENTITY_PARENTS:
        node=U['EntityType_'+name];g.add((node,RDF.type,U.EntityType));g.add((node,U.denotesClass,U[name]))
    for canonical,kind,_,_ in BASE_SPACE_TERMS:
        from urllib.parse import quote
        term=U['SpaceTerm_'+quote(canonical,safe='')];typ=U['SpaceType_'+kind]
        g.add((term,RDF.type,U.SpaceTerm));g.add((term,U.canonicalLabel,Literal(canonical,lang='ko')))
        g.add((typ,RDF.type,U.SpaceType))
    for engine,profile in ENGINE_PROFILES.items():
        node=U['Engine_'+engine];g.add((node,RDF.type,U.EngineProfile));g.add((node,U.engineName,Literal(engine)));g.add((node,U.profileJSON,Literal(json.dumps(profile,ensure_ascii=False,sort_keys=True))))
    comments={
        'PipeShaft':'Service-space/shaft volume; its enclosure walls are separate BuildingElements. PS mentions do not establish shared identity.',
        'Opening':'Architectural void or access feature; not the Door/Window/Grille that may fill it.',
        'ConnectionHypothesis':'Can be semantically proposed before native opening geometry is grounded. Its mode remains Unknown unless supported.',
        'ElementAssociationHypothesis':'General has association; does not entail physical containment.',
        'AccessLocationHypothesis':'Access location and boundary side are separate from connection endpoints and space existence. Direction frame must be explicit.',
        'EntityCorrespondenceHypothesis':'Same physical referent is independent of geometric stability. No label/code-based sameAs.',
        'GraphSnapshot':'Partial positive assertions, not a complete inventory. Missing relations cannot entail removals.',
        'EvidenceRequest':'A question for a future engine call, not evidence or an automatically completed graph edge.',
    }
    for name,comment in comments.items():g.add((U[name],RDFS.comment,Literal(comment)))
    return g


def main():
    parser=argparse.ArgumentParser(description='Generate v0.3 understanding ontology without changing live v0.2.2')
    parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    if args.output.exists():parser.error('Output exists; choose a new path.')
    graph=build_understanding_schema();args.output.parent.mkdir(parents=True,exist_ok=True)
    with args.output.open('x',encoding='utf-8') as stream:stream.write(graph.serialize(format='turtle'))
    print(f'Created {args.output}: {len(graph)} triples')


if __name__=='__main__':main()
