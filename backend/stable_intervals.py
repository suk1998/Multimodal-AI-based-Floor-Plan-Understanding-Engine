"""Propose interval identities from each drawing's own structural fragments and room labels."""
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
import math

import numpy as np

from backend.structural_anchor_alignment import frame


@dataclass(frozen=True)
class SearchPolicy:
    min_length_zoom_units: float = 8.0
    min_room_baseline_zoom_units: float = 25.0
    max_search_label_heights: float = 4.0
    max_angle_deg: float = 12.0
    max_thickness_ratio: float = 1.35
    ambiguous_score_gap: float = .20
    max_intervals_per_frame: int = 256


def intervals(bundle, policy):
    """Keep both visible faces. A cut at the analysis boundary has unknown continuation."""
    result = []
    counts = Counter()
    binding = bundle['source_binding']
    for parent in bundle['architectural_assessment']['structural_parents']:
        for f in parent['fragments']:
            if f['decision'] != 'ADMISSIBLE_GEOMETRIC_HYPOTHESIS' or f['boundary_truncated']:
                counts['ineligible_or_truncated'] += 1
                continue
            faces = np.array(f['boundary_faces_native'], float)
            mid, direction, _ = frame(faces.mean(axis=0))
            length = np.linalg.norm(faces[0, 1] - faces[0, 0])
            if length < policy.min_length_zoom_units * binding['extraction_zoom']:
                counts['short'] += 1
                continue
            axis = int(abs(direction[1]) > abs(direction[0]))
            # Canonical side in a nearly upright drawing; never pair physical endpoints.
            faces = sorted(faces, key=lambda s: float(s[:, 1-axis].mean()))
            result.append(dict(id=f['fragment_id'], parent=parent['parent_structure_id'], faces=faces,
                               mid=mid, axis=axis, direction=direction, width=f['thickness_px']))
    return result, dict(counts)


def unique_labels(bundle):
    by_name = defaultdict(list)
    for row in bundle['space_closure']['regions']:
        by_name[row['name']].append(row)
    return {name: rows[0] for name, rows in by_name.items() if len(rows) == 1}


def local_search(target, names, ref_labels, target_labels, zoom, policy):
    # Two text labels give a broad search neighborhood only. Neither enters the line solver.
    ordered = sorted(names, key=lambda n: (np.linalg.norm(target['mid']-target_labels[n]['seed_native']), n))
    for second in ordered[1:]:
        a = ordered[0]
        t0 = np.array(target_labels[a]['seed_native'])
        t1 = np.array(target_labels[second]['seed_native'])
        r0 = np.array(ref_labels[a]['seed_native'])
        r1 = np.array(ref_labels[second]['seed_native'])
        if min(np.linalg.norm(t1-t0)/zoom[1], np.linalg.norm(r1-r0)/zoom[0]) < policy.min_room_baseline_zoom_units:
            continue
        factor = complex(*(r1-r0)) / complex(*(t1-t0))
        if abs(math.degrees(np.angle(factor))) > policy.max_angle_deg or not .05 <= abs(factor) <= 20:
            continue
        center = complex(*r0) + factor * complex(*(target['mid']-t0))
        height = max(ref_labels[a]['text_height'], ref_labels[second]['text_height'], zoom[0]*4)
        return np.array([center.real, center.imag]), factor, policy.max_search_label_heights*height, [a, second]
    return None


