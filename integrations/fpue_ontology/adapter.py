"""Read-only adapters for existing FPUE PoC bundles, not a detector or reasoner.

Supported contracts are explicit. Unknown versions fail instead of being coerced.
The web runtime's architectural_spatial_bundle/1 is a different contract and is
not silently treated as the older understanding bundles.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
from collections import Counter
from pathlib import Path

from rdflib import Graph
from rdflib.namespace import RDF

if __package__:
    from .bridge import ELEMENT_CLASSES, OBSERVATION_CLASSES, REVIEW, FP, convert_normalized, validate_relation
else:
    from bridge import ELEMENT_CLASSES, OBSERVATION_CLASSES, REVIEW, FP, convert_normalized, validate_relation

SUPPORTED_SCHEMAS = {"space_grounding/1", "geometry_refinement/2", "architectural_spatial_bundle/1"}
ROLE_NAMES = {"Ref": "REF", "Target": "TARGET"}
RELATIONS = {"labels": "LabelAssociation", "boundedBy": "BoundaryAssociation", "hosts": "HostingRelation", "adjacentTo": "Adjacency", "connects": "Connection", "connectedTo": "Connection", "refersTo": "ReferenceAssociation", "contains": "Containment"}
BINDING_KEYS = ("source_sha256", "image_sha256", "page", "extraction_zoom", "width", "height", "frame_id")


def _reject_nonfinite(value):
    raise ValueError(f"non-finite JSON number {value}")


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"), parse_constant=_reject_nonfinite)


def artifact(path):
    path = Path(path)
    return {"path": str(path.resolve()), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def _index(records, description):
    if not isinstance(records, list):
        raise ValueError(f"{description} must be a list")
    result = {}
    for record in records:
        if not isinstance(record, dict) or not isinstance(record.get("id"), str) or not record["id"].strip():
            raise ValueError(f"{description}: missing record id")
        if record["id"] in result:
            raise ValueError(f"{description}: duplicate id {record['id']}")
        result[record["id"]] = record
    return result


def inspect_directory(directory):
    """Require and cross-check the handoff's four actual files before mapping."""
    directory = Path(directory)
    names = ("understanding_bundle", "space_candidates", "element_candidates", "relation_candidates")
    paths = {name: directory / (name + ".json") for name in names}
    missing = [str(path) for path in paths.values() if not path.is_file()]
    if missing:
        raise ValueError("required FPUE files missing: " + ", ".join(missing))
    files = {name: read_json(path) for name, path in paths.items()}
    bundle = files["understanding_bundle"]
    entities, relations = bundle["entities"], bundle["relations"]
    for name in names[1:]:
        data = files[name]
        if data.get("schema_version") != bundle.get("schema_version") or data.get("source_bindings") != bundle.get("source_bindings") or data.get("coordinate_system") != bundle.get("coordinate_system"):
            raise ValueError(f"{name}: source/version/frame contract differs from bundle")
        if set(data["drawings"]) != set(bundle["source_bindings"]):
            raise ValueError(f"{name}: drawings roles differ from bundle")
        for role, drawing in data["drawings"].items():
            actual = drawing["candidates"] if name == "relation_candidates" else drawing
            expected = [r for r in (relations if name == "relation_candidates" else entities) if r["role"] == role and (name != "space_candidates" or r["type"] == "Space") and (name != "element_candidates" or r["type"] != "Space")]
            if _index(actual, name) != _index(expected, "bundle " + name):
                raise ValueError(f"{name}/{role}: candidate records differ from bundle")
    context = {
        "artifacts": [artifact(path) for path in paths.values()],
        "relation_attempts": {role: copy.deepcopy(drawing.get("attempts", [])) for role, drawing in files["relation_candidates"]["drawings"].items()},
        "four_file_consistency": "MATCHED",
    }
    evidence = directory / "geometry_observations.json"
    if evidence.is_file():
        context["artifacts"].append(artifact(evidence))
    return bundle, context


