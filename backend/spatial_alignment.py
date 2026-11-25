"""Stage 9. Scoped Target-native → Ref-native registration of independent understandings."""
import hashlib
import math
from dataclasses import asdict, replace

import cv2
import numpy as np

from backend.stable_intervals import allocate, propose
from backend.structural_alignment import fingerprint, image_binding, polygon_mask, transform, validate_roi
from backend.structural_anchor_alignment import Settings, distribution, fit_lines, frame, measure_pairs


def check_source(page, bundle, role):
    binding = bundle['source_binding']
    expected = image_binding(page)
    frame_id = role+':'+expected['image_id']
    model = bundle.get('reference_model' if role == 'Ref' else 'target_model') or {}
    if (any(binding.get(k) != v for k, v in expected.items()) or
            binding.get('image_sha256') != hashlib.sha256(page.png).hexdigest() or
            any(source.get('frame_id') != frame_id or source.get('source_role') != role for source in
                (binding, bundle, model, bundle['space_closure'], bundle['architectural_assessment']))):
        raise ValueError('독립 이해 결과의 원본·역할·Frame을 확인하세요.')
    return dict(image=expected, frame_id=frame_id, geometry_run_id=bundle['run_id'],
                image_sha256=binding['image_sha256'], scope=binding['core_bbox_native'])


def measure(pairs, matrix, grays, cfg):
    primary = measure_pairs(pairs, matrix, grays, cfg)
    other = [dict(p, ref_segment=p['ref_other_face'], target_segment=p['target_other_face']) for p in pairs]
    secondary = measure_pairs(other, matrix, grays, cfg)
    for a, b in zip(primary, secondary):
        a['other_face'] = b
        a['passed'] = all(row['geometry_passed'] and row['support_passed'] and
                          min(row['ref_analysis_interval_overlap_fraction'], row['target_analysis_interval_overlap_fraction']) >= .35
                          for row in (a, b))
    return primary


def fit_model(fit, grays, sizes, cfg):
    # Prefer two translation DOFs when the FIT observations already support them.
    normals = np.array([frame(p['ref_segment'])[2] for p in fit])
    deltas = np.array([np.mean(p['ref_segment'], axis=0)-np.mean(p['target_segment'], axis=0) for p in fit])
    weights = np.sqrt([1/sum(q['parent_structure_id']==p['parent_structure_id'] for q in fit) for p in fit])
    shift, _, rank, _ = np.linalg.lstsq(normals*weights[:, None], (normals*deltas).sum(axis=1)*weights, rcond=None)
    matrix = np.eye(3)
    matrix[:2, 2] = shift
    if rank == 2 and all(r['passed'] for r in measure(fit, matrix, grays, cfg)):
        return matrix, {'model':'TRANSLATION', 'dof':2, 'rank':int(rank)}, None
    matrix, diagnostics, why = fit_lines(fit, *sizes, cfg)
    return matrix, {**diagnostics, 'model':'SIMILARITY', 'dof':4}, why


def directional_hulls(rows, pairs, inverse):
    axes = {p['id']:p['axis'] for p in pairs}
    hulls = []
    for axis in (0, 1):
        points = [point for r in rows if r['usage']=='VALIDATION' and axes[r['id']]==axis
                  for point in transform(r['overlap_ref_points'], inverse)]
        if len(points)<3:
            return []
        hull = cv2.convexHull(np.float32(points))
        if cv2.contourArea(hull) < 1:
            return []
        hulls.append(hull)
    return hulls


