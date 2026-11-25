"""Reference-only fragment closure and bounded ontology-guided reobservation.

Produces conditional regions, never GT or new physical Walls. Reuses the PDF
adapter and preserved vector/raster primitives. No Target inputs or model calls.
"""
from dataclasses import asdict, dataclass
import copy
import hashlib
import itertools
import math
import time
import uuid

import cv2
import numpy as np
import pymupdf

from phase1_pdf import render_page
from backend.architectural_observations import clip_segment
from poc.space_grounding_geometry import (Settings, axis_points, axis_segment,
    merge_lines, pdf_to_native_matrix, raster_observations, vector_observations)
from integrations.fpue_ontology.vocabulary import resolve_space_term
from integrations.fpue_ontology import understanding_contract_v0_4 as ontology


@dataclass(frozen=True)
class RestorationSettings:
    version: str = 'reference_fragment_restoration/1'
    normal_tolerance_px: float = 1.5
    projection_reach_text_heights: float = 2.5
    minimum_line_text_heights: float = 2.0
    minimum_side_text_heights: float = 3.0
    search_radius_page_fraction: float = .28
    side_alternatives: int = 4
    minimum_supported_side_fraction: float = .30
    maximum_inferred_perimeter_fraction: float = .55
    context_margin_text_heights: float = 6.0
    maximum_reobservations: int = 24
    maximum_labels: int = 48
    reobserve_zoom_factor: float = 2.0
    gap_arc_radius_ratio_min: float = .70
    gap_arc_radius_ratio_max: float = 1.55


def digest(data):
    return hashlib.sha256(data).hexdigest()


def layer_role(layers):
    """CAD metadata is a hint, never an architectural verification."""
    words = [str(layer).upper() for layer in layers]
    if words and all('A-FIN2' in word for word in words):
        return 'SURFACE_PATTERN_HINT'
    excluded = ('DIM', 'A-CEN', 'CENTER', 'A-HID', 'A-FUR', 'A-TOI',
                'A-SHE', 'A-TE', 'TEXT', '커텐', '실명')
    if words and all(any(token in word for token in excluded) for word in words):
        return 'ANNOTATION_OR_FIXTURE_HINT'
    if any(any(token in word for token in ('A-WAL', 'WALL', 'A-FIN')) for word in words):
        return 'STRUCTURAL_BOUNDARY_HINT'
    if any('A-WIN' in word for word in words):
        return 'WINDOW_ASSEMBLY_BOUNDARY_HINT'
    if any('A-DOR' in word or 'DOOR' in word for word in words):
        return 'OPENING_SYMBOL_HINT'
    return 'UNCLASSIFIED_LAYER'


def interval_union(intervals, start, end):
    result = []
    for a, b in sorted((max(start, a), min(end, b)) for a, b in intervals if b > start and a < end):
        if b <= a:
            continue
        if result and a <= result[-1][1] + 1e-6:
            result[-1][1] = max(b, result[-1][1])
        else:
            result.append([float(a), float(b)])
    return result


def interval_gaps(intervals, start, end):
    result = []
    previous = start
    for a, b in interval_union(intervals, start, end):
        if a > previous + 1e-6:
            result.append([float(previous), float(a)])
        previous = b
    if previous < end - 1e-6:
        result.append([float(previous), float(end)])
    return result


def ring_from_box(box):
    x0, y0, x1, y1 = map(float, box)
    return [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]


def _covers(line, tangent, reach):
    return line['start'] - reach <= tangent <= line['end'] + reach


