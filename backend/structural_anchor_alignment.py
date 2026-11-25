"""User-reviewed LINE constraints, Target native -> Ref native.

Selection endpoints delimit analysis intervals, NEVER endpoint correspondences.
The legacy SIFT/point alignment and its thresholds are intentionally untouched.
"""
from copy import deepcopy
from dataclasses import asdict, dataclass
import math
import unicodedata

import cv2
import numpy as np

from backend.structural_alignment import encode, fingerprint, image_binding, polygon_mask, transform, validate_roi

ENGINE = 'phase2b3-reviewed-lines-v1'
SEMANTICS = 'ANALYSIS_INTERVAL_BOUNDARIES'
SCOPE = 'SEGMENT_CORRESPONDENCE_ONLY'


@dataclass(frozen=True)
class Settings:
    normal_tolerance_ref_px: float = 3.0
    angle_tolerance_degrees: float = 1.0
    support_radius_native_px: float = 2.0
    ink_threshold: int = 160
    minimum_visible_support: float = .70
    minimum_axis_span_fraction: float = .15
    minimum_normal_eigen_ratio: float = .05
    maximum_condition_number: float = 10000.0
    maximum_rotation_degrees: float = 15.0
    minimum_rotation_concentration: float = .90


def parent_key(value):
    return unicodedata.normalize('NFC', value.strip()).casefold()


def segment(value, image):
    if (not isinstance(value, list) or len(value) != 2 or
        any(not isinstance(p, list) or len(p) != 2 or any(type(v) not in (int, float) for v in p) for p in value)):
        raise ValueError('구조 구간은 각 원본 이미지의 숫자 두 점이어야 합니다.')
    p = np.asarray(value, float)
    if not np.isfinite(p).all() or (p < 0).any() or (p > [image.width, image.height]).any() or np.array_equal(p[0], p[1]):
        raise ValueError('구조 구간의 원본 좌표·길이가 유효하지 않습니다.')
    return p


def validate_anchor_file(data, ref, target):
    if not isinstance(data, dict) or type(data.get('schema_version')) is not int or data.get('schema_version') != 1 or data.get('kind') != 'STRUCTURAL_ANCHOR_SET' or data.get('coordinate_system') != 'image_native_pixels' or data.get('endpoint_semantics') != SEMANTICS or data.get('landmark_correspondence') is not False:
        raise ValueError('Phase 2B-2 구조 구간 JSON이 필요합니다. 점 대응은 사용할 수 없습니다.')
    if data.get('ref_image') != image_binding(ref) or data.get('target_image') != image_binding(target):
        raise ValueError('Anchor의 양쪽 PDF·페이지·추출 배율·크기가 현재 도면과 다릅니다.')
    pairs = data.get('pairs')
    if not isinstance(pairs, list) or len(pairs) > 256:
        raise ValueError('구조 Anchor Pair는 256개 이하의 배열이어야 합니다.')
    seen = set()
    for p in pairs:
        if not isinstance(p, dict) or not isinstance(p.get('id'), str) or not 1 <= len(p['id']) <= 80 or p['id'] in seen:
            raise ValueError('Anchor ID가 비어 있거나 중복됐습니다.')
        seen.add(p['id'])
        if not isinstance(p.get('source'), dict) or p['source'].get('kind') not in ['MANUAL', 'ANALYST_REFERENCE']:
            raise ValueError('수동 지정 또는 재검토된 참고 구간 출처가 필요합니다.')
        if p.get('endpoint_semantics') != SEMANTICS or p.get('landmark_correspondence') is not False:
            raise ValueError('분석 구간 지정점을 Landmark로 사용할 수 없습니다.')
        if p.get('usage') not in ['FIT', 'VALIDATION', 'HOLD', 'EXCLUDE'] or p.get('boundary_kind') not in ['OUTER', 'INNER', 'OTHER', 'UNDETERMINED']:
            raise ValueError('구간 용도 또는 경계 종류가 잘못됐습니다.')
        if not isinstance(p.get('parent_structure_id'), str) or len(p['parent_structure_id']) > 120 or not isinstance(p.get('rationale'), str) or len(p['rationale']) > 2000 or type(p.get('revision')) is not int or p['revision'] < 0:
            raise ValueError('Parent·근거·revision이 잘못됐습니다.')
        for side, image in [('ref', ref), ('target', target)]:
            if p.get(side + '_segment') is not None:
                segment(p[side + '_segment'], image)
    return deepcopy(pairs)


