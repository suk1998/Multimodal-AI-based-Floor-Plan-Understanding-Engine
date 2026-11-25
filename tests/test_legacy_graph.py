import tempfile
import unittest
from pathlib import Path
import cv2
import numpy as np
from floorplan_change.legacy_config import Config
from floorplan_change.legacy_graph_pipeline import run_pipeline
from floorplan_change.graph import correspond
from floorplan_change.change import infer
from floorplan_change.report import render_final_result
from floorplan_change.ui.pdf_loader import pdf_page
from floorplan_change.evaluation import evaluate_predictions
from floorplan_change.thin_wall_detection import detect_thin_wall_regions


def canvas():
    return np.full((400, 500, 3), 255, np.uint8)


def line(image, a, b):
    cv2.line(image, a, b, (0,0,0), 3)


def chain(side, index, start, end):
    start, end = np.array(start, float), np.array(end, float)
    return {"id": f"{side}S{index:04}", "orientation": 0., "start": start.tolist(),
            "end": end.tolist(), "center": ((start+end)/2).tolist(),
            "length": float(np.linalg.norm(end-start)), "bbox": [*start, *end],
            "junction_ids": [], "opening_ids": [], "confidence": 1.}


def graph_with(chains):
    return {"chains": chains, "openings": [], "junctions": [], "communities": []}


def correspondence_with(groups=None, junction=None):
    groups = groups or []
    return {"chain": [], "chain_groups": groups, "opening": [],
            "junction": [junction] if junction else []}