def _validate_binding(binding, role):
    if not isinstance(binding, dict) or any(key not in binding for key in BINDING_KEYS):
        raise ValueError(f"{role}: incomplete source binding")
    for key in ("source_sha256", "image_sha256"):
        value = binding[key]
        if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
            raise ValueError(f"{role}: invalid {key}")
    if type(binding["page"]) is not int or binding["page"] < 1:
        raise ValueError("source page must be a positive 1-based integer")
    zoom = binding["extraction_zoom"]
    if isinstance(zoom, bool) or not isinstance(zoom, (int, float)) or not math.isfinite(zoom) or zoom <= 0:
        raise ValueError("extraction_zoom must be positive")
    if binding.get("role", role) != role or binding.get("coordinate_system", "image_native_pixels") != "image_native_pixels":
        raise ValueError("source binding role or native coordinate system mismatch")
    for key in ("width", "height"):
        if type(binding[key]) is not int or binding[key] <= 0:
            raise ValueError(f"invalid source {key}")
    if not isinstance(binding["frame_id"], str) or not binding["frame_id"].strip():
        raise ValueError("source frame_id missing")


def _check_record_frame(record, binding, role):
    if record.get("role") != role or record.get("frame_id") != binding["frame_id"]:
        raise ValueError(f"{record.get('id')}: record role/frame mismatch")
    if "source_binding" in record:
        candidate = record["source_binding"]
        if any(candidate.get(k) != binding[k] for k in BINDING_KEYS):
            raise ValueError(f"{record['id']}: source binding mismatch")
        if candidate.get("role", role) != role:
            raise ValueError(f"{record['id']}: source role mismatch")


def _common(record, role, frame_id, warnings):
    status = record.get("claim_status", record.get("verification_status", record.get("status", "UNKNOWN")))
    if status not in REVIEW:
        warnings.append({"id": record["id"], "reason": "unrecognized status retained in source_record; RDF status UNKNOWN", "source_status": status})
        mapped_status = "UNKNOWN"
    else:
        mapped_status = status
    if "verification_status" in record and record["verification_status"] != status:
        warnings.append({"id": record["id"], "reason": "status fields disagree; preserved without upgrading", "status": status, "verification_status": record["verification_status"]})
    result = {"id": record["id"], "status": mapped_status, "source_status": status, "source_role": ROLE_NAMES[role], "frame_id": frame_id, "annotation_source": record.get("annotation_source", "FPUE_PROPOSAL"), "source_record": copy.deepcopy(record)}
    result["evidence_ids"] = copy.deepcopy(record.get("geometry_evidence_ids", record.get("evidence_ids", [])))
    if record.get("confidence") is not None:
        result["confidence"] = record["confidence"]
    return result


