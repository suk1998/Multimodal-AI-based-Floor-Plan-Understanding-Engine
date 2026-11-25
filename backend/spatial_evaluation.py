"""Scoped human-GT evaluation; never turns an example or a prediction into GT.

Use existing GT geometry/reviewer validation. Relations are evaluated only with
complete human scope, confirmed native objects and explicit one-to-one matches.
No automatic graph matching and no invented metrics when GT is unavailable.
"""
import math
from collections import Counter
import numpy as np
from poc.gt_evaluation import human_stamp, validate_geometry, point_inside

BINDING_KEYS = ('source_sha256', 'image_sha256', 'page', 'extraction_zoom', 'width', 'height', 'frame_id')


def evaluate_connections(bundle, gt_package, relation_gt, *, split='DEVELOPMENT', thresholds_tuned=False):
    pending = {'status': 'PENDING_GT_REVIEW', 'metrics': None, 'reason': 'confirmed spatial relation GT and complete review scope required'}
    if not gt_package or not relation_gt:
        return pending
    if gt_package.get('schema_version') != 'fpue_gt_review/1':
        raise ValueError('existing fpue_gt_review/1 package required')
    if bundle.get('schema_version') != 'architectural_spatial_bundle/1':
        raise ValueError('current Spatial bundle required')
    if split not in {'DEVELOPMENT', 'HOLDOUT'} or type(thresholds_tuned) is not bool or split == 'HOLDOUT' and thresholds_tuned:
        raise ValueError('invalid split or threshold tuning on HOLDOUT')
    role = bundle['source_role']; binding = bundle['source_binding']
    gt_binding = gt_package.get('bindings', {}).get(role, {})
    if any(gt_binding.get(k) != binding.get(k) for k in BINDING_KEYS):
        raise ValueError('GT source PDF/page/native Frame mismatch; no automatic frame relabeling')
    if relation_gt.get('schema_version') != 'fpue_spatial_relation_gt/1' or relation_gt.get('source_role') != role or relation_gt.get('frame_id') != binding['frame_id']:
        raise ValueError('relation GT role/frame contract mismatch')
    if relation_gt.get('source_image_sha256') != binding['image_sha256']:
        raise ValueError('relation GT image mismatch')
    if relation_gt.get('review_status') != 'CONFIRMED' or relation_gt.get('inventory_complete') is not True or relation_gt.get('missing_relations_reviewed') is not True:
        return pending
    human_stamp(relation_gt)
    scope = relation_gt.get('scope_native')
    validate_geometry(scope, 'ReviewScope', binding)
    inventory = {}
    for g in gt_package.get('gt_annotations', []):
        if g.get('role') != role: continue
        if g['gt_id'] in inventory: raise ValueError('duplicate GT object ID')
        if g.get('review_status') != 'CONFIRMED': continue
        if g.get('frame_id') != binding['frame_id'] or g.get('source_image_sha256') != binding['image_sha256']:
            raise ValueError('GT object source mismatch')
        if g.get('geometry_origin') not in {'HUMAN_DRAWN', 'HUMAN_NATIVE_COORDINATE_EDIT'}:
            raise ValueError('prediction copied as confirmed GT')
        allowed = {'SpaceBoundary': {'SpaceBoundary'}, 'Wall': {'WallSegment', 'WallBoundary'}, 'Door': {'DoorPoint', 'DoorThreshold'}, 'Opening': {'OpeningSegment'}, 'Window': {'WindowSegment'}}
        if g.get('geometry_type') not in allowed.get(g.get('element_type'), set()):
            raise ValueError('GT element/geometry type mismatch')
        human_stamp(g); validate_geometry(g.get('geometry_native'), g['geometry_type'], binding)
        inventory[g['gt_id']] = g
    if not inventory:
        return pending
    predictions = {s['space_id']: 'Space' for s in bundle['spaces']}
    predictions.update({g['entity_id']: g['element_type'] for g in bundle['geometry_candidates']})
    links = {}; used_gt = set()
    for link in relation_gt.get('entity_matches', []):
        if link.get('review_status') != 'CONFIRMED': continue
        human_stamp(link)
        candidate, gt_id = link.get('candidate_id'), link.get('gt_id')
        if candidate not in predictions or gt_id not in inventory: raise ValueError('unresolved entity GT match')
        if candidate in links or gt_id in used_gt: raise ValueError('entity GT matches must be one-to-one')
        gt_type = 'Space' if inventory[gt_id]['element_type'] == 'SpaceBoundary' else inventory[gt_id]['element_type']
        if predictions[candidate] != gt_type: raise ValueError('candidate/GT object type mismatch')
        links[candidate] = gt_id; used_gt.add(gt_id)
    truth = set(); ids = set()
    for relation in relation_gt.get('relations', []):
        if relation.get('relation_type') != 'Connection':
            raise ValueError('only explicitly reviewed Connection GT can evaluate passage connectivity')
        if relation.get('review_status') != 'CONFIRMED': return pending
        human_stamp(relation)
        rid = relation['gt_relation_id']
        if rid in ids: raise ValueError('duplicate GT relation ID')
        ids.add(rid)
        src, dst, via = (relation.get(k) for k in ('source_gt_id', 'target_gt_id', 'via_gt_id'))
        if src == dst or src not in inventory or dst not in inventory or via not in inventory:
            raise ValueError('GT relation endpoints or physical opening unresolved')
        if inventory[src]['element_type'] != 'SpaceBoundary' or inventory[dst]['element_type'] != 'SpaceBoundary' or inventory[via]['element_type'] not in {'Door', 'Opening'}:
            raise ValueError('GT Connection requires two Spaces and Door/Opening')
        geometry = inventory[via]['geometry_native']; points = geometry.get('points_native', [])
        center = np.mean(points, axis=0).tolist()
        if not point_inside(center, scope['points_native']): raise ValueError('GT relation outside confirmed scope')
        edge = (tuple(sorted((src, dst))), via)
        if edge in truth: raise ValueError('duplicate semantic GT Connection')
        truth.add(edge)
    observed = []; unmapped = []
    by_id = {g['entity_id']: g for g in bundle['geometry_candidates']}
    for relation in bundle.get('relations', []):
        if relation['relation_type'] != 'Connection' or relation.get('claim_status') == 'REJECTED': continue
        via_id = relation.get('via_element_id'); via = by_id.get(via_id)
        if not via or not via['geometry_properties'].get('opening_segment_native'):
            raise ValueError('predicted Connection opening geometry missing')
        center = np.mean(via['geometry_properties']['opening_segment_native'], axis=0).tolist()
        if not point_inside(center, scope['points_native']): continue
        endpoints = [relation['source_id'], relation['target_id'], via_id]
        if any(identity not in links for identity in endpoints):
            unmapped.append(relation['relation_id']); continue
        src, dst, via_gt = (links[identity] for identity in endpoints)
        observed.append((tuple(sorted((src, dst))), via_gt))
    if unmapped:
        return {**pending, 'reason': 'in-scope predictions need explicit human entity matches', 'unmapped_relation_ids': unmapped}
    remaining = set(truth); tp = 0
    for edge in observed:
        if edge in remaining: tp += 1; remaining.remove(edge)
    fp, fn = len(observed)-tp, len(remaining)
    precision = tp/(tp+fp) if tp+fp else None
    recall = tp/(tp+fn) if tp+fn else None
    f1 = 2*tp/(2*tp+fp+fn) if 2*tp+fp+fn else None
    return {'status': 'EVALUATED_CONFIRMED_SCOPE_ONLY', 'dataset_kind': gt_package.get('dataset_kind','UNKNOWN'), 'metrics': {'TP': tp, 'FP': fp, 'FN': fn, 'precision': precision, 'recall': recall, 'F1': f1}, 'split': split, 'scope_native': scope, 'predictions_in_scope': len(observed), 'confirmed_relations': len(truth), 'all_candidates_including_uncertain_evaluated': True, 'representative_of_full_page': False}