def propose_region(lines, seed, text_height, labels=(), settings=None):
    """Rank observed support planes and endpoint-derived fourth-side alternatives.

    Endpoint extension is explicit inferred geometry. The score is a ranking
    heuristic, not an architectural correctness probability.
    """
    cfg = settings or RestorationSettings()
    x, y = map(float, seed)
    reach = text_height * cfg.projection_reach_text_heights
    minimum = text_height * cfg.minimum_line_text_heights
    candidates = {'left': [], 'right': [], 'top': [], 'bottom': []}
    for line in lines:
        if line['end'] - line['start'] < minimum:
            continue
        axis, normal = line['axis'], line['normal']
        if axis == 'V' and _covers(line, y, reach):
            if abs(normal-x) > text_height*.6:
                candidates['left' if normal < x else 'right'].append((abs(normal-x), normal, False))
        if axis == 'H' and _covers(line, x, reach):
            if abs(normal-y) > text_height*.6:
                candidates['top' if normal < y else 'bottom'].append((abs(normal-y), normal, False))
    # A U made of three sides can propose its fourth side from the two open ends.
    for side, axis, endpoint, positive in [('left', 'H', 'start', False), ('right', 'H', 'end', True),
                                         ('top', 'V', 'start', False), ('bottom', 'V', 'end', True)]:
        if candidates[side]:
            continue
        coordinate = x if axis == 'H' else y
        endpoints = [line[endpoint] for line in lines if line['axis'] == axis and line['end']-line['start'] >= minimum
                     and ((line[endpoint] > coordinate) if positive else (line[endpoint] < coordinate))]
        for value in endpoints:
            if sum(abs(other-value) <= cfg.normal_tolerance_px for other in endpoints) >= 2:
                candidates[side].append((abs(value-coordinate), value, True))
    choices = []
    for side in ['left', 'top', 'right', 'bottom']:
        selected = []
        for distance, value, inferred in sorted(candidates[side]):
            if all(abs(value-v[0]) > cfg.normal_tolerance_px for v in selected):
                selected.append((float(value), inferred))
            if len(selected) >= cfg.side_alternatives:
                break
        if not selected:
            return {'status': 'PARTIAL_BOUNDARY', 'reason': 'NO_SUPPORTED_OR_ENDPOINT_DERIVED_'+side.upper(), 'polygon_native': None, 'alternatives': []}
        choices.append(selected)
    ranked = []
    for planes in itertools.product(*choices):
        left, top, right, bottom = [p[0] for p in planes]
        width, height = right-left, bottom-top
        if min(width, height) < text_height*cfg.minimum_side_text_heights or max(width, height)/min(width, height) > 8:
            continue
        edges = []
        far_faces = []
        for side, axis, normal, start, end in [('left', 'V', left, top, bottom), ('top', 'H', top, left, right),
                                               ('right', 'V', right, top, bottom), ('bottom', 'H', bottom, left, right)]:
            sources = [line for line in lines if line['axis'] == axis and abs(line['normal']-normal) <= cfg.normal_tolerance_px
                       and line['end'] > start and line['start'] < end]
            support = interval_union([(s['start'], s['end']) for s in sources], start, end)
            coverage = sum(b-a for a, b in support)/(end-start)
            gaps = interval_gaps(support, start, end)
            seed_normal = x if axis == 'V' else y
            nearer = [line['id'] for line in lines if line['axis'] == axis
                and layer_role(line.get('layers', [])) == 'STRUCTURAL_BOUNDARY_HINT'
                and cfg.normal_tolerance_px < abs(line['normal']-normal) < text_height*1.6
                and abs(line['normal']-seed_normal) < abs(normal-seed_normal)
                and min(normal, seed_normal) < line['normal'] < max(normal, seed_normal)
                and max(0, min(end, line['end'])-max(start, line['start'])) > (end-start)*.45]
            far_faces.extend(nearer)
            edges.append({'side': side, 'axis': axis, 'normal': normal, 'start': start, 'end': end,
                'points_native': axis_points(axis, normal, start, end), 'observed_intervals': support,
                'inferred_intervals': gaps, 'observed_fraction': coverage,
                'nearer_parallel_face_ids': nearer,
                'source_line_ids': [s['id'] for s in sources], 'source_layers': sorted({layer for s in sources for layer in s.get('layers', [])})})
        conflicts = [label['id'] for label in labels if left < label['seed'][0] < right and top < label['seed'][1] < bottom
                     and np.linalg.norm(np.asarray(label['seed'])-seed) > text_height*1.5]
        inferred_length = sum(sum(b-a for a, b in edge['inferred_intervals']) for edge in edges)
        perimeter = 2*(width+height)
        crossing = [line['id'] for line in lines if
            (line['axis'] == 'H' and top+text_height < line['normal'] < bottom-text_height and max(0, min(right, line['end'])-max(left, line['start'])) > width*.75)
            or (line['axis'] == 'V' and left+text_height < line['normal'] < right-text_height and max(0, min(bottom, line['end'])-max(top, line['start'])) > height*.75)]
        score = 5*sum(edge['observed_fraction'] for edge in edges) - 8*len(conflicts) - 3*len(crossing) - 5*len(far_faces) - perimeter/(text_height*20)
        supported_sides = sum(edge['observed_fraction'] >= cfg.minimum_supported_side_fraction for edge in edges)
        acceptable = supported_sides >= 3 and inferred_length/perimeter <= cfg.maximum_inferred_perimeter_fraction and not conflicts and not crossing and not far_faces
        ranked.append({'bbox_native': [left, top, right, bottom], 'polygon_native': ring_from_box([left, top, right, bottom]),
            'edges': edges, 'ranking_score': float(score), 'ranking_is_probability': False,
            'inferred_perimeter_fraction': inferred_length/perimeter, 'supported_sides': supported_sides,
            'nearer_parallel_face_ids': sorted(set(far_faces)),
            'other_label_ids_inside': conflicts, 'interior_crossing_line_ids': crossing, 'acceptable_geometry_proposal': acceptable})
    if not ranked:
        return {'status': 'PARTIAL_BOUNDARY', 'reason': 'NO_NONDEGENERATE_CLOSURE', 'polygon_native': None, 'alternatives': []}
    ranked.sort(key=lambda value: (value['acceptable_geometry_proposal'], value['ranking_score']), reverse=True)
    best = ranked[0]
    return {**best, 'status': 'REGION_PROPOSED' if best['acceptable_geometry_proposal'] else 'UNRESOLVED',
        'reason': 'CONDITIONAL_FRAGMENT_CLOSURE_NOT_ARCHITECTURAL_GT' if best['acceptable_geometry_proposal'] else 'CONFLICT_OR_INSUFFICIENT_BOUNDARY_SUPPORT',
        'alternatives': [{k: v for k, v in item.items() if k != 'edges'} for item in ranked[1:4]]}


