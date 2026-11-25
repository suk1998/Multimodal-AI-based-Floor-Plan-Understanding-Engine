"""Availability checks using actual user GT images; never accuracy evaluation."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from backend.gt_image_catalog import catalog_gt_images

ROOT=Path(__file__).resolve().parents[1]


class GTImageCatalogTests(unittest.TestCase):
    def test_actual_images_keep_their_own_frame_and_source_hash(self):
        before={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in (ROOT/'GT').glob('*.png')}
        catalog=catalog_gt_images(ROOT/'GT')
        self.assertEqual({r['variant'] for r in catalog['images']},{'84A','84B','84C'})
        for record in catalog['images']:
            path=Path(record['path'])
            self.assertEqual(record['sha256'],before[path.name])
            self.assertTrue(record['image_frame_id'].startswith('gt_image:'))
            self.assertIsNone(record['image_to_native_transform'])
            self.assertIsNone(record['source_pdf_binding'])
        self.assertEqual(before,{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in (ROOT/'GT').glob('*.png')})

    def test_images_are_available_without_invented_connection_metrics(self):
        with tempfile.TemporaryDirectory() as temp:
            out=Path(temp)/'availability.json'
            result=subprocess.run([sys.executable,'-B',str(ROOT/'scripts/evaluate_spatial_grounding.py'),'--image-gt-dir',str(ROOT/'GT'),'--output',str(out)],cwd=ROOT,capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            reports=json.loads(out.read_text())['datasets']
            self.assertEqual(len(reports),3)
            self.assertTrue(all(r['status']=='IMAGE_GT_AVAILABLE_SPATIAL_GT_PENDING' and r['metrics'] is None for r in reports))

    def test_existing_evaluation_output_is_preserved(self):
        with tempfile.TemporaryDirectory() as temp:
            out=Path(temp)/'original.json';out.write_text('PRESERVE')
            result=subprocess.run([sys.executable,'-B',str(ROOT/'scripts/evaluate_spatial_grounding.py'),'--image-gt-dir',str(ROOT/'GT'),'--output',str(out)],cwd=ROOT,capture_output=True)
            self.assertNotEqual(result.returncode,0);self.assertEqual(out.read_text(),'PRESERVE')

    def test_missing_or_empty_image_directory_is_explicit(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(ValueError):catalog_gt_images(Path(temp)/'missing')
            with self.assertRaises(ValueError):catalog_gt_images(Path(temp))


if __name__=='__main__':unittest.main()
