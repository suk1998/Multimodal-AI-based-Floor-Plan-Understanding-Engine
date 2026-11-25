"""FPUE generic spatial ontology and initial vocabulary (RDFLib, Python 3.10+).

Run: python schema.py --output-dir output
The ontology schema does not contain floor-plan-type-specific relationships.
"""
from __future__ import annotations

import argparse
import re
import json
from urllib.parse import quote

from rdflib import Graph, Literal, Namespace
from rdflib.namespace import OWL, RDF, RDFS, XSD

if __package__:
    from .rules import rule_spec, rule_digest
    from .vocabulary import BASE_SPACE_TERMS, BASE_ELEMENT_CODES, normalize_label, vocabulary_digest
else:
    from rules import rule_spec, rule_digest
    from vocabulary import BASE_SPACE_TERMS, BASE_ELEMENT_CODES, normalize_label, vocabulary_digest

FP = Namespace("https://example.org/fpue/ontology#")
VOC = Namespace("https://example.org/fpue/vocabulary/")

# (canonical label, generic type, number or None, optional role)

# (type code, class category, name, material, role/feature): user-provided code glossary


def bind(g: Graph) -> Graph:
    for prefix, namespace in (("fp", FP), ("voc", VOC), ("owl", OWL), ("rdf", RDF),
                              ("rdfs", RDFS), ("xsd", XSD)):
        g.bind(prefix, namespace)
    return g


def space_term_uri(label: str):
    return VOC["space-term/" + quote(label, safe="")]


def space_type_uri(type_name: str):
    return VOC["space-type/" + quote(type_name, safe="")]


def element_type_uri(catalog_scope: str, code: str):
    # Catalog scoped: code WD/1 in a different project is not necessarily the same spec.
    return VOC["element-type/" + quote(catalog_scope, safe="") + "/" + quote(code, safe="")]


def material_uri(material: str):
    return VOC["material/" + quote(material, safe="")]


def normalize_space_label(raw: str) -> str:
    """Conservative spelling normalization. Never hallucinate a room from OCR garbage."""
    return normalize_label(raw)


def space_term_for_label(raw: str) -> tuple[str, str, int | None, str | None] | None:
    norm = normalize_space_label(raw)
    return next((r for r in BASE_SPACE_TERMS if normalize_space_label(r[0]) == norm), None)



def register_space_term(g: Graph, label: str, type_name: str,
                        number: int | None = None, role: str | None = None) -> None:
    """Extend vocabulary without changing the ontology schema (e.g. 서재 -> StudyRoom)."""
    if not label.strip() or not type_name.strip():
        raise ValueError("label and type_name must be nonempty")
    type_iri, term_iri = space_type_uri(type_name), space_term_uri(label)
    g.add((type_iri, RDF.type, FP.SpaceType))
    g.add((type_iri, RDFS.label, Literal(type_name, lang="en")))
    g.add((term_iri, RDF.type, FP.SpaceTerm))
    g.add((term_iri, FP.canonicalLabel, Literal(label, lang="ko")))
    g.add((term_iri, FP.mapsToSpaceType, type_iri))
    if number is not None:
        g.add((term_iri, FP.spaceOrdinal, Literal(number, datatype=XSD.integer)))
    if role:
        g.add((term_iri, FP.spaceRole, Literal(role)))


def register_element_type(g: Graph, catalog_scope: str, code: str,
                          category: str | None = None, name: str | None = None,
                          material: str | None = None,
                          function: str | None = None) -> None:
    """Extend the project-local catalog; unset attributes stay unset, never guessed."""
    if not code.strip() or not catalog_scope.strip():
        raise ValueError("catalog_scope and code must be nonempty")
    iri = element_type_uri(catalog_scope, code)
    g.add((iri, RDF.type, FP.ElementType))
    g.add((iri, FP.catalogScope, Literal(catalog_scope)))
    g.add((iri, FP.typeCode, Literal(code)))
    if category:
        g.add((iri, FP.elementCategoryName, Literal(category)))
    if name:
        g.add((iri, FP.typeName, Literal(name)))
    if material:
        mat = material_uri(material)
        g.add((mat, RDF.type, FP.Material))
        g.add((mat, RDFS.label, Literal(material, lang="en")))
        g.add((iri, FP.hasMaterial, mat))
    if function:
        g.add((iri, FP.functionalRole, Literal(function)))