def normalize_bundle(bundle, role, *, context=None, catalog_scope=None):
    """Map one drawing independently; return normalized contract and audit report."""
    if isinstance(bundle, dict) and bundle.get('schema_version') == 'architectural_spatial_bundle/1':
        if __package__:
            from .spatial_adapter import normalize_spatial_bundle
        else:
            from spatial_adapter import normalize_spatial_bundle
        return normalize_spatial_bundle(bundle, role, context=context, catalog_scope=catalog_scope)
    if not isinstance(bundle, dict) or bundle.get("schema_version") not in SUPPORTED_SCHEMAS:
        raise ValueError(f"unsupported FPUE schema {bundle.get('schema_version') if isinstance(bundle, dict) else type(bundle).__name__!r}; supported: {sorted(SUPPORTED_SCHEMAS)}")
    if role not in ROLE_NAMES:
        raise ValueError("role must be Ref or Target")
    if bundle.get("coordinate_system") != "image_native_pixels":
        raise ValueError("bundle must declare image_native_pixels; no PDF/native guessing")
    binding = bundle.get("source_bindings", {}).get(role)
    _validate_binding(binding, role)
    if not isinstance(bundle.get("entities"), list) or not isinstance(bundle.get("relations"), list):
        raise ValueError("bundle entities and relations must be lists")
    if any(not isinstance(r, dict) or r.get("role") not in ROLE_NAMES for r in bundle["entities"] + bundle["relations"]):
        raise ValueError("record with missing or unsupported role")
    entities = [e for e in bundle["entities"] if e["role"] == role]
    relations = [r for r in bundle["relations"] if r["role"] == role]
    entity_index, relation_index = _index(entities, "entities"), _index(relations, "relations")
    if set(entity_index) & set(relation_index):
        raise ValueError("duplicate id across entities and relations")
    context = context or {}
    frame_id = binding["frame_id"]
    warnings = []
    metadata = {
        "adapter_version": "fpue_ontology_adapter/1", "source_schema": bundle["schema_version"],
        "source_artifacts": copy.deepcopy(context.get("artifacts", [])),
        "relation_attempts": copy.deepcopy(context.get("relation_attempts", {}).get(role, [])),
        "unresolved_issues": [copy.deepcopy(x) for x in bundle.get("unresolved_issues", []) if x.get("role") == role],
        "four_file_consistency": context.get("four_file_consistency", "NOT_CHECKED_BUNDLE_ONLY"),
        "not_performed": ["detection", "inference", "matching", "alignment", "GT validation", "change detection"],
    }
    data = {"schema_version": "fpue_ontology_normalized/1", "document": {
        "id": f"pdf:{binding['source_sha256']}:page:{binding['page']}", "role": ROLE_NAMES[role],
        "catalog_scope": catalog_scope or "unresolved-catalog:" + binding["source_sha256"],
        "source_binding": copy.deepcopy(binding), "frame_registry": copy.deepcopy(bundle.get("frame_registry", {}).get(role)),
        "coordinate_frame": {"id": frame_id, "kind": "image_native_pixels", "width": binding["width"], "height": binding["height"], "axis_definition": "origin=top-left;+x=right;+y=down;geographic-north=unknown"},
        "metadata": metadata,
    }, "spaces": [], "elements": [], "observations": [], "relations": [], "claims": [], "deferred_records": []}
    field_inventory = {}
    for record in entities:
        _check_record_frame(record, binding, role)
        mapped = _common(record, role, frame_id, warnings)
        geometry = record.get("geometry")
        if geometry is not None:
            if not isinstance(geometry, dict):
                raise ValueError(f"{record['id']}: geometry must be a dict or null")
            mapped["geometry_native"] = copy.deepcopy(geometry)
            # Only the explicitly stored extent is exposed as bbox. Never use the
            # OCR name box, seed, analysis extent or a generated rectangle here.
            mapped["bbox"] = copy.deepcopy(geometry.get("bbox_xyxy"))
        typ = record.get("type")
        if typ == "Space":
            names = record.get("name_hypotheses", [])
            mapped["name_hypotheses"] = copy.deepcopy(names)
            if len(names) == 1 and isinstance(names[0].get("text"), str) and names[0]["text"].strip():
                mapped["raw_label"] = names[0]["text"]
            elif len(names) > 1:
                warnings.append({"id": record["id"], "reason": "multiple name hypotheses retained; no single label selected"})
            mapped["label_state"] = "UNKNOWN"
            data["spaces"].append(mapped)
        elif typ in ELEMENT_CLASSES:
            mapped["category"] = typ
            if record.get("type_code") is not None:
                mapped["type_code"] = record["type_code"]
            data["elements"].append(mapped)
        else:
            mapped["category"] = typ if typ in OBSERVATION_CLASSES else "UnclassifiedObservation"
            if typ == "TextRegion":
                mapped["raw_text"] = record.get("text")
                mapped["recognition_confidence"] = record.get("recognition_score")
            if typ not in OBSERVATION_CLASSES:
                warnings.append({"id": record["id"], "reason": "unsupported entity type retained as unclassified observation", "source_type": typ})
            data["observations"].append(mapped)
        field_inventory.setdefault(str(typ), set()).update(record)
    spaces, elements, observations = (_index(data[key], key) for key in ("spaces", "elements", "observations"))
    semantic_signatures = Counter()
    for record in relations:
        _check_record_frame(record, binding, role)
        mapped = _common(record, role, frame_id, warnings)
        objects = record.get("object_ids")
        reason = None
        if not isinstance(objects, list) or len(objects) != 1:
            reason = "non-binary relation requires an explicit contract; endpoints not guessed or split"
        elif record.get("predicate") not in RELATIONS:
            reason = "unsupported source relation predicate"
        else:
            mapped.update(kind=RELATIONS[record["predicate"]], source_id=record.get("subject_id"), target_id=objects[0])
            for key in ("via_element_id", "mode", "opening_side"):
                if key in record:
                    mapped[key] = copy.deepcopy(record[key])
            try:
                if mapped["kind"] == "Connection":
                    via = record.get("via_element_id")
                    if not mapped["evidence_ids"] or via not in elements or elements[via]["category"] not in {"Door", "FireDoor", "Opening"}:
                        raise ValueError("Connection requires explicit two-space and observed Door/Opening evidence; no adjacency conversion")
                validate_relation(mapped, spaces, elements, observations)
            except ValueError as error:
                reason = str(error)
        if reason:
            data["deferred_records"].append({"id": record["id"], "reason": reason, "source_record": copy.deepcopy(record)})
        else:
            data["relations"].append(mapped)
            semantic_signatures[(mapped["kind"], mapped["source_id"], mapped["target_id"]) ] += 1
    subjects = {r["id"] for k in ("spaces", "elements", "observations", "relations") for r in data[k]}
    claims = []
    for e in entities + relations:
        claims.extend(e.get("claims", []))
    # The combined bundle repeats entity claims in a top-level array. Check them
    # instead of counting them twice. A top-level claim cannot borrow another role.
    role_subjects = set(entity_index) | set(relation_index)
    for claim in bundle.get("claims", []):
        if claim.get("subject") in role_subjects:
            claims.append(claim)
    unique_claims = {}
    for claim in claims:
        cid = claim.get("id")
        if not isinstance(cid, str) or not cid.strip():
            raise ValueError("claim id missing")
        if cid in unique_claims and unique_claims[cid] != claim:
            raise ValueError(f"conflicting duplicate claim {cid}")
        unique_claims[cid] = claim
    for cid, claim in unique_claims.items():
        if cid in role_subjects:
            raise ValueError(f"claim ID collides with entity/relation {cid}")
        if claim.get("subject") not in subjects:
            data["deferred_records"].append({"id": cid, "reason": "claim subject absent or deferred; not reattached to another object", "source_record": copy.deepcopy(claim)})
        else:
            mapped = _common(claim, role, frame_id, warnings)
            mapped.update({key: copy.deepcopy(claim[key]) for key in ("subject", "predicate", "value", "scope", "criteria_version") if key in claim})
            data["claims"].append(mapped)
    interpreted_fields = {"id", "type", "role", "frame_id", "source_binding", "geometry", "claim_status", "verification_status", "status", "geometry_evidence_ids", "evidence_ids", "claims", "name_hypotheses", "text", "recognition_score", "confidence", "type_code", "annotation_source"}
    report = {
        "adapter_version": "fpue_ontology_adapter/1", "source_role": role, "frame_id": frame_id,
        "source_binding": copy.deepcopy(binding), "source_files": metadata["source_artifacts"],
        "four_file_consistency": metadata["four_file_consistency"],
        "counts": {"source_entities": len(entities), "source_entity_types": dict(Counter(e["type"] for e in entities)), "source_relations": len(relations), "source_relation_kinds": dict(Counter(r["predicate"] for r in relations)), "normalized_spaces": len(data["spaces"]), "normalized_elements": len(data["elements"]), "normalized_observations": len(data["observations"]), "normalized_relations": len(data["relations"]), "normalized_claims": len(data["claims"]), "deferred_records": len(data["deferred_records"]), "normalized_relation_kinds": dict(Counter(r["kind"] for r in data["relations"]))},
        "source_record_fields_retained_only_as_JSON": {t: sorted(fields - interpreted_fields) for t, fields in field_inventory.items()},
        "bundle_fields_available_in_original_artifact_only": sorted(set(bundle) - {"schema_version", "entities", "relations", "claims", "source_bindings", "frame_registry", "coordinate_system", "unresolved_issues"}),
        "entity_statuses": dict(Counter(r["status"] for k in ("spaces", "elements", "observations") for r in data[k])),
        "relation_statuses": dict(Counter(r["status"] for r in data["relations"])),
        "deferred": [{"id": r["id"], "reason": r["reason"]} for r in data["deferred_records"]],
        "warnings": warnings,
        "duplicate_semantic_relations_retained_by_distinct_source_ID": sum(n - 1 for n in semantic_signatures.values()),
        "geometry": {"coordinate_system": "image_native_pixels", "native_coordinates_transformed": False, "bbox_semantics": "source extent only; never a space boundary", "full_geometry_and_source_record_retained": True, "pdf_transform_available": data["document"]["frame_registry"] is not None},
        "catalog_scope": data["document"]["catalog_scope"], "catalog_scope_verified": False,
        "verified_by_adapter": False, "GT_metrics": None,
    }
    return data, report


