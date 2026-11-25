"""Convert one plan's explicit candidate records to RDF without semantic inference.

RDF instances describe candidate records. Their rdf:type is not verification of a
physical object. Scoped Claims, statuses, native geometry and source payloads stay
attached. No relation is materialized as a direct connectedTo/hosts/etc. fact.
"""
from __future__ import annotations

import argparse
import json
import math
from decimal import Decimal
from pathlib import Path
from urllib.parse import quote

from rdflib import Graph, Literal, Namespace
from rdflib.namespace import RDF, XSD

if __package__:
    from .schema import FP, bind, element_type_uri, space_term_for_label, space_term_uri, space_type_uri, normalize_space_label
    from .rules import validate_signature
else:
    from schema import FP, bind, element_type_uri, space_term_for_label, space_term_uri, space_type_uri, normalize_space_label
    from rules import validate_signature

DATA = Namespace("https://example.org/fpue/data/")
ELEMENT_CLASSES = {"Door", "FireDoor", "Window", "Grille", "Wall", "PipeShaft", "Opening", "UnclassifiedElement"}
OBSERVATION_CLASSES = {"TextRegion", "Symbol", "GeometryObservation", "UnclassifiedObservation"}
RELATION_CLASSES = {"Connection", "Adjacency", "Containment", "ElementAssociation", "LabelAssociation", "BoundaryAssociation", "HostingRelation", "ReferenceAssociation"}
MODES = {"Door": "DoorMode", "OpenPassage": "OpenPassage", "OpenPlan": "OpenPlan", "Unknown": "UnknownMode"}
REVIEW = {"PROPOSED": "Proposed", "CONFIRMED": "Confirmed", "UNCERTAIN": "Uncertain", "REJECTED": "Rejected", "SUPPORTED": "Supported", "VERIFIED": "Verified", "CONFLICTED": "Conflicted", "STALE": "Stale", "UNKNOWN": "Unknown"}
LABEL_STATE = {"PRESENT": "LabelPresent", "ABSENT": "LabelAbsent", "UNKNOWN": "LabelUnknown"}
DIRECTIONS = {"North", "East", "South", "West", "UnknownSide"}


def _iri(*parts: str):
    return DATA["/".join(quote(str(p), safe="") for p in parts)]


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False, separators=(",", ":"))


def _literal_number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)) or not Decimal(str(value)).is_finite():
        raise ValueError(f"expected finite numeric geometry value, got {value!r}")
    lexical = format(Decimal(str(value)), 'f')
    if '.' not in lexical:
        lexical += '.0'
    return Literal(lexical, datatype=XSD.decimal)


def _status(g: Graph, node, obj: dict) -> None:
    state = obj.get("status", "PROPOSED")
    if state not in REVIEW:
        raise ValueError(f"unsupported status: {state!r}")
    g.add((node, FP.reviewStatus, FP[REVIEW[state]]))
    g.add((node, FP.annotationSource, Literal(str(obj.get("annotation_source", "FPUE_PROPOSAL")))))
    for key, predicate in (("evidence_ref", FP.evidenceRef), ("evidence_note", FP.evidenceNote)):
        if obj.get(key) is not None:
            g.add((node, predicate, Literal(str(obj[key]))))
    for evidence_id in obj.get("evidence_ids", []):
        g.add((node, FP.evidenceRef, Literal(str(evidence_id))))
    if obj.get("confidence") is not None:
        conf = obj["confidence"]
        if isinstance(conf, bool) or not isinstance(conf, (int, float)) or not math.isfinite(conf) or not 0 <= conf <= 1:
            raise ValueError(f"invalid confidence {conf!r}; must be within [0, 1]")
        g.add((node, FP.confidence, _literal_number(conf)))


def _check_bbox(bbox, width=None, height=None):
    if not isinstance(bbox, list) or len(bbox) != 4:
        raise ValueError("bbox must be [minX,minY,maxX,maxY]")
    for v in bbox:
        _literal_number(v)
    x0, y0, x1, y1 = bbox
    if not x0 < x1 or not y0 < y1:
        raise ValueError("bbox has invalid dimensions")
    if x0 < 0 or (width is not None and x1 > width):
        raise ValueError("bbox outside frame width")
    if y0 < 0 or (height is not None and y1 > height):
        raise ValueError("bbox outside frame height")