def eligibility(p):
    if p['usage'] not in ['FIT', 'VALIDATION']:
        return False, p['usage'] + ' 구간은 추정·검증에 사용하지 않음'
    r = p.get('review')
    if not isinstance(r, dict) or r.get('status') != 'REVIEWED':
        return False, '현재 대응 검토 완료가 아님'
    if (r.get('scope') != SCOPE or type(r.get('applies_to_revision')) is not int or r['applies_to_revision'] != p['revision'] or
        r.get('identity_hypothesis_checked') is not True or r.get('interval_scope_checked') is not True or
        not isinstance(r.get('reason'), str) or not r['reason'].strip() or r['reason'].strip() != p['rationale'].strip() or
        not isinstance(r.get('at'), str) or not r['at']):
        return False, '검토 근거·범위·revision이 현재 구간과 일치하지 않음'
    if not p['parent_structure_id'].strip() or p['boundary_kind'] == 'UNDETERMINED' or p.get('ref_segment') is None or p.get('target_segment') is None:
        return False, '양쪽 완성 구간·Parent·경계 종류 확인 필요'
    return True, '현재 revision의 사용자 구간 대응 검토 완료'


def frame(points):
    p = np.asarray(points, float)
    u = (p[1] - p[0]) / np.linalg.norm(p[1] - p[0])
    n = np.array([-u[1], u[0]])
    return p.mean(0), u, n


def line_geometry(ref_points, target_points, matrix):
    r = np.asarray(ref_points, float)
    t = transform(target_points, matrix)
    rm, ru, rn = frame(r)
    tm, tu, tn = frame(t)
    # Point-to-INFINITE-LINE incidences, not paired physical endpoint distances.
    signed = (t - rm) @ rn
    reverse = (r - tm) @ tn
    distances = np.abs(np.r_[signed, reverse])
    angle = math.degrees(math.acos(float(np.clip(abs(ru @ tu), 0, 1))))
    ri = np.sort(r @ ru); ti = np.sort(t @ ru)
    lo, hi = max(ri[0], ti[0]), min(ri[1], ti[1]); overlap = max(0., hi-lo)
    overlap_points = [rm + (v-rm @ ru)*ru for v in [lo, hi]] if overlap > 0 else None
    return {'normal_signed_midpoint_ref_px': float((tm-rm) @ rn), 'normal_max_ref_px': float(distances.max()),
            'normal_rmse_ref_px': float(np.sqrt(np.mean(distances**2))), 'angle_degrees': angle,
            'analysis_interval_overlap_ref_px': float(overlap),
            'ref_analysis_interval_overlap_fraction': float(overlap/(ri[1]-ri[0])),
            'target_analysis_interval_overlap_fraction': float(overlap/(ti[1]-ti[0])) if ti[1]>ti[0] else 0.,
            'overlap_ref_points': np.asarray(overlap_points).tolist() if overlap_points is not None else None,
            'physical_endpoint_change': 'NOT_ASSESSED', 'physical_length_change': 'NOT_ASSESSED',
            'opening_change': 'NOT_ASSESSED', 'landmark_residual': None}


def visible_support(gray, points, radius, threshold):
    p = np.asarray(points, float); _, _, n = frame(p)
    count = min(2048, max(2, int(np.ceil(np.linalg.norm(p[1]-p[0])))+1))
    samples = p[0] + np.linspace(0, 1, count)[:, None]*(p[1]-p[0])
    # Reported raster diagnostic; ink is not proof of a wall or stability.
    offsets = np.linspace(-radius, radius, max(1, int(math.ceil(radius*4))+1))
    q = samples[:, None, :] + offsets[None, :, None]*n
    x = np.rint(q[..., 0]).astype(int); y = np.rint(q[..., 1]).astype(int)
    valid = (x >= 0) & (y >= 0) & (x < gray.shape[1]) & (y < gray.shape[0])
    ink = gray[np.clip(y, 0, gray.shape[0]-1), np.clip(x, 0, gray.shape[1]-1)] < threshold
    return float(np.any(valid & ink, axis=1).mean())