def _line_visibility(gray, points, radius=1, distance=None):
    if gray.size == 0:
        return 0.0
    if distance is None:
        distance = cv2.distanceTransform((gray >= 245).astype(np.uint8), cv2.DIST_L2, 5)
    count = max(3, int(np.linalg.norm(np.diff(np.asarray(points), axis=0)))+1)
    samples = np.linspace(points[0], points[1], count)
    x = np.rint(samples[:, 0]).astype(int)
    y = np.rint(samples[:, 1]).astype(int)
    valid = (x >= 0) & (x < gray.shape[1]) & (y >= 0) & (y < gray.shape[0])
    return float(np.mean(distance[y[valid], x[valid]] <= radius)) if valid.any() else 0.0


def _window(seed, initial, binding, height, cfg):
    if initial.get('bbox_native'):
        box = initial['bbox_native']
        margin = cfg.context_margin_text_heights*height
        window = [box[0]-margin, box[1]-margin, box[2]+margin, box[3]+margin]
    else:
        radius = min(binding['width'], binding['height'])*cfg.search_radius_page_fraction
        window = [seed[0]-radius, seed[1]-radius, seed[0]+radius, seed[1]+radius]
    scope=binding.get('drawing_region_bbox_native',[0,0,binding['width'],binding['height']])
    return [max(math.floor(scope[0]), math.floor(window[0])), max(math.floor(scope[1]), math.floor(window[1])),
            min(math.ceil(scope[2]), math.ceil(window[2])), min(math.ceil(scope[3]), math.ceil(window[3]))]