def _check_native_geometry(value, width=None, height=None):
    """Validate declared native point arrays; do not turn arbitrary arrays into points."""
    coordinate_keys = {"point", "points", "points_native", "seed_native", "polygon_native", "polygons_native", "polylines_native", "outer_rings", "holes", "polygon", "boundaries", "circle_center_native", "logical_boundary_segment_native", "center_path_native", "boundary_segments_native", "observed_endpoints_native"}

    def points(v):
        if v is None:
            return
        if isinstance(v, dict):
            walk(v)
        elif isinstance(v, list):
            if len(v) == 2 and all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in v):
                x, y = v
                _literal_number(x); _literal_number(y)
                if x < 0 or (width is not None and x > width) or y < 0 or (height is not None and y > height):
                    raise ValueError("native point outside frame")
            else:
                for child in v:
                    points(child)
        else:
            raise ValueError("native points must be numeric [x,y] arrays")

    def walk(v):
        if isinstance(v, dict):
            if v.get("coordinate_system", "image_native_pixels") not in {"image_native_pixels", "image_native"}:
                raise ValueError("geometry must use the declared native pixel frame")
            for key, child in v.items():
                if key in coordinate_keys:
                    points(child)
                elif isinstance(child, (dict, list)):
                    walk(child)
        elif isinstance(v, list):
            for child in v:
                if isinstance(child, (dict, list)):
                    walk(child)
    walk(value)
    _json(value)  # Reject non-finite numbers anywhere, including ancillary measurements.


def _add_geometry(g, entity, frame, record, width, height):
    bbox, geometry = record.get("bbox"), record.get("geometry_native")
    if bbox is None and geometry is None:
        return
    if bbox is not None:
        _check_bbox(bbox, width, height)
    if geometry is not None:
        if not isinstance(geometry, dict):
            raise ValueError("geometry_native must be a dict or null")
        _check_native_geometry(geometry, width, height)
    geo = entity + "/geometry"
    from rdflib import URIRef
    geo = URIRef(geo)
    g.add((geo, RDF.type, FP.Geometry)); g.add((geo, FP.inFrame, frame)); g.add((entity, FP.hasGeometry, geo))
    if geometry is not None:
        g.add((geo, FP.geometryJSON, Literal(_json(geometry))))
    if bbox is not None:
        for prop, value in zip((FP.minX, FP.minY, FP.maxX, FP.maxY), bbox):
            g.add((geo, prop, _literal_number(value)))


def _validate_objects(records, group):
    if not isinstance(records, list):
        raise ValueError(f"{group} must be a list")
    ids = {}
    for item in records:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not item["id"].strip():
            raise ValueError(f"{group}: each record needs nonempty string id")
        if item["id"] in ids:
            raise ValueError(f"duplicate {group} id: {item['id']}")
        ids[item["id"]] = item
    return ids


def _resolve_space_term(raw, vocab):
    if vocab is not None:
        found = {term for term in vocab.subjects(RDF.type, FP.SpaceTerm) for label in vocab.objects(term, FP.canonicalLabel) if normalize_space_label(str(label)) == normalize_space_label(raw)}
        if len(found) > 1:
            raise ValueError(f"ambiguous vocabulary label {raw!r}")
        if found:
            term = found.pop()
            types = list(vocab.objects(term, FP.mapsToSpaceType))
            if len(types) > 1:
                raise ValueError(f"ambiguous vocabulary type for {raw!r}")
            ordinal = vocab.value(term, FP.spaceOrdinal)
            role = vocab.value(term, FP.spaceRole)
            return term, types[0] if types else None, int(ordinal) if ordinal is not None else None, str(role) if role is not None else None
    known = space_term_for_label(raw)
    if known:
        label, name, number, role = known
        return space_term_uri(label), space_type_uri(name), number, role
    return None