def write_conversion(bundle, output, *, context=None, roles=("Ref", "Target"), catalog_scope=None, vocabulary=None):
    """Validate all roles before creating a fresh output directory. Never overwrite."""
    output = Path(output)
    if output.exists():
        raise FileExistsError(f"output exists: {output}; choose a new directory")
    prepared = []
    for role in roles:
        normalized, report = normalize_bundle(bundle, role, context=context, catalog_scope=catalog_scope)
        graph = convert_normalized(normalized, vocabulary=vocabulary)
        report["RDF"] = {"triples": len(graph), "candidate_records": len(set(graph.subjects(RDF.type, FP.CandidateRecord))), "scoped_claims": len(set(graph.subjects(RDF.type, FP.Claim))), "deferred_records": len(set(graph.subjects(RDF.type, FP.DeferredRecord))), "connections": len(set(graph.subjects(RDF.type, FP.Connection))), "change_events": len(set(graph.subjects(RDF.type, FP.ChangeEvent)))}
        serialized = graph.serialize(format="turtle")
        reparsed = Graph().parse(data=serialized, format="turtle")
        from rdflib.compare import isomorphic
        if not isomorphic(graph, reparsed):
            raise ValueError("RDF Turtle roundtrip failed")
        report["Turtle_roundtrip_isomorphic"] = True
        prepared.append((role, normalized, report, serialized))
    output.mkdir(parents=True, exist_ok=False)
    for role, normalized, report, serialized in prepared:
        directory = output / role
        directory.mkdir()
        for filename, value in (("normalized.json", normalized), ("mapping_report.json", report)):
            with (directory / filename).open("x", encoding="utf-8") as stream:
                json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
                stream.write("\n")
        with (directory / "ontology.ttl").open("x", encoding="utf-8") as stream:
            stream.write(serialized)
    return [report for _, _, report, _ in prepared]