def reobserve(pdf_bytes, binding, window, text_height, request_id, cfg=None, *, expected_role="Ref"):
    """Actually reread own PDF paths and rerender a higher-resolution local view."""
    cfg = cfg or RestorationSettings()
    if expected_role not in ('Ref','Target') or binding.get('source_role', binding.get('role')) != expected_role or digest(pdf_bytes) != binding['source_sha256']:
        raise ValueError('Reobservation must use the same original Reference PDF')
    with pymupdf.open(stream=pdf_bytes, filetype='pdf') as document:
        page = document[binding['page']-1]
        if page.rotation:
            return {'status': 'UNSUPPORTED_ROTATED_PAGE_REOBSERVATION', 'lines': [], 'request_id': request_id}, None
        zoom = binding['extraction_zoom']
        high_zoom = min(4.0, zoom*cfg.reobserve_zoom_factor)
        x0, y0, x1, y1 = window
        pix = page.get_pixmap(matrix=pymupdf.Matrix(high_zoom, high_zoom),
            clip=pymupdf.Rect(x0/zoom, y0/zoom, x1/zoom, y1/zoom), colorspace=pymupdf.csRGB, alpha=False)
        gray = cv2.cvtColor(np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, pix.n), cv2.COLOR_RGB2GRAY)
        visible_distance = cv2.distanceTransform((gray >= 245).astype(np.uint8), cv2.DIST_L2, 5)
        matrix = np.asarray(pdf_to_native_matrix(page, zoom))
        vector = vector_observations(page, matrix)
        scale = high_zoom/zoom
        accepted, rejected = [], []
        for source in vector['segments']:
            clipped = clip_segment(source['points'], window)
            if clipped is None:
                continue
            axial = axis_segment(clipped, Settings().axial_angle_degrees)
            if axial is None:
                continue
            axis, normal, start, end = axial
            if end-start < text_height*cfg.minimum_line_text_heights:
                continue
            if max(p[0] for p in source['points']) < x0 or min(p[0] for p in source['points']) > x1 or max(p[1] for p in source['points']) < y0 or min(p[1] for p in source['points']) > y1:
                continue
            role = layer_role([source.get('layer', '')])
            if role not in {'STRUCTURAL_BOUNDARY_HINT', 'WINDOW_ASSEMBLY_BOUNDARY_HINT'}:
                rejected.append({'source_primitive_id': source['id'], 'layer': source.get('layer', ''),
                    'reason': role, 'points_native': source['points']})
                continue
            points = (np.asarray(clipped)*scale-[pix.x, pix.y]).tolist()
            visibility = _line_visibility(gray, points, radius=max(1, scale), distance=visible_distance)
            if visibility < .6:
                rejected.append({'source_primitive_id': source['id'], 'layer': source.get('layer', ''), 'reason': 'INSUFFICIENT_RERENDER_VISIBILITY', 'points_native': source['points']})
                continue
            accepted.append({'id': request_id+':'+source['id'], 'axis': axis, 'normal': normal, 'start': start, 'end': end,
                'points': clipped, 'native_unclipped_points': source['points'], 'context_clipped': clipped != source['points'],
                'stroke_px': source['stroke_px'], 'layers': [source.get('layer', '')],
                'source_primitive_id': source['id'], 'source_role_hint': role, 'high_resolution_visible_fraction': visibility,
                'request_id': request_id, 'source_evidence': {'source_sha256': binding['source_sha256'], 'page': binding['page'], 'path_id': source['path_id'], 'path_index': source['path_index']}})
        # Reuse LSD as additional observation; width is detector support, not wall thickness.
        lsd = cv2.createLineSegmentDetector(cv2.LSD_REFINE_STD).detect(gray)[0]
        raster_lines = [] if lsd is None else [((line.reshape(2, 2)+[pix.x, pix.y])/scale).tolist() for line in lsd[:6000]]
        arcs = [arc for arc in vector['arcs'] if x0 <= arc['center'][0] <= x1 and y0 <= arc['center'][1] <= y1]
        return {'status': 'COMPLETED', 'request_id': request_id, 'lines': accepted, 'rejected_lines': rejected,
            'raster_segments_native': raster_lines, 'arcs': arcs, 'window_native': window,
            'rerender_zoom': high_zoom, 'rerender_size': [pix.width, pix.height],
            'rerender_to_native': [[1/scale, 0, pix.x/scale], [0, 1/scale, pix.y/scale], [0, 0, 1]],
            'rerender_sha256': digest(pix.tobytes('png')), 'PDF_reread': True,
            'raster_lsd_omitted_due_to_limit': max(0, (0 if lsd is None else len(lsd))-6000),
            'architectural_truth_verified': False, 'layer_role_is_hint_not_GT': True}, pix.tobytes('png')