def validate_relation(record, spaces, elements, observations):
    """Closed-world input validation, separate from OWL domain/range inference."""
    rid, kind = record.get("id"), record.get("kind")
    if kind not in RELATION_CLASSES:
        raise ValueError(f"{rid}: unsupported relationship kind {kind!r}")
    src, dst = record.get("source_id"), record.get("target_id")
    if src not in spaces | elements | observations or dst not in spaces | elements | observations:
        raise ValueError(f"{rid}: unknown relation endpoint {src!r}/{dst!r}")
    if src == dst:
        raise ValueError(f"{rid}: self relation is not allowed")
    def category(identity):
        return 'Space' if identity in spaces else (elements | observations)[identity]['category']
    validate_signature(kind, category(src), category(dst))
    if kind in {"Connection", "Adjacency"} and not (src in spaces and dst in spaces):
        raise ValueError(f"{rid}: {kind} endpoints must both be Spaces")
    if kind in {"ElementAssociation", "Containment"} and not (src in spaces and dst in elements):
        raise ValueError(f"{rid}: {kind} requires source Space and target BuildingElement")
    if kind == "LabelAssociation" and not (src in observations and observations[src]["category"] in {"TextRegion", "Symbol"} and (dst in spaces or (dst in observations and observations[dst]["category"] == "Symbol"))):
        raise ValueError(f"{rid}: LabelAssociation requires TextRegion/Symbol -> Space/Symbol")
    if kind == "BoundaryAssociation" and not (src in spaces and dst in elements and elements[dst]["category"] in {"Wall", "Opening", "Door", "FireDoor", "Window"}):
        raise ValueError(f"{rid}: BoundaryAssociation requires Space -> Wall/Opening/Door/Window")
    if kind == "HostingRelation" and not (src in elements and elements[src]["category"] == "Wall" and dst in elements and elements[dst]["category"] in {"Opening", "Door", "FireDoor", "Window", "Grille"}):
        raise ValueError(f"{rid}: HostingRelation requires Wall -> Opening/Door/Window/Grille")
    if kind == "ReferenceAssociation" and not (src in observations and observations[src]["category"] == "Symbol" and dst in elements):
        raise ValueError(f"{rid}: ReferenceAssociation requires Symbol -> BuildingElement")
    if kind == "Connection":
        mode, via = record.get("mode", "Unknown"), record.get("via_element_id")
        if mode not in MODES:
            raise ValueError(f"{rid}: unsupported connection mode {mode!r}")
        if via is not None and (via not in elements or elements[via]["category"] not in {"Door", "FireDoor", "Opening"}):
            raise ValueError(f"{rid}: via_element_id must be a Door or Opening")
        if mode == "Door" and (via is None or elements[via]["category"] not in {"Door", "FireDoor"}):
            raise ValueError(f"{rid}: Door connection requires observed Door via_element_id")
        if record.get("annotation_source") != "USER_GROUND_TRUTH" and not (record.get("evidence_ids") or record.get("evidence_ref")):
            raise ValueError(f"{rid}: Connection requires explicit observation evidence")
        # The handoff manual subset explicitly describes an OpenPassage without a
        # physical instance. Keep that example assertion; raw FPUE adapters require
        # observation evidence and an explicit passage for all physical connections.
        if mode == "OpenPassage" and via is None and record.get("annotation_source") != "USER_GROUND_TRUTH":
            raise ValueError(f"{rid}: OpenPassage requires observed Opening via_element_id")
        if mode in {"Unknown", "OpenPlan"} and not (record.get("evidence_ids") or record.get("evidence_ref")):
            raise ValueError(f"{rid}: Connection requires explicit observation evidence")