class PipelineTests(unittest.TestCase):
    def test_thin_wall_detection_synthetic_cases(self):
        cfg = Config(registration=False)

        thin = canvas()
        cv2.rectangle(thin, (60, 80), (260, 87), (0, 0, 0), -1)
        thin_result = detect_thin_wall_regions(thin, cfg)
        self.assertTrue(thin_result["candidates"])
        diagnostic = thin_result["candidate_diagnostics"][0]
        self.assertTrue({"candidate_id", "elongation", "minor_axis_spread", "median_thickness",
                 "thickness_cv", "continuity", "connectivity", "rectangularity",
                 "final_score", "accepted", "rejection_reason"}.issubset(diagnostic))
        self.assertEqual(set(thin_result["feature_distributions"]),
                 {"thickness_cv", "continuity", "connectivity"})
        self.assertEqual(thin_result["stage_counts"], {
            "thickness_band": 1, "elongation": 1, "thickness_consistency": 1,
            "continuity": 1, "connectivity": 1, "final": 1})

        line_image = canvas()
        cv2.line(line_image, (60, 120), (260, 120), (0, 0, 0), 1)
        line_result = detect_thin_wall_regions(line_image, cfg)
        self.assertFalse(line_result["candidates"])
        self.assertGreater(np.count_nonzero(line_result["too_thin_mask"]), 0)

        dotted = canvas()
        for x in range(60, 260, 12):
            cv2.line(dotted, (x, 150), (x + 5, 150), (0, 0, 0), 1)
        self.assertFalse(detect_thin_wall_regions(dotted, cfg)["candidates"])

        thick = canvas()
        cv2.rectangle(thick, (60, 190), (260, 219), (0, 0, 0), -1)
        thick_result = detect_thin_wall_regions(thick, cfg)
        self.assertFalse(thick_result["candidates"])
        self.assertGreater(np.count_nonzero(thick_result["thick_wall_mask"]), 0)

    def test_thin_wall_connected_to_thick_region_and_gap_preserved(self):
        image = canvas()
        cv2.rectangle(image, (60, 250), (260, 279), (0, 0, 0), -1)
        cv2.rectangle(image, (150, 275), (157, 360), (0, 0, 0), -1)
        result = detect_thin_wall_regions(image, Config(registration=False))
        self.assertTrue(result["candidates"])
        self.assertGreater(max(candidate["wall_connectivity"] for candidate in result["candidates"]), 0)

        with_gap = canvas()
        cv2.rectangle(with_gap, (60, 60), (140, 67), (0, 0, 0), -1)
        cv2.rectangle(with_gap, (160, 60), (240, 67), (0, 0, 0), -1)
        result = detect_thin_wall_regions(with_gap, Config(registration=False))
        self.assertEqual(len(result["candidates"]), 2)

    def test_ground_truth_perfect_and_duplicate_matching(self):
        truth = [{"ground_truth_id": "GT001", "bbox": [10, 10, 50, 50],
                  "change_types": ["moved"]}]
        predictions = [{"change_id": "CR001", "bbox": [10, 10, 50, 50],
                       "change_types": ["moved"]},
                      {"change_id": "CR002", "bbox": [12, 12, 48, 48],
                       "change_types": ["moved"]}]
        metrics = evaluate_predictions(predictions, [], [], truth, .3)
        self.assertEqual(metrics["tp"], 1)
        self.assertEqual(metrics["duplicate_tp"], 1)
        self.assertEqual(metrics["fp"], 0)
        self.assertEqual(metrics["fn"], 0)

    def test_ground_truth_fp_fn_review_and_suppressed_provenance(self):
        truth = [{"ground_truth_id": "GT001", "bbox": [10, 10, 50, 50]},
             {"ground_truth_id": "GT002", "bbox": [100, 100, 140, 140]}]
        confirmed = [{"change_id": "CR001", "bbox": [10, 10, 50, 50], "change_types": ["added"]},
                     {"change_id": "CR002", "bbox": [200, 200, 20, 20], "change_types": ["moved"]}]
        review = [{"change_id": "RV001", "bbox": [100, 100, 140, 140], "change_types": ["moved"]}]
        metrics = evaluate_predictions(confirmed, review, [], truth, .3)
        self.assertEqual(metrics["tp"], 1)
        self.assertEqual(metrics["fp"], 1)
        self.assertEqual(metrics["fn"], 1)
        self.assertEqual(metrics["review_gt_ids"], [1])
        suppressed = [{"change_id": "SUP001", "bbox": [100, 100, 140, 140]}]
        metrics = evaluate_predictions(confirmed[:1], [], suppressed, truth, .3)
        self.assertEqual(metrics["suppressed_gt_ids"], [1])

    def test_v3_canonical_topology_ignores_raw_ids(self):
        ref_chains = [chain("R", 1, (50, 100), (150, 100)), chain("R", 2, (100, 50), (100, 150))]
        target_chains = [chain("T", 1, (50, 100), (150, 100)), chain("T", 2, (100, 50), (100, 150))]
        ref = {"chains": ref_chains, "openings": [], "junctions": [{"id": "RJ1", "center": [100, 100],
                "chain_ids": ["RS0001", "RS0002"], "degree": 2}], "communities": []}
        target = {"chains": target_chains, "openings": [], "junctions": [{"id": "TJ1", "center": [100, 100],
                "chain_ids": ["TS0001", "TS0002"], "degree": 2}], "communities": []}
        groups = [{"id": "CG0001", "canonical_structure_id": "CSID_0001", "type": "1:1",
                   "ref_ids": ["RS0001"], "target_ids": ["TS0001"], "confidence": .95},
                  {"id": "CG0002", "canonical_structure_id": "CSID_0002", "type": "1:1",
                   "ref_ids": ["RS0002"], "target_ids": ["TS0002"], "confidence": .95}]
        result, _ = infer(ref, target, {"chain": [], "chain_groups": groups, "opening": [],
            "junction": [{"ref_id": "RJ1", "target_id": "TJ1", "confidence": .95}]},
            np.zeros((220, 220), np.uint8), np.zeros((220, 220), np.uint8),
            Config(registration=False, min_change_confidence=0.0))
        self.assertFalse(any("topology_changed" in e["change_type"] for c in result for e in c["events"]))

    def test_v3_group_edit_and_uncertain_moved(self):
        ref_chain = chain("R", 1, (50, 100), (250, 100))
        target_parts = [chain("T", 1, (50, 100), (150, 100)), chain("T", 2, (150, 100), (280, 100))]
        group = {"id": "CG0001", "canonical_structure_id": "CSID_0001", "type": "1:N",
                 "ref_ids": ["RS0001"], "target_ids": ["TS0001", "TS0002"], "confidence": .9}
        result, _ = infer(graph_with([ref_chain]), graph_with(target_parts),
                          correspondence_with([group]), np.zeros((220, 320), np.uint8),
                          np.zeros((220, 320), np.uint8), Config(registration=False, min_change_confidence=0.0))
        self.assertTrue(any("modified" in e["change_type"] for c in result for e in c["events"]))
        moved = chain("T", 1, (250, 250), (450, 250))
        pair = {"ref_id": "RS0001", "target_id": "TS0001", "confidence": .65}
        result, _ = infer(graph_with([ref_chain]), graph_with([moved]),
                          {"chain": [pair], "chain_groups": [], "opening": [], "junction": []},
                          np.zeros((500, 500), np.uint8), np.zeros((500, 500), np.uint8),
                          Config(registration=False, min_change_confidence=0.0))
        self.assertTrue(any("correspondence_uncertain" in e["change_type"] and "moved" not in e["change_type"]
                            for c in result for e in c["events"]))
        self.assertTrue(all(c["review_state"] == "review" for c in result))

    def test_v3_same_provenance_events_form_one_region(self):
        ref_chains = [chain("R", 1, (50, 100), (150, 100)), chain("R", 2, (100, 50), (100, 150))]
        target_chains = [chain("T", 1, (50, 100), (150, 100)), chain("T", 2, (100, 50), (100, 150)),
                         chain("T", 3, (60, 112), (140, 112))]
        ref = {"chains": ref_chains, "openings": [], "junctions": [
            {"id": "RJ1", "center": [100, 100], "chain_ids": ["RS0001", "RS0002"], "degree": 2},
            {"id": "RJ2", "center": [100, 112], "chain_ids": ["RS0001", "RS0002"], "degree": 2}], "communities": []}
        target = {"chains": target_chains, "openings": [], "junctions": [
            {"id": "TJ1", "center": [100, 100], "chain_ids": ["TS0001", "TS0003"], "degree": 2},
            {"id": "TJ2", "center": [100, 112], "chain_ids": ["TS0001", "TS0003"], "degree": 2}], "communities": []}
        groups = [{"id": "CG0001", "canonical_structure_id": "CSID_0001", "type": "1:1",
                   "ref_ids": ["RS0001"], "target_ids": ["TS0001"], "confidence": .95},
                  {"id": "CG0002", "canonical_structure_id": "CSID_0002", "type": "1:1",
                   "ref_ids": ["RS0002"], "target_ids": ["TS0002"], "confidence": .95}]
        result, _ = infer(ref, target, {"chain": [], "chain_groups": groups, "opening": [],
            "junction": [{"ref_id": "RJ1", "target_id": "TJ1", "confidence": .95},
                         {"ref_id": "RJ2", "target_id": "TJ2", "confidence": .95}]},
            np.zeros((220, 220), np.uint8), np.zeros((220, 220), np.uint8),
            Config(registration=False, min_change_confidence=0.0))
        topology_regions = [region for region in result
                    if all(event["entity_kind"] == "junction" for event in region["events"])]
        self.assertEqual(len(topology_regions), 1)
        self.assertEqual(len(topology_regions[0]["events"]), 2)

    def test_v3_one_to_many_partial_correspondence(self):
        ref = graph_with([chain("R", 1, (70, 100), (350, 100))])
        target = graph_with([chain("T", 1, (70, 100), (210, 100)),
                             chain("T", 2, (210, 100), (350, 100))])
        result = correspond(ref, target, Config(registration=False))
        groups = [g for g in result["chain_groups"] if g["type"] == "1:N"]
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["ref_ids"], ["RS0001"])
        self.assertEqual(groups[0]["target_ids"], ["TS0001", "TS0002"])

    def test_v3_many_to_one_partial_correspondence(self):
        ref = graph_with([chain("R", 1, (70, 100), (210, 100)),
                          chain("R", 2, (210, 100), (350, 100))])
        target = graph_with([chain("T", 1, (70, 100), (350, 100))])
        result = correspond(ref, target, Config(registration=False))
        groups = [g for g in result["chain_groups"] if g["type"] == "N:1"]
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["ref_ids"], ["RS0001", "RS0002"])
        self.assertEqual(groups[0]["target_ids"], ["TS0001"])

    def test_identical_and_blank(self):
        for drawn in (False, True):
            im = canvas()
            if drawn:
                line(im, (50,100), (420,100))
                line(im, (80,100), (80,300))
            before = im.copy()
            r = run_pipeline(im, im)
            self.assertEqual(r["changes"], [])
            np.testing.assert_array_equal(im, before)

    def test_removed_on_target_only_and_export(self):
        ref, target = canvas(), canvas()
        line(ref, (70,100), (350,100))
        before = ref.copy()
        with tempfile.TemporaryDirectory() as d:
            r = run_pipeline(ref, target, d, Config(registration=False))
            self.assertTrue(r["changes"])
            self.assertTrue(all("removed" in c["change_type"] for c in r["changes"]))
            np.testing.assert_array_equal(ref, before)
            np.testing.assert_array_equal(r["ref_baseline"], before)
            self.assertFalse(np.array_equal(target, r["target_result"]))
            baseline = cv2.imdecode(np.fromfile(Path(d)/"visual/ref_baseline.png", np.uint8), 1)
            np.testing.assert_array_equal(baseline, before)
            header = (Path(d)/"report/change_report.csv").read_text(encoding="utf-8-sig").splitlines()[0]
            self.assertIn("변경_ID", header)
            self.assertNotIn("dx", header)

    def test_moved_chain(self):
        ref, target = canvas(), canvas()
        line(ref, (70,100), (350,100))
        line(target, (70,120), (350,120))
        r = run_pipeline(ref, target, config=Config(registration=False))
        self.assertTrue(any("moved" in c["change_type"] for c in r["changes"]))
        self.assertFalse(any("added" in c["change_type"] or "removed" in c["change_type"] for c in r["changes"]))

    def test_opening_moved(self):
        ref, target = canvas(), canvas()
        for im, lo, hi in ((ref, 180, 220), (target, 240, 280)):
            line(im, (50,150), (lo,150))
            line(im, (hi,150), (430,150))
        r = run_pipeline(ref, target, config=Config(registration=False))
        self.assertTrue(r["ref"]["openings"])
        self.assertTrue(any("opening_moved" in c["change_type"] for c in r["changes"]))

    def test_registration_translation(self):
        rng = np.random.default_rng(21)
        ref = canvas()
        for _ in range(60):
            x,y = rng.integers([30,30], [420,330])
            cv2.rectangle(ref, (int(x),int(y)), (int(x+15),int(y+12)), (0,0,0), 2)
        target = cv2.warpAffine(ref, np.float32([[1,0,12],[0,1,8]]), (500,400), borderValue=(255,255,255))
        r = run_pipeline(ref, target)
        matrix = np.array(r["alignment"]["matrix"])
        np.testing.assert_allclose(matrix[:2,2], [-12,-8], atol=2)

    def test_bad_registration_and_input(self):
        with self.assertRaises(ValueError):
            run_pipeline(canvas(), np.zeros_like(canvas()))
        with self.assertRaises(ValueError):
            Config(roi=(.8,0,.2,1))
        with self.assertRaises(ValueError):
            run_pipeline(canvas(), np.zeros((100,100,3), np.uint8), config=Config(registration=False))

    def test_pdf_page_selection(self):
        import fitz
        with fitz.open() as d:
            d.new_page(width=100,height=120)
            d.new_page(width=140,height=160)
            data = d.tobytes()
        self.assertEqual(pdf_page(data, 2, 1).shape, (160,140,3))
        with self.assertRaises(ValueError):
            pdf_page(data, 3)

    def test_stable_ids(self):
        ref, target = canvas(), canvas()
        line(target, (70,100), (350,100))
        a = run_pipeline(ref,target,config=Config(registration=False))
        b = run_pipeline(ref,target,config=Config(registration=False))
        self.assertEqual(a["changes"], b["changes"])


if __name__ == "__main__":
    unittest.main()