def build_schema() -> Graph:
    """Only reusable ontology classes/properties and small controlled enumerations."""
    g = bind(Graph())
    g.add((FP[""], RDF.type, OWL.Ontology))
    g.add((FP[""], OWL.versionInfo, Literal("0.2.2-spatial-integration")))
    for predicate in (FP.applicationRulesJSON, FP.applicationRulesSHA256, FP.initialSpaceVocabularySHA256):
        g.add((predicate, RDF.type, OWL.AnnotationProperty))
    g.add((FP[""], FP.applicationRulesJSON, Literal(json.dumps(rule_spec(), sort_keys=True))))
    g.add((FP[""], FP.applicationRulesSHA256, Literal(rule_digest())))
    g.add((FP[""], FP.initialSpaceVocabularySHA256, Literal(vocabulary_digest())))
    g.add((FP[""], RDFS.label, Literal("FPUE Spatial Ontology v0.2.2 / 범용 공간·건축요소", lang="ko")))
    g.add((FP[""], RDFS.comment, Literal(
        "스키마는 도면 평형과 무관하다. 관계의 존재는 시각·기하 근거로 판정한다.", lang="ko")))

    classes = {
        "FloorPlan": None, "PlanPair": None, "CandidateRecord": None,
        "Claim": None, "DeferredRecord": None,
        "ObservedEntity": None, "SpatialEntity": "ObservedEntity",
        "GeometryObservation": "ObservedEntity", "UnclassifiedObservation": "ObservedEntity",
        "UnclassifiedElement": "BuildingElement",
        "Space": "SpatialEntity", "BuildingElement": "SpatialEntity",
        "TextRegion": "ObservedEntity", "Symbol": "ObservedEntity",
        "Wall": "BuildingElement", "Door": "BuildingElement",
        "FireDoor": "Door", "Window": "BuildingElement",
        "Grille": "BuildingElement", "PipeShaft": "BuildingElement",
        "Opening": "BuildingElement", "SpaceType": None, "SpaceTerm": None,
        "ElementType": None, "Material": None, "Geometry": None,
        "CoordinateFrame": None, "ObservationRelation": None,
        "SpatialRelation": "ObservationRelation",
        "LabelAssociation": "ObservationRelation",
        "BoundaryAssociation": "SpatialRelation",
        "HostingRelation": "SpatialRelation",
        "ReferenceAssociation": "ObservationRelation",
        "Connection": "SpatialRelation", "Adjacency": "SpatialRelation",
        "Containment": "SpatialRelation", "ElementAssociation": "SpatialRelation",
        "ConnectionMode": None, "LocalDirection": None, "ReviewStatus": None,
        "LabelState": None, "Correspondence": None, "ChangeKind": None,
        "ChangeEvent": None,
    }
    for name, parent in classes.items():
        g.add((FP[name], RDF.type, OWL.Class))
        if parent:
            g.add((FP[name], RDFS.subClassOf, FP[parent]))
    g.add((FP.Space, OWL.disjointWith, FP.BuildingElement))

    # (property, domain, range). Domain omitted when used by multiple entity kinds.
    object_properties = (
        ("inPlan", None, "FloorPlan"), ("referencePlan", "PlanPair", "FloorPlan"),
        ("targetPlan", "PlanPair", "FloorPlan"), ("hasFrame", "FloorPlan", "CoordinateFrame"),
        ("hasGeometry", None, "Geometry"), ("inFrame", "Geometry", "CoordinateFrame"),
        ("hasSpaceType", "Space", "SpaceType"), ("hasSpaceTerm", "Space", "SpaceTerm"),
        ("mapsToSpaceType", "SpaceTerm", "SpaceType"),
        ("hasElementType", "BuildingElement", "ElementType"),
        ("hasMaterial", "ElementType", "Material"),
        ("relationSource", "ObservationRelation", "ObservedEntity"),
        ("relationTarget", "ObservationRelation", "ObservedEntity"),
        ("connectionMode", "Connection", "ConnectionMode"),
        ("viaElement", "Connection", "BuildingElement"),
        ("openingSide", None, "LocalDirection"),
        ("reviewStatus", None, "ReviewStatus"), ("labelState", "Space", "LabelState"),
        ("hasClaim", None, "Claim"), ("claimSubject", "Claim", "CandidateRecord"),
        ("refEntity", "Correspondence", "SpatialEntity"),
        ("targetEntity", "Correspondence", "SpatialEntity"),
        ("refRelation", "ChangeEvent", "SpatialRelation"),
        ("targetRelation", "ChangeEvent", "SpatialRelation"),
        ("aboutCorrespondence", "ChangeEvent", "Correspondence"),
        ("changeKind", "ChangeEvent", "ChangeKind"),
    )
    for name, domain, range_ in object_properties:
        p = FP[name]
        g.add((p, RDF.type, OWL.ObjectProperty))
        if domain:
            g.add((p, RDFS.domain, FP[domain]))
        g.add((p, RDFS.range, FP[range_]))

    data_properties = (
        ("planVariant", "FloorPlan", XSD.string), ("planRole", "FloorPlan", XSD.string),
        ("planId", "FloorPlan", XSD.string),
        ("spaceFunction", "Space", XSD.string),  # retained for v0.1 compatibility
        ("visibleLabel", "Space", XSD.string),
        ("recognizedText", "TextRegion", XSD.string),
        ("canonicalLabel", "SpaceTerm", RDFS.Literal),
        ("spaceOrdinal", None, XSD.integer), ("spaceRole", None, XSD.string),
        ("sourceId", None, XSD.string), ("typeCode", "ElementType", XSD.string),
        ("typeName", "ElementType", XSD.string),
        ("typeNumber", "ElementType", XSD.integer),
        ("catalogScope", "ElementType", XSD.string),
        ("elementCategoryName", "ElementType", XSD.string),
        ("functionalRole", "ElementType", XSD.string),
        ("minX", "Geometry", XSD.decimal), ("minY", "Geometry", XSD.decimal),
        ("maxX", "Geometry", XSD.decimal), ("maxY", "Geometry", XSD.decimal),
        ("polygonWKT", "Geometry", XSD.string),
        ("frameDefinition", "CoordinateFrame", XSD.string),
        ("coordinateKind", "CoordinateFrame", XSD.string),
        ("imageWidth", "CoordinateFrame", XSD.integer),
        ("imageHeight", "CoordinateFrame", XSD.integer),
        ("northRotationDeg", "CoordinateFrame", XSD.decimal),
        ("confidence", None, XSD.decimal), ("annotationSource", None, XSD.string),
        ("evidenceRef", None, XSD.string), ("evidenceNote", None, XSD.string),
        ("frameId", None, XSD.string), ("sourceRole", None, XSD.string),
        ("sourceRecordJSON", None, XSD.string), ("geometryJSON", "Geometry", XSD.string),
        ("sourceBindingJSON", "FloorPlan", XSD.string),
        ("frameRegistryJSON", "CoordinateFrame", XSD.string),
        ("bundleMetadataJSON", "FloorPlan", XSD.string), ("mappingJSON", None, XSD.string),
        ("claimPredicate", "Claim", XSD.string), ("claimValueJSON", "Claim", XSD.string),
        ("claimScope", "Claim", XSD.string), ("criteriaVersion", "Claim", XSD.string),
        ("deferredReason", "DeferredRecord", XSD.string),
        ("recordSemantics", "FloorPlan", XSD.string),
    )
    for name, domain, range_ in data_properties:
        p = FP[name]
        g.add((p, RDF.type, OWL.DatatypeProperty))
        if domain:
            g.add((p, RDFS.domain, FP[domain]))
        g.add((p, RDFS.range, range_))

    vocabularies = {
        "ConnectionMode": ["DoorMode", "OpenPassage", "OpenPlan", "UnknownMode"],
        "LocalDirection": ["North", "East", "South", "West", "UnknownSide"],
        "ReviewStatus": ["Proposed", "Confirmed", "Uncertain", "Rejected", "Supported",
                         "Verified", "Conflicted", "Stale", "Unknown"],
        "LabelState": ["LabelPresent", "LabelAbsent", "LabelUnknown"],
        "ChangeKind": ["Unchanged", "Added", "Removed", "ConnectionChanged",
                       "ElementTypeChanged", "GeometryChanged", "OpeningRelocated",
                       "LabelRemoved", "ChangeUncertain"],
    }
    for cls, names in vocabularies.items():
        for name in names:
            g.add((FP[name], RDF.type, FP[cls]))
    g.add((FP.northRotationDeg, RDFS.comment, Literal(
        "실제 방위가 알려진 경우만 저장; 화면 위쪽을 실제 북쪽으로 간주하지 않음.", lang="ko")))
    g.add((FP.relationSource, RDFS.comment, Literal(
        "직접 연결의 양쪽 끝을 기록하는 순서이며 출입 방향을 뜻하지 않음.", lang="ko")))
    g.add((FP.CandidateRecord, RDFS.comment, Literal(
        "RDF instance is a source candidate record, not a verified physical fact. "
        "Use scoped Claims and reviewStatus; rdf:type alone is not verification.")))
    g.add((FP.LabelAssociation, RDFS.comment, Literal(
        "TextRegion/Symbol labels a Space or Symbol; a symbol label is not a room label.")))
    g.add((FP.geometryJSON, RDFS.comment, Literal(
        "Exact native geometry JSON, including partial boundaries, holes and endpoint semantics. "
        "A bbox is only an extent. PDF transforms are preserved, never implicitly applied.")))
    return g