def distribution(pairs, side, roi, settings):
    if not pairs:
        return {'parent_count': 0, 'segment_count': 0, 'sufficient': False, 'normal_eigen_ratio': 0., 'axis_span_fraction': [0., 0.], 'midpoint_hull': []}
    points = [p[side+'_segment'] for p in pairs]; mids = np.array([frame(p)[0] for p in points]); normals = np.array([frame(p)[2] for p in points])
    eigen = np.linalg.eigvalsh(normals.T @ normals / len(normals)); ratio = float(eigen[0]/max(eigen[1], 1e-15))
    extent = np.maximum(np.ptp(np.asarray(roi, float), axis=0), 1.)
    span = np.ptp(mids, axis=0)/extent
    parents = len({parent_key(p['parent_structure_id']) for p in pairs})
    enough = parents >= 3 and ratio >= settings.minimum_normal_eigen_ratio and bool(np.all(span >= settings.minimum_axis_span_fraction))
    hull = cv2.convexHull(mids.astype(np.float32)).reshape(-1, 2).tolist() if len(mids) >= 3 else []
    return {'parent_count': parents, 'segment_count': len(pairs), 'normal_eigen_ratio': ratio,
            'axis_span_fraction': span.tolist(), 'sufficient': bool(enough), 'midpoint_hull': hull,
            'hull_is_dense_validation': False, 'normal_matrix_eigenvalues': eigen.tolist()}


def fit_lines(pairs, ref_size, target_size, settings):
    # Unoriented angles (mod pi). The allowed small-rotation branch is explicit.
    weights = np.array([1/sum(parent_key(q['parent_structure_id']) == parent_key(p['parent_structure_id']) for q in pairs) for p in pairs])
    angles = []
    for p in pairs:
        ru = frame(p['ref_segment'])[1]; tu = frame(p['target_segment'])[1]
        angles.append(math.atan2(ru[1], ru[0])-math.atan2(tu[1], tu[0]))
    z = np.sum(weights * np.exp(2j*np.asarray(angles))) / weights.sum()
    rotation = float(np.angle(z)/2); concentration = float(abs(z))
    if concentration < settings.minimum_rotation_concentration or abs(math.degrees(rotation)) > settings.maximum_rotation_degrees:
        return None, {'rotation_concentration': concentration}, 'FIT 방향이 충돌하거나 허용 회전 분기를 벗어났습니다.'
    rot = np.array([[math.cos(rotation), -math.sin(rotation)], [math.sin(rotation), math.cos(rotation)]])
    rc = np.average([frame(p['ref_segment'])[0] for p in pairs], axis=0, weights=weights)
    tc = np.average([frame(p['target_segment'])[0] for p in pairs], axis=0, weights=weights)
    rd, td = np.linalg.norm(ref_size), np.linalg.norm(target_size)
    a, b = [], []
    for p in pairs:
        rm, _, rn = frame(p['ref_segment']); tm = frame(p['target_segment'])[0]
        a.append([rn @ (rot @ (tm-tc))/td, rn[0], rn[1]])
        b.append(rn @ (rm-rc)/rd)
    a = np.asarray(a)*np.sqrt(weights[:, None]); b = np.asarray(b)*np.sqrt(weights)
    x, _, rank, singular = np.linalg.lstsq(a, b, rcond=None)
    condition = float(singular[0]/singular[-1]) if singular[-1] > 0 else None
    diagnostics = {'rank': int(rank), 'condition_number': condition, 'singular_values': singular.tolist(),
                   'rotation_concentration': concentration, 'rotation_degrees': math.degrees(rotation),
                   'equations': 'n_ref dot (s R midpoint_target + translation - midpoint_ref) = 0; unoriented direction agreement',
                   'paired_endpoint_constraints': False}
    if rank < 3 or condition is None or condition > settings.maximum_condition_number:
        return None, diagnostics, 'FIT 선 배치가 배율·이동을 구분하기에 부족하거나 수치적으로 불안정합니다.'
    scale = float(x[0]*rd/td)
    if not .05 <= scale <= 20:
        return None, diagnostics, '양의 similarity 배율이 유효 범위를 벗어났습니다.'
    matrix = np.eye(3); matrix[:2, :2] = scale*rot; matrix[:2, 2] = rc+rd*x[1:]-scale*rot @ tc
    diagnostics['scale_target_to_ref'] = scale
    return matrix, diagnostics, None


def measure_pairs(pairs, matrix, grays, settings):
    rows = []
    inverse = np.linalg.inv(matrix)
    for p in pairs:
        g = line_geometry(p['ref_segment'], p['target_segment'], matrix)
        support = {side: visible_support(im, p[side+'_segment'], settings.support_radius_native_px, settings.ink_threshold) for side, im in zip(['ref', 'target'], grays)}
        overlap = g['overlap_ref_points']
        joint = min(visible_support(grays[0], overlap, settings.support_radius_native_px, settings.ink_threshold), visible_support(grays[1], transform(overlap, inverse), settings.support_radius_native_px, settings.ink_threshold)) if overlap is not None else None
        rows.append({'id': p['id'], 'parent_structure_id': p['parent_structure_id'], 'usage': p['usage'], **g,
                     'visible_ink_support': support, 'common_window_support_min': joint,
                     'support_is_semantic_stability': False,
                     'geometry_passed': g['normal_max_ref_px'] <= settings.normal_tolerance_ref_px and g['angle_degrees'] <= settings.angle_tolerance_degrees,
                     'support_passed': min(support.values()) >= settings.minimum_visible_support and joint is not None and joint >= settings.minimum_visible_support})
    return rows