def coverage_cells(polygon, matrix, grays, scopes, hulls, zoom, hull_margin=0):
    """Local, unique two-dimensional image agreement, never extrapolate a validation hull."""
    ref, target = grays
    h, w = target.shape
    mask = polygon_mask(polygon, (h, w))
    total = cv2.countNonZero(mask)
    # Include a one-pixel guard when certifying the continuous polygon boundary.
    guarded = cv2.dilate(mask, np.ones((3,3), np.uint8))
    tile = max(24, round(32*zoom), math.ceil(max(w,h)/64))
    radius = max(4, round(4*zoom))
    tolerance = max(1, round(zoom))
    audit = dict(target_roi_pixels=total, validated_pixels=0, fraction=0., tiles=[],
                 directional_validation_hulls_target=[v.reshape(-1,2).tolist() for v in hulls],
                 tile_size_px=tile, search_radius_px=radius, peak_offset_tolerance_px=tolerance,
                 full_boundary_guard_passed=False, minimum_correlation=.80, minimum_peak_margin=.05, unit='native_pixel_samples',
                 scope='ROI_INTERSECTION_WITH_EACH_ACCEPTED_TILE', unmeasured_structure='UNKNOWN')
    if not hulls:
        audit['reason']='두 방향의 독립 검증 구간이 둘러싸는 면적이 부족합니다.'
        return audit
    warped = cv2.warpAffine(ref, matrix[:2], (w,h), flags=cv2.INTER_LINEAR|cv2.WARP_INVERSE_MAP, borderValue=255)
    ref_box, target_box = scopes
    for y in range(0,h,tile):
        for x in range(0,w,tile):
            x2,y2 = min(w,x+tile), min(h,y+tile)
            count = cv2.countNonZero(mask[y:y2,x:x2])
            if not count and not np.any(guarded[y:y2,x:x2]):
                continue
            corners = [[x,y],[x2,y],[x2,y2],[x,y2]]
            mapped = transform(corners,matrix)
            row = dict(target_bbox_native=[x,y,x2,y2], roi_pixels=count)
            audit['tiles'].append(row)
            if any(cv2.pointPolygonTest(hull, tuple(point), bool(hull_margin)) < -hull_margin for hull in hulls for point in corners):
                row['status']='OUTSIDE_DIRECTIONAL_SUPPORT'
                continue
            # Keep the entire search window inside both observed native frames and scopes.
            search = [[x-radius,y-radius],[x2+radius,y-radius],[x2+radius,y2+radius],[x-radius,y2+radius]]
            ref_search = transform(search,matrix)
            if (np.any(np.min(ref_search,axis=0)<ref_box[:2]) or np.any(np.max(ref_search,axis=0)>ref_box[2:])
                    or x-radius<target_box[0] or y-radius<target_box[1] or x2+radius>target_box[2] or y2+radius>target_box[3]):
                row['status']='OUTSIDE_OBSERVED_FRAME'
                continue
            patch=target[y:y2,x:x2]
            corners_detected=cv2.goodFeaturesToTrack(patch, 20, .04, max(3,zoom*2))
            if corners_detected is None or len(corners_detected)<3 or min(np.ptp(corners_detected[:,0],axis=0)) < min(patch.shape)*.25:
                row['status']='INSUFFICIENT_2D_TEXTURE'
                continue
            scores=cv2.matchTemplate(warped[y-radius:y2+radius,x-radius:x2+radius],patch,cv2.TM_CCOEFF_NORMED)
            _,peak,_,(px,py)=cv2.minMaxLoc(scores)
            yy,xx=np.indices(scores.shape)
            others=scores[np.maximum(abs(xx-px),abs(yy-py))>tolerance]
            gap=peak-float(others.max())
            row.update(correlation=peak, peak_margin=gap, peak_shift_target_px=[px-radius,py-radius])
            if peak<.80 or gap<.05 or math.hypot(px-radius,py-radius)>tolerance:
                row['status']='LOCAL_AGREEMENT_UNRESOLVED'
                continue
            row.update(status='SUPPORTED', ref_tile_native=mapped.tolist())
            audit['validated_pixels']+=count
    audit['full_boundary_guard_passed']=bool(audit['tiles']) and all(t['status']=='SUPPORTED' for t in audit['tiles'])
    audit['fraction']=audit['validated_pixels']/total if total else 0.
    return audit