def main():
    parser = argparse.ArgumentParser(description="Read-only FPUE ontology mapping; fresh output directory required")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--input-dir", type=Path, help="four mutually consistent Page 4 candidate files")
    source.add_argument("--bundle", type=Path, help="explicit standalone supported understanding_bundle.json")
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--role", choices=["Ref", "Target", "both"], default="both")
    parser.add_argument("--catalog-scope", help="only supply a common catalog when actual provenance establishes it")
    parser.add_argument("--vocabulary", type=Path)
    args = parser.parse_args()
    try:
        if args.input_dir:
            bundle, context = inspect_directory(args.input_dir)
        else:
            bundle, context = read_json(args.bundle), {"artifacts": [artifact(args.bundle)]}
        vocab = Graph().parse(args.vocabulary, format="turtle") if args.vocabulary else None
        reports = write_conversion(bundle, args.output_dir, context=context, roles=("Ref", "Target") if args.role == "both" else (args.role,), catalog_scope=args.catalog_scope, vocabulary=vocab)
    except (OSError, ValueError, KeyError, TypeError) as error:
        parser.exit(1, f"Mapping failed: {error}\n")
    for report in reports:
        print(report["source_role"], json.dumps(report["counts"], ensure_ascii=False))
    print("Created", args.output_dir, "— candidate mapping only, no GT evaluation")


if __name__ == "__main__":
    main()