def gap_assessments(candidate, reobservation, text_height, settings=None):
    cfg = settings or RestorationSettings()
    assessments = []
    for edge in candidate.get('edges', []):
        for index, (a, b) in enumerate(edge['inferred_intervals']):
            if b-a < 1:
                continue
            arcs = []
            nearby = []
            for arc in reobservation.get('arcs', []):
                if not (65 <= arc.get('angle_degrees', 0) <= 115) or arc.get('radius', 0) < text_height:
                    continue
                center = arc['center']
                normal, tangent = (center[0], center[1]) if edge['axis'] == 'V' else (center[1], center[0])
                if abs(normal-edge['normal']) <= text_height*1.5 and a-text_height <= tangent <= b+text_height:
                    evidence = {'source_primitive_id': arc['id'], 'layer': arc.get('layer', ''),
                        'center_native': center, 'radius_native_px': arc['radius'],
                        'polyline_native': arc['polyline'], 'jamb_and_leaf_independently_verified': False}
                    nearby.append(evidence)
                    ratio = (b-a)/arc['radius']
                    if cfg.gap_arc_radius_ratio_min <= ratio <= cfg.gap_arc_radius_ratio_max and layer_role([arc.get('layer', '')]) == 'OPENING_SYMBOL_HINT':
                        arcs.append(evidence)
            assessments.append({'side': edge['side'], 'interval': [a, b],
                'segment_native': axis_points(edge['axis'], edge['normal'], a, b),
                'interpretation': 'DOOR_CANDIDATE' if arcs else 'UNKNOWN', 'arc_evidence': arcs,
                'nearby_arc_evidence': nearby, 'tiny_gap_is_not_automatically_a_door': True,
                'status': 'PROPOSED', 'meaning': 'Inferred spatial separator, not an observed physical Wall',
                'physical_wall_existence': 'UNKNOWN'})
    return assessments


def local_relation_attempts(cases, text_height):
    attempts = []
    valid = [case for case in cases if case['after']['status'] == 'REGION_PROPOSED']
    for a, b in itertools.combinations(valid, 2):
        for first in a['after'].get('edges', []):
            for second in b['after'].get('edges', []):
                if first['axis'] != second['axis'] or abs(first['normal']-second['normal']) > text_height:
                    continue
                if (first['side'], second['side']) not in {('left', 'right'), ('right', 'left'), ('top', 'bottom'), ('bottom', 'top')}:
                    continue
                start, end = max(first['start'], second['start']), min(first['end'], second['end'])
                if end-start <= text_height*1.5:
                    continue
                openings = interval_union(first['inferred_intervals']+second['inferred_intervals'], start, end)
                # A single missing face can be a missed wall; both sides must
                # expose a compatible opening interval for a Connection proposal.
                shared = []
                for x0, x1 in first['inferred_intervals']:
                    for y0, y1 in second['inferred_intervals']:
                        lo, hi = max(start, x0, y0), min(end, x1, y1)
                        if hi-lo > text_height:
                            shared.append([lo, hi])
                attempts.append({'source_space_id': a['id'], 'target_space_id': b['id'],
                    'kind': 'Connection' if shared else 'Adjacency', 'mode': 'Unknown',
                    'existence': 'UNKNOWN', 'geometry_status': 'PROPOSED',
                    'source_edge': first['side'], 'target_edge': second['side'],
                    'normal_distance_native_px': abs(first['normal']-second['normal']),
                    'shared_boundary_range_native': [start, end], 'candidate_gap_intervals_native': shared,
                    'one_sided_gap_intervals_native': openings,
                    'physical_crossing_path_verified': False, 'both_space_extents_verified': False,
                    'reason': 'COMPATIBLE_UNOBSERVED_INTERVALS_ON_TWO_PROPOSED_REGION_SIDES' if shared else 'ALIGNED_BOUNDARIES_DO_NOT_PROVE_ACCESS'})
    return attempts