def verify_geometry(pages, geometries, roi):
    sources = [check_source(p,g,r) for p,g,r in zip(pages,geometries,('Ref','Target'))]
    if max(p.width*p.height for p in pages)>12_000_000:
        raise ValueError('정렬은 도면당 1,200만 픽셀 이하에서 실행하세요.')
    polygon = validate_roi(roi,pages[1])
    pairs, search = propose(*geometries)
    allocate(pairs,[p.zoom for p in pages])
    cfg = replace(Settings(), normal_tolerance_ref_px=1.5*pages[0].zoom)
    result = dict(schema_version='spatial_alignment/1', direction='target_native_to_ref_native',
                  sources=sources, target_roi=roi, input_fingerprint=fingerprint([sources,roi]),
                  settings={**asdict(cfg),'minimum_common_interval_fraction':.35}, status='DEFERRED', reasons=[], pairs=pairs, search=search,
                  matrix=None, inverse_matrix=None, mapping=None, diagnostic_matrix=None, rows=[],
                  coverage=None, roi_mapping_confirmed=False, validation_used_for_fit=False)
    partitions = {use:[p for p in pairs if p['usage']==use] for use in ('FIT','VALIDATION')}
    dist = {use:{side:distribution(rows,side,np.array(s['scope']).reshape(2,2),cfg) for side,s in
                 zip(('ref','target'),sources)} for use,rows in partitions.items()}
    result['distribution']=dist
    if search.get('reason') or not all(d['sufficient'] for d in dist['FIT'].values()):
        result['reasons'].append('구조 구간 탐색 상한을 초과했습니다.' if search.get('reason') else '적합에 필요한 서로 다른 부모 벽, 두 방향 또는 공간 분포가 부족합니다.')
        return result
    grays = [cv2.imdecode(np.frombuffer(p.png,np.uint8),0) for p in pages]
    sizes = [[p.width,p.height] for p in pages]
    matrix, diagnostics, error = fit_model(partitions['FIT'],grays,sizes,cfg)
    result['fit_model']=diagnostics
    if error:
        result['reasons'].append(error)
        return result
    rows=measure(pairs,matrix,grays,cfg)
    result.update(rows=rows,diagnostic_matrix=matrix.tolist())
    for p,r in zip(pairs,rows):
        p['interval_stability']='SAMPLED_AGREEMENT' if r['passed'] else 'CONFLICT_OR_INSUFFICIENT_SUPPORT'
    if any(not r['passed'] for r in rows):
        result.update(status='FAILED',reasons=['독립 검증 또는 적합 구간의 양쪽 경계에서 위치·방향·가시 지지·공통 범위 조건을 충족하지 못했습니다.'])
        return result
    if not all(d['sufficient'] for d in dist['VALIDATION'].values()):
        result.update(status='PARTIAL',reasons=['독립 검증의 부모 벽·방향·공간 분포가 부족합니다.'])
        return result
    inverse=np.linalg.inv(matrix)
    hulls=directional_hulls(rows,pairs,inverse)
    coverage=coverage_cells(polygon,matrix,grays,[s['scope'] for s in sources],hulls,pages[1].zoom)
    result['coverage']=coverage
    if coverage['validated_pixels']:
        result.update(matrix=matrix.tolist(), inverse_matrix=inverse.tolist())
        full=coverage['validated_pixels']==coverage['target_roi_pixels'] and coverage['full_boundary_guard_passed']
        result.update(status='VERIFIED_ROI' if full else 'PARTIAL', roi_mapping_confirmed=full)
        result['mapping']=dict(scope='FULL_ROI' if full else 'SUPPORTED_TILES_INTERSECT_ROI_ONLY',
                               target_roi_vertices=polygon.tolist(), ref_roi_vertices=transform(polygon,matrix).tolist() if full else None,
                               supported_tile_indices=[i for i,r in enumerate(coverage['tiles']) if r['status']=='SUPPORTED'])
    else:
        result['status']='PARTIAL'
    if not result['roi_mapping_confirmed']:
        result['reasons'].append(coverage.get('reason','검증 범위 밖 또는 국소 영상 지지가 부족한 영역은 대응을 보류합니다.'))
    return result


