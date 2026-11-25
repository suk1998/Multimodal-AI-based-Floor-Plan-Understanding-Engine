"""Diagnostics regression tests; these are synthetic checks, not real-data GT."""
import argparse
import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import detection_diagnostics as d


class DiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.legacy, self.constants = d.load_legacy()

    def test_roi_rejects_missing_frame_wrong_size_and_crossing(self):
        with self.assertRaises(ValueError):
            d.validate_polygon([[1,1],[8,1],[8,8]],(10,10),None)
        with self.assertRaises(ValueError):
            d.validate_polygon([[1,1],[8,8],[1,8],[8,1]],(10,10),"target_native_pixels")
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/"roi.json"
            path.write_text(json.dumps({"image_size":[20,20],"coordinate_system":"normalized",
                                       "polygon":[[0,0],[1,0],[1,1]]}))
            with self.assertRaises(ValueError):
                d.load_region(path,(10,10))

    def test_roi_is_required_at_cli_before_any_work(self):
        completed=subprocess.run([sys.executable,"-B",str(d.ROOT/"detection_diagnostics.py"),
                                  "--output","/tmp/unused-diagnostics-test"],capture_output=True,text=True)
        self.assertEqual(completed.returncode,2)
        self.assertIn("--roi",completed.stderr)

    def test_normalized_polygon_and_outside_pixels(self):
        points,mask=d.validate_polygon([[.2,.2],[.8,.2],[.8,.8]],(101,101),"normalized")
        self.assertEqual(points,[[20.,20.],[80.,20.],[80.,80.]])
        self.assertFalse(mask[0,0])
        self.assertTrue(mask[30,60])

    def test_exact_euclidean_thresholds_and_no_outside_counterpart(self):
        source=np.zeros((20,20),bool); counter=source.copy(); scope=np.ones_like(source)
        counter[10,5]=True
        for x in [6,7,8,10,11]:
            source[10,x]=True
        rows,bins,_=d.distance_distribution(source,counter,scope,"test","native_target")
        self.assertEqual([r["beyond_tolerance_pixels"] for r in rows],[5,4,3,2,1])
        self.assertEqual(sum(r["pixels"] for r in bins),5)
        scope[:,5]=False
        rows,_,_=d.distance_distribution(source,counter,scope,"test","native_target")
        self.assertEqual(rows[-1]["no_counterpart_pixels"],5)

    def test_morphology_records_loss_and_invented_support_separately(self):
        raw=np.zeros((80,100),bool)
        raw[10,10:70]=True
        raw[30:40,10:30]=True;raw[30:40,32:50]=True
        stages=d.morphology_stages(raw,np.ones_like(raw),self.legacy)
        self.assertFalse(stages["opened"][10].any())
        self.assertTrue(np.any(stages["closed"] & ~raw))
        self.assertTrue(np.any(raw & ~stages["closed"]))

    def test_filter_reasons_match_original_and_trace_thin_candidate(self):
        mask=np.zeros((160,280),bool)
        mask[10:13,10:210]=True
        mask[50:60,10:80]=True
        rows,accepted,_=d.component_audit(mask,np.ones_like(mask),self.constants,"test","closed")
        rejected=next(r for r in rows if not r["accepted"])
        self.assertEqual(rejected["first_rejection_reason"],"elongated_thin_component")
        self.assertIn("bbox_dimension_below_6px",rejected["all_rejection_reasons"])
        _,boxes,_=d.merge_audit(accepted,self.legacy,mask.shape,mask,"test")
        self.assertEqual(boxes,self.legacy["filter_component_boxes"](mask.astype(np.uint8)*255))

    def test_merge_uses_union_area_and_exact_component_provenance(self):
        accepted=[{"component_id":1,"bbox_ref_xywh":[10,10,10,10],"bbox_area_px":100},
                  {"component_id":2,"bbox_ref_xywh":[25,10,10,10],"bbox_area_px":100}]
        rows,boxes,summary=d.merge_audit(accepted,self.legacy,(60,60),np.zeros((60,60),bool),"test")
        self.assertEqual(boxes,[(10,10,25,10)])
        self.assertEqual(rows[0]["source_component_ids"],[1,2])
        self.assertEqual(rows[0]["newly_enclosed_area_px"],50)
        self.assertEqual(summary["newly_enclosed_union_area_px"],50)
        # Identical bbox values must not erase a distinct source component ID.
        accepted[1]["bbox_ref_xywh"]=[10,10,10,10]
        rows,_,_=d.merge_audit(accepted,self.legacy,(60,60),np.zeros((60,60),bool),"test")
        self.assertEqual(rows[0]["source_component_ids"],[1,2])
        self.assertEqual(rows[0]["source_bbox_union_area_px"],100)
        self.assertEqual(rows[0]["source_bbox_sum_area_px"],200)

    def test_empty_counterpart_and_empty_error_statistics_are_null(self):
        self.assertEqual(d.stats([np.inf])["count"],0)
        self.assertIsNone(d.stats([])["rmse_px"])
        self.assertTrue(np.isinf(d.distance_map(np.zeros((5,5),bool))).all())

    def test_registration_does_not_accept_legacy_identity_fallback(self):
        image=np.full((100,120,3),255,np.uint8)
        with self.assertRaisesRegex(ValueError,"fell back silently"):
            d.align(self.legacy,image,image,np.zeros((100,120),np.uint8),np.zeros((100,120),np.uint8))
        self.assertIs(self.legacy["cv2"],cv2)

    def test_overlay_and_transform_preserve_native_target_and_input(self):
        target=np.full((30,40,3),230,np.uint8); original=target.copy()
        scope=np.zeros((30,40),bool);scope[10:20,10:20]=True
        result=d.overlay(target,scope,[(np.ones(scope.shape,bool),(0,0,255))])
        np.testing.assert_array_equal(target,original)
        np.testing.assert_array_equal(result[~scope],original[~scope])
        mask=np.zeros((30,40),bool);mask[10,10]=True
        shifted=d.warp_mask(mask,np.array([[1.,0,3],[0,1,2],[0,0,1]]),(40,30))
        self.assertTrue(shifted[12,13])
        self.assertEqual(int(shifted.sum()),1)

    def test_external_wall_residual_is_independent_and_handles_missing_side(self):
        scope=np.ones((30,40),bool);a=np.zeros_like(scope);b=a.copy()
        a[5:20,10]=True;b[5:20,12]=True
        payload={"image_size":[40,30],"coordinate_system":"target_native_pixels",
                 "walls":[{"id":"wall","points":[[11,5],[11,19]],"half_width_px":4}]}
        rows,_=d.stable_wall_audit(payload,(40,30),scope,a,b,[])
        self.assertEqual(rows[0]["symmetric_nearest_ink_target_pixels"]["median_px"],2.)
        rows,_=d.stable_wall_audit(payload,(40,30),scope,a,np.zeros_like(a),[])
        self.assertFalse(rows[0]["has_two_sided_ink_support"])
        self.assertGreater(rows[0]["no_counterpart_pixels"],0)
        self.assertIsNone(rows[0]["symmetric_nearest_ink_target_pixels"]["rmse_px"])

    def test_identical_images_end_to_end_and_existing_output_is_not_overwritten(self):
        # Synthetic input is limited to this test; it never supplies the real
        # Page 4 ROI or masquerades as a real-data benchmark.
        image=np.full((360,480,3),255,np.uint8)
        for i in range(9):
            x=35+(i%3)*135; y=35+(i//3)*105
            cv2.rectangle(image,(x,y),(x+80+i,y+9+i),(0,0,0),-1)
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            ref=root/"ref.png"; target=root/"target.png"; roi=root/"roi.json"
            d.write_png(ref,image); d.write_png(target,image)
            roi.write_text(json.dumps({"image_size":[480,360],"coordinate_system":"target_native_pixels",
                                       "polygon":[[10,10],[460,10],[460,340],[10,340]]}))
            args=argparse.Namespace(ref=ref,target=target,roi=roi,alignment_search=None,stable_walls=None,
                                    output=root/"output",ocr_mode="disabled",tesseract=None,ocr_lang="kor+eng")
            with contextlib.redirect_stdout(io.StringIO()):
                manifest=d.run(args)
            self.assertTrue(manifest["input_files_unchanged"])
            self.assertIsNone(manifest["accuracy_metrics"])
            measurements=json.loads((args.output/"measurements.json").read_text())
            self.assertTrue(all(side["raw_pixels"]==0 for side in measurements["sides"].values()))
            rendered=d.read_image(args.output/"native_pixel_difference_overlay.png")
            np.testing.assert_array_equal(rendered,image)
            saved=d.digest(args.output/"run_manifest.json")
            with self.assertRaisesRegex(ValueError,"never overwritten"):
                d.run(args)
            self.assertEqual(d.digest(args.output/"run_manifest.json"),saved)


if __name__ == "__main__":
    unittest.main(verbosity=2)