def restore_reference(pdf_bytes, image_png, source_binding, ocr_observations, settings=None, ocr_detection_polygons=None):
    """Full-page observation scope; per-label context is automatic, never user ROI."""
    started = time.perf_counter()
    run_id = source_binding.get('analysis_run_id') or uuid.uuid4().hex
    cfg = settings or RestorationSettings()
    if not 1 <= cfg.maximum_labels <= 128 or not 0 <= cfg.maximum_reobservations <= 48 or not 1 <= cfg.side_alternatives <= 6:
        raise ValueError('Invalid bounded restoration budget')
    binding = copy.deepcopy(source_binding)
    if binding.get('source_role', binding.get('role')) != 'Ref':
        raise ValueError('Reference-only input; Target data cannot correct Ref')
    if binding['width']*binding['height'] > 8_000_000:
        raise ValueError('Reference raster exceeds whole-page work budget; use a lower extraction scale or a tiled adapter')
    if digest(pdf_bytes) != binding['source_sha256'] or digest(image_png) != binding['image_sha256']:
        raise ValueError('PDF/image SHA mismatch')
    rendered = render_page(pdf_bytes, binding['page'], binding['extraction_zoom'])
    if digest(rendered.png) != binding['image_sha256'] or (rendered.width, rendered.height) != (binding['width'], binding['height']):
        raise ValueError('Page/scale/native frame mismatch')
    image = cv2.imdecode(np.frombuffer(image_png, np.uint8), cv2.IMREAD_COLOR)
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    texts = []
    for observation in ocr_observations:
        if observation.get('frame_id') != binding['frame_id']:
            raise ValueError('OCR input frame mismatch')
        polygon = np.asarray(observation['polygon_native'], float)
        if not np.isfinite(polygon).all() or (polygon[:, 0] < 0).any() or (polygon[:, 0] > binding['width']).any() or (polygon[:, 1] < 0).any() or (polygon[:, 1] > binding['height']).any():
            raise ValueError('OCR coordinates outside source')
        scope=binding.get('drawing_region_bbox_native',[0,0,binding['width'],binding['height']])
        center=polygon.mean(axis=0)
        if not (scope[0]<=center[0]<=scope[2] and scope[1]<=center[1]<=scope[3]):continue
        term = resolve_space_term(observation.get('text', ''))
        if observation.get('text', '').strip().upper() == 'PS':
            term = ('PS', 'PipeShaft', None, None)
        if term:
            texts.append({'id': observation['id'], 'name': term[0], 'space_type': term[1],
                'seed': np.mean(polygon, axis=0).tolist(), 'text_height': float(np.ptp(polygon[:, 1])), 'observation': observation})
    text_height = float(np.median([value['text_height'] for value in texts])) if texts else 16.0
    masks = raster_observations(gray, ocr_detection_polygons if ocr_detection_polygons is not None else [o['polygon_native'] for o in ocr_observations], text_height, Settings())
    with pymupdf.open(stream=pdf_bytes, filetype='pdf') as document:
        page = document[binding['page']-1]
        vector = vector_observations(page, pdf_to_native_matrix(page, binding['extraction_zoom']))
    if binding.get('drawing_region_bbox_native'):
        scoped=[]
        for line in vector['segments']:
            clipped=clip_segment(line['points'],binding['drawing_region_bbox_native'])
            if clipped is not None:scoped.append({**line,'points':clipped,'native_unclipped_points':line['points'],'context_clipped':clipped!=line['points']})
        vector['segments']=scoped
    initial_lines, initial_filter = merge_lines(vector['segments'], masks, text_height, Settings(), 'pdf')
    cases, requests, assets = [], [], {}
    for index, label in enumerate(texts[:cfg.maximum_labels]):
        identity = f'Ref:{run_id}:space:{index:03d}'
        seed = label['seed']
        radius = min(binding['width'], binding['height'])*cfg.search_radius_page_fraction
        near = [line for line in initial_lines if abs(line['normal']-(seed[1] if line['axis'] == 'H' else seed[0])) <= radius]
        before = propose_region(near, seed, text_height, texts, cfg)
        request_id = f'Ref:{run_id}:reobserve:{index:03d}'
        reasons = ['V04_REVIEW_FRAGMENT_CLOSURE', 'BOUNDARY_LAYER_AND_LOCAL_RASTER_REOBSERVATION']
        profile, question = 'SPATIAL', 'REVIEW_FRAGMENT_CLOSURE'
        if before.get('other_label_ids_inside'):
            reasons.append('MULTIPLE_LABELS_IN_PROPOSED_REGION')
        before_sources = {line['id']: line for line in near}
        if any(layer_role(before_sources[line_id].get('layers', [])) not in {'STRUCTURAL_BOUNDARY_HINT', 'WINDOW_ASSEMBLY_BOUNDARY_HINT'}
               for edge in before.get('edges', []) for line_id in edge['source_line_ids']):
            reasons.append('WALL_VS_ANNOTATION_OR_FIXTURE')
            profile, question = 'GEOMETRIC', 'WALL_VS_DIMENSION_OR_FURNITURE'
        elif not before.get('polygon_native'):
            profile, question = 'SPATIAL', 'GROUND_SPACE_BOUNDARIES'
        if question not in ontology.ENGINE_PROFILES[profile]['questions']:
            raise ValueError('Reobservation question is not in the shared ontology profile')
        if index >= cfg.maximum_reobservations:
            cases.append({'id': identity, 'label': label, 'before': before, 'after': before,
                'reobservation_status': 'BUDGET_EXCEEDED', 'policy': 'NO_UNEXECUTED_CORRECTION_CLAIMED'})
            continue
        window = _window(seed, before, binding, text_height, cfg)
        fresh, png = reobserve(pdf_bytes, binding, window, text_height, request_id, cfg)
        after = propose_region(fresh['lines'], seed, text_height, texts, cfg) if fresh['status'] == 'COMPLETED' else {'status': 'UNRESOLVED', 'polygon_native': None, 'reason': fresh['status']}
        if after.get('bbox_native'):
            box = after['bbox_native']
            if min(abs(box[0]-window[0]), abs(box[1]-window[1]), abs(box[2]-window[2]), abs(box[3]-window[3])) <= cfg.normal_tolerance_px:
                after.update(status='PARTIAL_BOUNDARY', acceptable_geometry_proposal=False, reason='REOBSERVATION_CONTEXT_BOUNDARY_CONTACT')
        if label['space_type'] in {'LivingRoom', 'KitchenDining'}:
            after['reason'] = 'OPEN_FUNCTIONAL_SPACE_REQUIRES_SHARED_REGION_GROUNDING'
            after['status'] = 'UNRESOLVED'
            after['acceptable_geometry_proposal'] = False
        gaps = gap_assessments(after, fresh, text_height, cfg)
        after['extent_assessment'] = 'UNRESOLVED_POSSIBLE_NONRECTANGULAR_OR_OPEN_EXTENT' if any(
            edge['observed_fraction'] < .5 and any(gap['side'] == edge['side'] and gap['interpretation'] == 'UNKNOWN' for gap in gaps)
            for edge in after.get('edges', [])) else 'CONDITIONAL_CLOSURE_EXTENT_NOT_GT_VERIFIED'
        requests.append({'request_id': request_id, 'subject_id': identity, 'source_role': 'Ref', 'frame_id': binding['frame_id'],
            'reasons': reasons, 'native_context': window, 'status': fresh['status'], 'executed': True,
            'ontology_profile': profile, 'ontology_question': question, 'ontology_contract_sha256': ontology.contract_digest(),
            'question_resolution': 'PARTIAL_GEOMETRIC_REASSESSMENT_NOT_ARCHITECTURAL_VERIFICATION',
            'engine': 'PDF_VECTOR_AND_LOCAL_RASTER', 'new_VLM_inference': False})
        if png:
            assets[request_id] = png
        cases.append({'id': identity, 'label': label, 'before': before, 'after': after,
            'reobservation': fresh, 'request_id': request_id, 'claim_status': 'PROPOSED',
            'gap_assessments': gaps, 'full_space_extent_verified': False,
            'unresolved_boundary_interpretations': sum(g['interpretation'] == 'UNKNOWN' for g in gaps),
            'correction_kind': 'REPLACED_CONDITIONAL_REGION' if before.get('bbox_native') != after.get('bbox_native') else 'REASSESSED_SAME_GEOMETRY',
            'prior_result_preserved': True})
    relation_attempts = local_relation_attempts(cases, text_height)
    return {'schema_version': 'reference_understanding_bundle/1', 'run_id': run_id, 'source_role': 'Ref', 'source_binding': binding,
        'ontology_contract': {'version': ontology.VERSION, 'sha256': ontology.contract_digest()},
        'algorithm': asdict(cfg), 'analysis_scope': {'kind': 'USER_SELECTED_DRAWING_REGION_WITH_LOCAL_CONTEXTS' if binding.get('drawing_region_bbox_native') else 'WHOLE_REFERENCE_PAGE_OBSERVATIONS_WITH_AUTOMATIC_LOCAL_CONTEXTS',
            'drawing_region_bbox_native':binding.get('drawing_region_bbox_native'),'scope_selection':binding.get('drawing_region_selection'),
            'automatic_layout_is_not_verified':True,'user_ROI_generated_or_modified': False, 'page_native_bbox': [0, 0, binding['width'], binding['height']]},
        'ocr_observations': copy.deepcopy(ocr_observations), 'initial_lines': initial_lines, 'initial_line_filter': initial_filter,
        'cases': cases, 'reobservation_requests': requests, 'relation_attempts': relation_attempts,
        'coverage': {'recognized_space_labels': len(texts), 'processed_space_labels': len(cases), 'unprocessed_label_ids': [t['id'] for t in texts[cfg.maximum_labels:]],
            'native_page_size': [binding['width'], binding['height']], 'space_inventory_complete': False,
            'geometric_orientation_supported': 'AXIAL_H_V_ONLY', 'new_OCR_run': False, 'new_VLM_run': False,
            'Ref_Target_alignment_performed': False, 'change_detection_performed': False, 'GT_metrics_computed': False},
        'summary': {'baseline_region_proposals': sum(c['before']['status'] == 'REGION_PROPOSED' for c in cases),
            'reobserved_region_proposals': sum(c['after']['status'] == 'REGION_PROPOSED' for c in cases),
            'reobservation_count': len(requests),
            'bbox_revisions': sum(c['before'].get('bbox_native') != c['after'].get('bbox_native') for c in cases),
            'unresolved_after': sum(c['after']['status'] != 'REGION_PROPOSED' for c in cases),
            'conditional_connection_proposals': sum(a['kind'] == 'Connection' for a in relation_attempts),
            'verified_architectural_spaces': 0},
        'runtime_seconds': time.perf_counter()-started}, assets