def convert_normalized(data: dict, vocabulary: Graph | None = None) -> Graph:
    """Convert explicit records for one plan/frame; never confirm or complete them."""
    if not isinstance(data, dict):
        raise ValueError("normalized input must be a dict")
    _json(data)
    doc = data.get("document")
    if not isinstance(doc, dict) or not isinstance(doc.get("id"), str) or not doc["id"].strip():
        raise ValueError("document.id is required")
    role = doc.get("role")
    if role not in {"REF", "TARGET", "SINGLE"}:
        raise ValueError("document.role must be REF, TARGET, or SINGLE")
    catalog = doc.get("catalog_scope", "INITIAL_USER_VOCAB")
    if not isinstance(catalog, str) or not catalog.strip():
        raise ValueError("document.catalog_scope must be nonempty")
    groups = {key: _validate_objects(data.get(key, []), key) for key in ("spaces", "elements", "observations", "relations", "claims", "deferred_records")}
    all_ids = [sid for records in groups.values() for sid in records]
    if len(set(all_ids)) != len(all_ids):
        raise ValueError("entity IDs must be unique across all record groups")
    spaces, elements, observations = (groups[k] for k in ("spaces", "elements", "observations"))
    frame_info = doc.get("coordinate_frame") or {}
    if not isinstance(frame_info, dict):
        raise ValueError("coordinate_frame must be a dict")
    frame_id = frame_info.get("id", doc["id"])
    if not isinstance(frame_id, str) or not frame_id.strip():
        raise ValueError("coordinate_frame.id must be a nonempty string")
    width, height = frame_info.get("width"), frame_info.get("height")
    for name, dimension in (("width", width), ("height", height)):
        if dimension is not None and (type(dimension) is not int or dimension <= 0):
            raise ValueError(f"coordinate_frame.{name} must be a positive integer")
    coordinate_kind = frame_info.get("kind", "image_native")
    if coordinate_kind not in {"image_native", "image_native_pixels"}:
        raise ValueError("only native pixel geometry is supported; no implicit PDF conversion")
    g = bind(Graph()); g.bind("data", DATA)
    prefix = ("plan", role, doc["id"], frame_id)
    plan, frame = _iri(*prefix), _iri("frame", role, doc["id"], frame_id)
    g.add((plan, RDF.type, FP.FloorPlan)); g.add((plan, FP.planId, Literal(doc["id"]))); g.add((plan, FP.planRole, Literal(role)))
    g.add((plan, FP.recordSemantics, Literal("candidate records; no semantic verification or OWL inference performed")))
    if doc.get("variant") is not None:
        g.add((plan, FP.planVariant, Literal(str(doc["variant"]))))
    g.add((frame, RDF.type, FP.CoordinateFrame)); g.add((plan, FP.hasFrame, frame))
    g.add((frame, FP.frameId, Literal(frame_id))); g.add((frame, FP.coordinateKind, Literal(coordinate_kind)))
    g.add((frame, FP.frameDefinition, Literal(frame_info.get("axis_definition", "origin=top-left;+x=right;+y=down;geographic-north=unknown"))))
    for prop, dimension in ((FP.imageWidth, width), (FP.imageHeight, height)):
        if dimension is not None:
            g.add((frame, prop, Literal(dimension, datatype=XSD.integer)))
    if frame_info.get("north_rotation_deg") is not None:
        g.add((frame, FP.northRotationDeg, _literal_number(frame_info["north_rotation_deg"])))
    for key, pred, node in (("source_binding", FP.sourceBindingJSON, plan), ("frame_registry", FP.frameRegistryJSON, frame), ("metadata", FP.bundleMetadataJSON, plan)):
        if key in doc:
            g.add((node, pred, Literal(_json(doc[key]))))
    nodes = {}
    for group in ("spaces", "elements", "observations", "relations"):
        for rid, record in groups[group].items():
            if record.get("frame_id", frame_id) != frame_id or record.get("source_role", role) != role:
                raise ValueError(f"{rid}: record role/frame differs from document")
            nodes[rid] = _iri(*prefix, group, rid)
    for group in ("spaces", "elements", "observations", "relations"):
        for rid, record in groups[group].items():
            node = nodes[rid]
            category = "Space" if group == "spaces" else record.get("kind") if group == "relations" else record.get("category")
            allowed = {"Space"} if group == "spaces" else RELATION_CLASSES if group == "relations" else ELEMENT_CLASSES if group == "elements" else OBSERVATION_CLASSES
            if category not in allowed:
                raise ValueError(f"{rid}: unsupported category {category!r}")
            if group == "relations":
                validate_relation(record, spaces, elements, observations)
            g.add((node, RDF.type, FP[category])); g.add((node, RDF.type, FP.CandidateRecord))
            g.add((node, FP.inPlan, plan)); g.add((node, FP.sourceId, Literal(rid)))
            g.add((node, FP.sourceRole, Literal(role))); g.add((node, FP.frameId, Literal(frame_id)))
            _status(g, node, record)
            g.add((node, FP.mappingJSON, Literal(_json({k: v for k, v in record.items() if k != "source_record"}))))
            if "source_record" in record:
                g.add((node, FP.sourceRecordJSON, Literal(_json(record["source_record"]))))
            _add_geometry(g, node, frame, record, width, height)
            if group == "spaces":
                label = record.get("raw_label")
                if label:
                    g.add((node, FP.visibleLabel, Literal(str(label))))
                    term = _resolve_space_term(str(label), vocabulary)
                    if term:
                        term_iri, type_iri, number, room_role = term
                        g.add((node, FP.hasSpaceTerm, term_iri))
                        if type_iri is not None:
                            g.add((node, FP.hasSpaceType, type_iri))
                        if number is not None:
                            g.add((node, FP.spaceOrdinal, Literal(number, datatype=XSD.integer)))
                        if room_role:
                            g.add((node, FP.spaceRole, Literal(room_role)))
                if record.get("space_type"):
                    typ = space_type_uri(str(record["space_type"]))
                    g.add((typ, RDF.type, FP.SpaceType)); g.add((node, FP.hasSpaceType, typ))
                label_state = record.get("label_state", "UNKNOWN")
                if label_state not in LABEL_STATE or (label_state == "ABSENT" and label):
                    raise ValueError(f"{rid}: invalid label_state for supplied raw_label")
                g.add((node, FP.labelState, FP[LABEL_STATE[label_state]]))
            if group == "elements" and record.get("type_code"):
                code = str(record["type_code"]); typ = element_type_uri(catalog, code)
                g.add((node, FP.hasElementType, typ)); g.add((typ, RDF.type, FP.ElementType))
                g.add((typ, FP.typeCode, Literal(code))); g.add((typ, FP.catalogScope, Literal(catalog)))
            if category == "TextRegion" and record.get("raw_text") is not None:
                g.add((node, FP.recognizedText, Literal(str(record["raw_text"]))))
            if group == "relations":
                g.add((node, FP.relationSource, nodes[record["source_id"]])); g.add((node, FP.relationTarget, nodes[record["target_id"]]))
                if category == "Connection":
                    g.add((node, FP.connectionMode, FP[MODES[record.get("mode", "Unknown")]]))
                    if record.get("via_element_id") is not None:
                        g.add((node, FP.viaElement, nodes[record["via_element_id"]]))
                if record.get("opening_side") is not None:
                    if record["opening_side"] not in DIRECTIONS:
                        raise ValueError(f"{rid}: invalid local opening_side")
                    g.add((node, FP.openingSide, FP[record["opening_side"]]))
    for cid, claim in groups["claims"].items():
        subject = claim.get("subject")
        if subject not in nodes:
            raise ValueError(f"{cid}: unknown claim subject {subject!r}")
        node = _iri(*prefix, "claims", cid)
        g.add((node, RDF.type, FP.Claim)); g.add((node, FP.inPlan, plan)); g.add((node, FP.sourceId, Literal(cid)))
        g.add((node, FP.claimSubject, nodes[subject])); g.add((nodes[subject], FP.hasClaim, node))
        _status(g, node, claim)
        g.add((node, FP.sourceRecordJSON, Literal(_json(claim.get("source_record", claim)))))
        for key, prop in (("predicate", FP.claimPredicate), ("scope", FP.claimScope), ("criteria_version", FP.criteriaVersion)):
            if key in claim:
                g.add((node, prop, Literal(str(claim[key]))))
        if "value" in claim:
            g.add((node, FP.claimValueJSON, Literal(_json(claim["value"]))))
    for rid, record in groups["deferred_records"].items():
        node = _iri(*prefix, "deferred", rid)
        g.add((node, RDF.type, FP.DeferredRecord)); g.add((node, FP.inPlan, plan)); g.add((node, FP.sourceId, Literal(rid)))
        g.add((node, FP.sourceRecordJSON, Literal(_json(record["source_record"]))))
        g.add((node, FP.deferredReason, Literal(record["reason"])))
    return g


def main():
    parser = argparse.ArgumentParser(description="Convert normalized FPUE candidates to RDF; no overwrite")
    parser.add_argument("--input", required=True, type=Path); parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--vocabulary", type=Path)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("output exists; choose a new path")
    vocab = Graph().parse(args.vocabulary, format="turtle") if args.vocabulary else None
    graph = convert_normalized(json.loads(args.input.read_text(encoding="utf-8")), vocabulary=vocab)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as stream:
        stream.write(graph.serialize(format="turtle"))
    print(f"Created {args.output}: {len(graph)} RDF triples")


if __name__ == "__main__":
    main()
