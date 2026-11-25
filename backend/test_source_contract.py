"""Step 0 source/frame contract checks, not architectural GT evaluation."""
import copy
import unittest
from backend.source_contract import (validate_binding, source_fingerprint,
    assert_same_source, describe_bundle, request_frame)
from backend.test_reference_restoration import fixture


class SourceContracts(unittest.TestCase):
    def binding(self):
        return fixture()[2]

    def test_binding_rejects_role_page_zoom_size_and_hash_replacements(self):
        original = self.binding()
        for key, value in [('source_role', 'Target'), ('page', 2), ('extraction_zoom', 2),
                           ('width', 301), ('image_sha256', '0'*64), ('source_sha256', '1'*64)]:
            changed = {**original, key: value}
            with self.assertRaises(ValueError):
                assert_same_source(original, changed)

    def test_boolean_and_nonfinite_source_dimensions_are_invalid(self):
        for key, value in [('page', True), ('width', False), ('extraction_zoom', float('nan'))]:
            with self.assertRaises(ValueError):
                validate_binding({**self.binding(), key: value})

    def test_integer_and_float_zoom_and_scopes_have_stable_fingerprints(self):
        binding = {**self.binding(), 'core_bbox_native': [10, 10, 100, 100], 'context_bbox_native': [0, 0, 300, 300]}
        changed = {**binding, 'extraction_zoom': 1.0, 'core_bbox_native': [10., 10., 100., 100.]}
        self.assertEqual(source_fingerprint(binding), source_fingerprint(changed))

    def test_frame_alias_is_explicit_and_never_allows_a_role_change(self):
        original = self.binding()
        changed = {**original, 'frame_id': 'Ref:old-same-image'}
        with self.assertRaises(ValueError):
            assert_same_source(original, changed)
        proof = assert_same_source(original, changed, allow_verified_frame_alias=True)
        self.assertTrue(proof['frame_alias'])
        self.assertFalse(proof['architectural_identity_or_truth_verified'])
        with self.assertRaises(ValueError):
            assert_same_source(original, {**changed, 'source_role': 'Target'}, allow_verified_frame_alias=True)

    def test_same_image_does_not_make_different_analysis_scopes_equivalent(self):
        original = {**self.binding(), 'core_bbox_native': [10, 10, 100, 100], 'context_bbox_native': [0, 0, 300, 300]}
        other = {**original, 'core_bbox_native': [20, 20, 100, 100]}
        with self.assertRaisesRegex(ValueError, 'scope differs'):
            assert_same_source(original, other)
        self.assertTrue(assert_same_source(original, other, include_scope=False)['same_native_source'])

    def test_outside_core_and_mismatched_context_are_invalid(self):
        for scope in [{'core_bbox_native': [0, 0, 301, 30]},
                      {'core_bbox_native': [0, 0, 100, 100], 'context_bbox_native': [20, 20, 200, 200]}]:
            with self.assertRaises(ValueError):
                validate_binding({**self.binding(), **scope})

    def test_descriptor_preserves_contract_identity_and_rejects_mixed_records(self):
        binding = self.binding()
        bundle = {'schema_version': 'reference_understanding_bundle/1', 'source_binding': binding,
                  'source_role': 'Ref', 'cases': [{'id': 'case-1'}]}
        descriptor = describe_bundle(bundle)
        self.assertEqual(descriptor['groups']['cases']['count'], 1)
        self.assertFalse(descriptor['architectural_truth_verified_by_this_check'])
        bundle['cases'][0]['source_role'] = 'Target'
        with self.assertRaises(ValueError):
            describe_bundle(bundle)

    def test_unknown_contract_or_duplicate_records_are_rejected(self):
        with self.assertRaises(ValueError):
            describe_bundle({'schema_version': 'unknown'})
        bundle = {'schema_version': 'fpue_region_hypotheses/0.4', 'source_binding': self.binding(),
                  'entities': [{'id': 'same'}, {'id': 'same'}]}
        with self.assertRaises(ValueError):
            describe_bundle(bundle)

    def test_request_frame_uses_the_selected_role_and_original_page(self):
        image = fixture()[1]
        ref, target = request_frame(image, 'Ref'), request_frame(image, 'Target')
        self.assertNotEqual(ref['frame_id'], target['frame_id'])
        self.assertEqual(ref['image_id'], target['image_id'])
        self.assertEqual(ref['width'], image.width)


if __name__ == '__main__':
    unittest.main()