def verify_wall_faces(pages, primary, strokes):
    """Use raster to retrieve candidates, then estimate only from structural normals."""
    from backend.wall_sections import anchor_pairs, paired_sections, raster_guide, sections

    polygon = np.asarray(primary['target_roi']['vertices'])
    scopes = [source['scope'] for source in primary['sources']]
    zooms = [page.zoom for page in pages]
    grays = [cv2.imdecode(np.frombuffer(p.png, np.uint8), 0) for p in pages]
    search_polygon = polygon
    guide, matches, inliers = raster_guide(grays, search_polygon, zooms)
    audit = dict(method='WALL_FACES_WITH_RASTER_SEARCH', search_matches=matches,
                 search_inliers=inliers, previous_status=primary['status'], previous_reasons=primary['reasons'])
    result = {**primary, 'wall_registration':audit}
    if guide is None:
        return result
    # Keep matching in the selected drawing vicinity rather than nearby sheets or unit plans.
    margin = 45*zooms[1]
    envelope = np.array([polygon.min(axis=0)-margin, polygon.max(axis=0)+margin])
    x,y = envelope[0]; u,v = envelope[1]
    boxes = [transform([[x,y],[u,y],[u,v],[x,v]],guide), envelope]
    windows = [[*np.maximum(box.min(axis=0),scope[:2]), *np.minimum(box.max(axis=0),scope[2:])]
               for box,scope in zip(boxes,scopes)]
    candidates = [sections(stroke,zoom,scope) for stroke,zoom,scope in zip(strokes,zooms,windows)]
    audit['sections_per_source'] = [len(q) for q in candidates]
    if max(audit['sections_per_source'])>256:
        return result
    pairs = anchor_pairs(paired_sections(*candidates,guide,zooms[0]),guide,zooms[0])
    allocate(pairs,zooms)
    cfg = replace(Settings(), normal_tolerance_ref_px=1.5*zooms[0])
    groups = {use:[p for p in pairs if p['usage']==use] for use in ('FIT','VALIDATION')}
    dist = {use:{side:distribution(group,side,np.array(scope).reshape(2,2),cfg)
                 for side,scope in zip(('ref','target'),scopes)} for use,group in groups.items()}
    audit.update(distribution=dist, attempted_pairs=len(pairs))
    if not all(d['sufficient'] for group in dist.values() for d in group.values()):
        return result
    matrix, model, reason = fit_model(groups['FIT'],grays,[[p.width,p.height] for p in pages],cfg)
    if matrix is None:
        audit['failure'] = reason
        return result
    rows = measure(pairs,matrix,grays,cfg)
    audit['independent_max_normal_ref_px'] = max(max(r['normal_max_ref_px'],r['other_face']['normal_max_ref_px'])
                                                for r in rows if r['usage']=='VALIDATION')
    if not all(row['passed'] for row in rows):
        audit['failure'] = 'INDEPENDENT_FACE_DISAGREEMENT'
        return result
    for pair in pairs:
        pair['interval_stability'] = 'SAMPLED_AGREEMENT'
    inverse = np.linalg.inv(matrix)
    holdout_points = [point for row in rows if row['usage']=='VALIDATION'
                      for face in (row,row['other_face']) for point in face['overlap_ref_points']]
    hull = cv2.convexHull(np.float32(transform(holdout_points,inverse)))
    if cv2.contourArea(hull)<1:
        return result
    edge_margin = 28*zooms[1]
    coverage = coverage_cells(polygon,matrix,grays,scopes,[hull],zooms[1],edge_margin)
    coverage['scope'] = 'INDEPENDENT_WALL_HULL_WITH_LOCAL_RASTER_VERIFICATION'
    coverage['max_distance_outside_holdout_hull_px'] = edge_margin
    if not coverage['validated_pixels']:
        return result
    indices = [i for i,row in enumerate(coverage['tiles']) if row['status']=='SUPPORTED']
    result.update(status='PARTIAL', pairs=pairs, rows=rows, distribution=dist, fit_model=model,
                  matrix=matrix.tolist(), inverse_matrix=inverse.tolist(), diagnostic_matrix=matrix.tolist(),
                  coverage=coverage, reasons=['벽 양면으로 정렬을 검증했습니다. 영상 지지가 확인된 영역에서 구조를 비교합니다.'],
                  mapping=dict(scope='SUPPORTED_TILES_INTERSECT_ROI_ONLY', supported_tile_indices=indices,
                               target_roi_vertices=polygon.tolist(), ref_roi_vertices=None))
    return result


def verify(pages, geometries, roi, strokes=None):
    primary = verify_geometry(pages, geometries, roi)
    if strokes and all(strokes) and primary['status'] in ('DEFERRED', 'PARTIAL') and primary['matrix'] is None:
        return verify_wall_faces(pages,primary,strokes)
    return primary
