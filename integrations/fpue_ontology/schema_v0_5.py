"""OWL extension for printed designations and catalog-to-instance hypotheses."""
import json
from rdflib import Graph,Literal,Namespace,URIRef
from rdflib.namespace import OWL,RDF,RDFS,XSD
from .schema_v0_4 import U as OLD,build_understanding_schema as previous_schema
from .schema import BASE_ELEMENT_CODES
from . import understanding_contract_v0_5 as contract

U=Namespace(contract.ONTOLOGY_IRI)


def build_understanding_schema():
    graph=Graph();graph.bind('u',U)
    for p,n in [('owl',OWL),('rdf',RDF),('rdfs',RDFS),('xsd',XSD)]:graph.bind(p,n)
    def rebase(term):
        return U[str(term)[len(str(OLD)):]] if isinstance(term,URIRef) and str(term).startswith(str(OLD)) else term
    for triple in previous_schema():graph.add(tuple(rebase(t) for t in triple))
    graph.set((U[''],OWL.versionInfo,Literal(contract.SCHEMA_VERSION)))
    graph.set((U[''],RDFS.label,Literal('FPUE catalog-scoped door/window/grille understanding',lang='en')))
    graph.set((U[''],U.applicationContractJSON,Literal(json.dumps(contract.contract_spec(),sort_keys=True))))
    graph.set((U[''],U.applicationContractSHA256,Literal(contract.contract_digest())))
    for name,parent in [('CodeFamilyConcept',None),('TypeDesignationHypothesis','Hypothesis'),('TypeAssignmentHypothesis','Hypothesis')]:
        graph.add((U[name],RDF.type,OWL.Class))
        if parent:graph.add((U[name],RDFS.subClassOf,U[parent]))
    for a,b in [('CodeFamilyConcept','ArchitecturalEntity'),('ElementType','ArchitecturalEntity')]:
        graph.add((U[a],OWL.disjointWith,U[b]))
    objects=[('entryFamily','CatalogEntry','CodeFamilyConcept'),('familyEntityType','CodeFamilyConcept','EntityType'),
        ('familyMaterial','CodeFamilyConcept','MaterialConcept'),('catalogEvidence','Catalog','ExternalEvidenceRecord'),
        ('designationSymbol','TypeDesignationHypothesis','ObservationHypothesis'),
        ('designationEntry','TypeDesignationHypothesis','CatalogEntry'),
        ('readingObservation','TypeDesignationHypothesis','ObservationRecord'),
        ('assignmentElement','TypeAssignmentHypothesis','ElementHypothesis'),
        ('assignmentEntry','TypeAssignmentHypothesis','CatalogEntry'),
        ('assignmentDesignation','TypeAssignmentHypothesis','TypeDesignationHypothesis'),
        ('assignmentReference','TypeAssignmentHypothesis','ReferenceAssociationHypothesis')]
    for name,domain,range_ in objects:
        graph.add((U[name],RDF.type,OWL.ObjectProperty));graph.add((U[name],RDFS.domain,U[domain]));graph.add((U[name],RDFS.range,U[range_]))
    data=[('codeFamily','CodeFamilyConcept',XSD.string),('familyFunction','CodeFamilyConcept',XSD.string),
        ('familyDefinitionSource','CodeFamilyConcept',XSD.string),('catalogOrigin','Catalog',XSD.string),
        ('catalogSourceSHA256','Catalog',XSD.string),('catalogSourcePage','Catalog',XSD.integer),
        ('entryTypeNumber','CatalogEntry',XSD.string),('canonicalTypeCode','TypeDesignationHypothesis',XSD.string),
        ('observedFamily','TypeDesignationHypothesis',XSD.string),('observedTypeNumber','TypeDesignationHypothesis',XSD.string),
        ('codeReadingState','TypeDesignationHypothesis',XSD.string),('assignmentScope','TypeAssignmentHypothesis',XSD.string)]
    for name,domain,range_ in data:
        graph.add((U[name],RDF.type,OWL.DatatypeProperty));graph.add((U[name],RDFS.domain,U[domain]));graph.add((U[name],RDFS.range,range_))
    # Reuse the user's existing glossary, rather than declaring PD/1 as a Door
    # class or copying its material onto a guessed physical object.
    families={code.split('/')[0]:(category,material,function) for code,category,_,material,function in BASE_ELEMENT_CODES}
    for family,(category,material,function) in families.items():
        node=U['CodeFamily_'+family];graph.add((node,RDF.type,U.CodeFamilyConcept));graph.add((node,U.codeFamily,Literal(family)))
        graph.add((node,U.familyEntityType,U['EntityType_'+category]));mat=U['MaterialConcept_'+material]
        graph.add((mat,RDF.type,U.MaterialConcept));graph.add((node,U.familyMaterial,mat))
        graph.add((node,U.familyDefinitionSource,Literal('schema.py:BASE_ELEMENT_CODES; USER_INITIAL_GLOSSARY')))
        if function:graph.add((node,U.familyFunction,Literal(function)))
    for name,comment in {
        'CodeFamilyConcept':'User-supplied abbreviation meaning; not a project-specific specification or physical element.',
        'CatalogEntry':'A type within one explicitly sourced catalog. Equal code strings in different catalogs are not the same type.',
        'TypeDesignationHypothesis':'Observed symbol family/number interpreted through a catalog; not an actual Door or Window.',
        'TypeAssignmentHypothesis':'An evidence-scoped hypothesis associating a distinct physical-element hypothesis with a catalog entry.',
        'entryTypeNumber':'Preserve the printed type number as a string. It is not a count or physical instance ID.',
    }.items():graph.set((U[name],RDFS.comment,Literal(comment)))
    return graph