def propose(ref, target, policy=SearchPolicy()):
    refs, rc = intervals(ref, policy)
    targets, tc = intervals(target, policy)
    rl, tl = unique_labels(ref), unique_labels(target)
    names = sorted(rl.keys() & tl.keys())
    audit = dict(policy=asdict(policy), available_intervals=[len(refs), len(targets)],
                 excluded_fragments=[rc, tc], common_unique_room_labels=names, held=[])
    if max(len(refs), len(targets)) > policy.max_intervals_per_frame:
        audit['reason'] = 'STRUCTURE_SEARCH_BUDGET_EXCEEDED'
        return [], audit
    options = defaultdict(list)
    reverse = defaultdict(list)
    zoom = [g['source_binding']['extraction_zoom'] for g in (ref, target)]
    for t in targets:
        local = local_search(t, names, rl, tl, zoom, policy)
        if local is None:
            continue
        center, factor, radius, labels = local
        td = factor * complex(*t['direction'])
        for r in refs:
            if r['axis'] != t['axis']:
                continue
            direction_error = abs(math.degrees(np.angle(td/complex(*r['direction']))))
            if min(direction_error, 180-direction_error) > policy.max_angle_deg:
                continue
            width_ratio = r['width'] / (t['width'] * abs(factor))
            if not 1/policy.max_thickness_ratio <= width_ratio <= policy.max_thickness_ratio:
                continue
            distance = float(np.linalg.norm(r['mid']-center))
            if distance > radius:
                continue
            score = distance/radius + abs(math.log(width_ratio))
            options[t['id']].append((score, r['id'], r, labels, distance))
            reverse[r['id']].append((score, t['id']))

    def unique_best(entries):
        entries.sort(key=lambda entry: entry[:2])
        return bool(entries) and (len(entries)==1 or entries[1][0]-entries[0][0] >= policy.ambiguous_score_gap)

    pairs = []
    for t in targets:
        entries = options[t['id']]
        if not unique_best(entries):
            reason = 'AMBIGUOUS' if entries else 'NO_UNIQUE_CONTEXT_MATCH'
        else:
            score, rid, r, labels, distance = entries[0]
            if unique_best(reverse[rid]) and reverse[rid][0][1] == t['id']:
                pairs.append(dict(id=f'I{len(pairs)+1:03}', ref_parent=r['parent'], target_parent=t['parent'],
                                  ref_fragment=rid, target_fragment=t['id'], axis=t['axis'],
                                  ref_segment=r['faces'][0].tolist(), target_segment=t['faces'][0].tolist(),
                                  ref_other_face=r['faces'][1].tolist(), target_other_face=t['faces'][1].tolist(),
                                  context_labels=labels,
                                  label_context={role:[{'name':name,'seed_native':table[name]['seed_native'],
                                                       'observation_id':table[name]['observation_id']} for name in labels]
                                                 for role,table in [('Ref',rl),('Target',tl)]},
                                  search_distance_ref_px=distance, search_score=score, identity_claim='PROPOSED',
                                  interval_stability='UNTESTED', physical_endpoints='UNKNOWN'))
                continue
            reason = 'REVERSE_MATCH_AMBIGUOUS'
        audit['held'].append(dict(target_fragment=t['id'], target_segment=t['faces'][0].tolist(),
                                  reason=reason, reference_alternatives=[e[1] for e in entries]))
    return pairs, audit


def allocate(pairs, zooms):
    """Cluster by either parent and by near-collinear support before splitting; no residual input."""
    groups = []
    def connected(a, b):
        for side, zoom in zip(('ref', 'target'), zooms):
            if a[side+'_parent'] == b[side+'_parent']:
                return True
            if a['axis'] == b['axis']:
                # Overlapping parallel-face envelopes may be fragments of one physical wall.
                axis = 1-a['axis']
                def span(p):
                    return sorted(np.mean(p[side+k], axis=0)[axis] for k in ('_segment', '_other_face'))
                lo, hi = span(a)
                other_lo, other_hi = span(b)
                if max(lo, other_lo) <= min(hi, other_hi)+2*zoom:
                    return True
        return False
    for pair in pairs:
        related = [g for g in groups if any(connected(pair, p) for p in g)]
        for group in related:
            groups.remove(group)
        groups.append([pair, *(p for g in related for p in g)])
    per_axis = Counter()
    for i, group in enumerate(sorted(groups, key=lambda group: min(p['id'] for p in group))):
        axis = group[0]['axis']
        usage = 'VALIDATION' if per_axis[axis] % 2 else 'FIT'
        per_axis[axis] += 1
        for p in group:
            p.update(usage=usage, parent_structure_id=f'parent-group-{i+1}')