def build_base_vocabulary(catalog_scope: str = "INITIAL_USER_VOCAB") -> Graph:
    """Initial 14 space labels and 14 catalog-scoped codes; NOT ontology constraints."""
    if not catalog_scope.strip():
        raise ValueError("catalog_scope must not be empty")
    g = bind(Graph())
    for label, type_name, number, role in BASE_SPACE_TERMS:
        type_iri = space_type_uri(type_name)
        term_iri = space_term_uri(label)
        g.add((type_iri, RDF.type, FP.SpaceType))
        g.add((type_iri, RDFS.label, Literal(type_name, lang="en")))
        g.add((term_iri, RDF.type, FP.SpaceTerm))
        g.add((term_iri, FP.canonicalLabel, Literal(label, lang="ko")))
        g.add((term_iri, FP.mapsToSpaceType, type_iri))
        if number is not None:
            g.add((term_iri, FP.spaceOrdinal, Literal(number, datatype=XSD.integer)))
        if role:
            g.add((term_iri, FP.spaceRole, Literal(role)))

    for code, category, name, material, function in BASE_ELEMENT_CODES:
        type_iri = element_type_uri(catalog_scope, code)
        g.add((type_iri, RDF.type, FP.ElementType))
        g.add((type_iri, FP.typeCode, Literal(code)))
        g.add((type_iri, FP.typeName, Literal(name)))
        g.add((type_iri, FP.elementCategoryName, Literal(category)))
        g.add((type_iri, FP.catalogScope, Literal(catalog_scope)))
        g.add((type_iri, FP.typeNumber, Literal(int(code.split("/")[-1]), datatype=XSD.integer)))
        material_iri = material_uri(material)
        g.add((material_iri, RDF.type, FP.Material))
        g.add((material_iri, RDFS.label, Literal(material, lang="en")))
        g.add((type_iri, FP.hasMaterial, material_iri))
        if function:
            g.add((type_iri, FP.functionalRole, Literal(function)))
    return g


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate FPUE OWL schema and initial vocabulary")
    parser.add_argument("--output-dir", default="output")
    parser.add_argument("--catalog-scope", default="INITIAL_USER_VOCAB")
    parser.add_argument("--schema-version", choices=["0.2", "0.3", "0.4", "0.5"], default="0.2",
                        help="0.2 keeps the deployed contract; later versions explicitly select understanding schemas")
    args = parser.parse_args()
    from pathlib import Path
    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    if args.schema_version == "0.3":
        if __package__:
            from .schema_v0_3 import build_understanding_schema
        else:
            import sys
            sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
            from integrations.fpue_ontology.schema_v0_3 import build_understanding_schema
        targets = [out / "fpue_understanding_v0_3.ttl"]
        graphs = [build_understanding_schema()]
    elif args.schema_version == "0.4":
        if __package__:
            from .schema_v0_4 import build_understanding_schema
        else:
            import sys
            sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
            from integrations.fpue_ontology.schema_v0_4 import build_understanding_schema
        targets = [out / "fpue_understanding_v0_4.ttl"]
        graphs = [build_understanding_schema()]
    elif args.schema_version == "0.5":
        if __package__:
            from .schema_v0_5 import build_understanding_schema
        else:
            import sys
            sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
            from integrations.fpue_ontology.schema_v0_5 import build_understanding_schema
        targets = [out / "fpue_understanding_v0_5.ttl"]
        graphs = [build_understanding_schema()]
    else:
        targets = [out / "fpue_schema_v0_2.ttl", out / "fpue_initial_vocabulary.ttl"]
        graphs = [build_schema(), build_base_vocabulary(args.catalog_scope)]
    if any(p.exists() for p in targets):
        parser.error("output exists; choose a new output directory")
    for path, graph in zip(targets, graphs):
        with path.open("x", encoding="utf-8") as stream:
            stream.write(graph.serialize(format="turtle"))
    for path in targets:
        print(f"Created: {path}")


if __name__ == "__main__":
    main()