def assess_matrix(pairs, matrix, grays, settings):
    """Also used to reject the frozen north-wall absorption regression matrix."""
    rows = measure_pairs(pairs, matrix, grays, settings)
    failed = [p['id'] for p in rows if not p['geometry_passed']]
    return rows, failed


def align_structural_anchors(ref_page, target_page, roi, anchor_file, settings=None):
    settings = settings or Settings()
    if max(p.width*p.height for p in [ref_page, target_page]) > 12_000_000:
        raise ValueError('구간 정렬은 이미지당 1,200만 픽셀 이하로 추출하세요.')
    polygon = validate_roi(roi, target_page); pairs = validate_anchor_file(anchor_file, ref_page, target_page)
    grays = [cv2.imdecode(np.frombuffer(p.png, np.uint8), 0) for p in [ref_page, target_page]]
    result = {'engine': ENGINE, 'direction': 'target_native_to_ref_native', 'coordinate_system': 'image_native_pixels',
              'ref_image': image_binding(ref_page), 'target_image': image_binding(target_page), 'target_roi': deepcopy(roi),
              'anchor_fingerprint': fingerprint(anchor_file), 'input_fingerprint': fingerprint({'anchors': anchor_file, 'roi': roi, 'settings': asdict(settings)}),
              'settings': asdict(settings), 'status': 'DEFERRED', 'reasons': [], 'matrix': None, 'inverse_matrix': None,
              'mapping': None, 'provisional_mapping': None, 'roi_mapping_confirmed': False, 'rows': [],
              'review': {'state': 'NOT_APPROVED', 'scope': 'sampled_line_constraints_only'}, 'approval_eligible': False,
              'physical_endpoint_length_opening_changes': 'NOT_ASSESSED', 'automatic_stability_declared': False,
              'source_anchor_file': deepcopy(anchor_file), 'eligibility': [], 'fit_ids': [], 'validation_ids': [],
              'validation_used_for_estimation': False, 'validity': 'CURRENT'}
    eligible = []
    for p in pairs:
        ok, why = eligibility(p); result['eligibility'].append({'id': p['id'], 'eligible': ok, 'reason': why})
        if ok: eligible.append(p)
    fit = [p for p in eligible if p['usage'] == 'FIT']; val = [p for p in eligible if p['usage'] == 'VALIDATION']
    result['fit_ids'] = [p['id'] for p in fit]; result['validation_ids'] = [p['id'] for p in val]
    shared = {parent_key(p['parent_structure_id']) for p in fit} & {parent_key(p['parent_structure_id']) for p in val}
    result['parent_conflicts'] = sorted(shared)
    if shared:
        result['reasons'].append('같은 Parent Structure가 FIT와 VALIDATION에 있어 독립 검증이 아닙니다.'); return result, {}
    # Ref distribution is normalized by a separate native image extent, not Target coordinates.
    fit_dist = {side: distribution(fit, side, polygon if side == 'target' else [[0, 0], [ref_page.width, ref_page.height]], settings) for side in ['ref', 'target']}
    result['fit_distribution'] = fit_dist
    result['validation_distribution'] = {side: distribution(val, side, polygon if side == 'target' else [[0, 0], [ref_page.width, ref_page.height]], settings) for side in ['ref', 'target']}
    if not all(d['sufficient'] for d in fit_dist.values()):
        result['reasons'].append('FIT 부모 구조 3개·비평행 방향·양축 공간 분포가 부족합니다.'); return result, {}
    matrix, diagnostics, error = fit_lines(fit, [ref_page.width, ref_page.height], [target_page.width, target_page.height], settings)
    result['fit_conditioning'] = diagnostics
    if error:
        result['reasons'].append(error); return result, {}
    rows, failures = assess_matrix(fit+val, matrix, grays, settings); result['rows'] = rows
    result['rejected_matrix'] = matrix.tolist()
    if failures:
        result['status'] = 'FAILED'; result['reasons'].append('법선 거리 또는 방향 검증 실패: '+', '.join(failures)); return result, {}
    if any(not r['support_passed'] for r in rows if r['usage'] == 'FIT'):
        result['reasons'].append('FIT의 가시 잉크 지지 또는 양쪽 분석 범위의 공통 지지가 부족합니다.'); return result, {}
    # Different Parent IDs alone do not make duplicate geometric support independent.
    duplicates = []
    for v in val:
        for f in fit:
            for side in ['ref', 'target']:
                g = line_geometry(f[side+'_segment'], v[side+'_segment'], np.eye(3))
                if g['normal_max_ref_px'] <= settings.support_radius_native_px*2 and g['angle_degrees'] <= settings.angle_tolerance_degrees and g['analysis_interval_overlap_ref_px'] > 0:
                    duplicates.append({'fit': f['id'], 'validation': v['id'], 'side': side})
    result['duplicate_geometric_support'] = duplicates
    val_dist = result['validation_distribution']
    mapped = transform(polygon, matrix)
    outside = [i for i, p in enumerate(mapped) if not (0 <= p[0] <= ref_page.width and 0 <= p[1] <= ref_page.height)]
    sufficient = bool(val) and not duplicates and all(r['support_passed'] for r in rows) and all(d['sufficient'] for d in val_dist.values()) and not outside
    result['status'] = 'VERIFIED_SAMPLED' if sufficient else 'PARTIAL'
    result['approval_eligible'] = sufficient
    if not val: result['reasons'].append('독립 VALIDATION 구간이 없습니다.')
    if duplicates: result['reasons'].append('Parent ID가 달라도 FIT와 겹치는 기하 지지는 독립 검증으로 세지 않습니다.')
    if not all(d['sufficient'] for d in val_dist.values()): result['reasons'].append('VALIDATION의 부모 구조 수·방향·공간 분포가 부족합니다.')
    if any(not r['support_passed'] for r in rows if r['usage'] == 'VALIDATION'): result['reasons'].append('VALIDATION 가시 지지 또는 공통 분석 범위가 부족합니다.')
    if outside: result['reasons'].append('임시 Ref ROI에 이미지 밖 꼭짓점이 있습니다.')
    result['reasons'].append('검증 범위는 선택한 선의 위치·방향·가시 지지입니다. 물리적 끝점·길이·개구부와 ROI 전체는 미검증입니다.')
    result['matrix'] = matrix.tolist(); result['inverse_matrix'] = np.linalg.inv(matrix).tolist(); result.pop('rejected_matrix', None)
    result['provisional_mapping'] = {'target_vertices': polygon.tolist(), 'ref_vertices_unclipped': mapped.tolist(), 'outside_ref_vertex_indices': outside, 'confirmed': False, 'scope': 'diagnostic_only'}
    # Probe bands and midpoint hulls describe sampling, not densely verified ROI area.
    tmask = polygon_mask(polygon, grays[1].shape); bands = np.zeros_like(tmask)
    probed = []
    for row in rows:
        if row['usage'] != 'VALIDATION' or not row['support_passed'] or row['overlap_ref_points'] is None:
            continue
        interval = transform(row['overlap_ref_points'], np.linalg.inv(matrix))
        probed.append(interval.tolist())
        cv2.line(bands, *[tuple(np.rint(np.asarray(q)*256).astype(int)) for q in interval], 255, max(1, int(round(settings.normal_tolerance_ref_px/np.linalg.norm(matrix[:2, 0])*2))), shift=8)
    total = int(np.count_nonzero(tmask))
    result['coverage'] = {'validation_probe_band_roi_fraction': int(np.count_nonzero(tmask & bands))/total if total else 0., 'band_is_dense_validation': False,
                          'validation_segments_target': [p['target_segment'] for p in val], 'common_supported_probe_intervals_target': probed, 'target_roi_pixels': total,
                          'verified_physical_endpoints': 0, 'verified_openings': 0, 'whole_roi_verified': False}
    warped = cv2.warpAffine(grays[0], matrix[:2], (target_page.width, target_page.height), flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP, borderValue=255)
    overlay = np.full((*grays[1].shape, 3), 255, np.uint8)
    overlay[:, :, 0] = warped; overlay[:, :, 1] = warped; overlay[:, :, 2] = grays[1]
    overlay[tmask == 0] = 245
    for p in fit+val:
        color = (140, 120, 0) if p['usage'] == 'FIT' else (180, 50, 140)
        cv2.line(overlay, *[tuple(np.rint(q).astype(int)) for q in p['target_segment']], color, 2)
    return result, {'overlay': encode(overlay), 'warped_ref': encode(warped), 'probe_bands': encode(cv2.bitwise_and(bands, tmask))}
