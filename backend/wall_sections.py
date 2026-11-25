"""Read dark, solid PDF strokes that form paired wall faces and transverse returns."""
from collections import defaultdict
from dataclasses import dataclass
import re

import cv2
import numpy as np
import pymupdf

from backend.structural_alignment import automatic_anchors, transform

from poc.space_grounding_geometry import pdf_to_native_matrix, transform_points


@dataclass(frozen=True)
class Stroke:
    axis: int
    normal: float
    lo: float
    hi: float
    refs: tuple[str, ...]
    wall_layer: bool

    def points(self, lo=None, hi=None):
        points = [[self.lo if lo is None else lo, self.normal],
                  [self.hi if hi is None else hi, self.normal]]
        return points if self.axis == 0 else [p[::-1] for p in points]


@dataclass(frozen=True)
class Section:
    first: Stroke
    second: Stroke
    returns: tuple
    id: str

    @property
    def axis(self):
        return self.first.axis

    @property
    def length(self):
        return min(self.first.hi, self.second.hi) - max(self.first.lo, self.second.lo)

    @property
    def width(self):
        return self.second.normal - self.first.normal

    @property
    def refs(self):
        return list(dict.fromkeys(self.first.refs + self.second.refs + tuple(
            r for cap in self.returns if cap for r in cap.refs)))


def read_strokes(pdf, page):
    """Extract each drawing independently, in rendered native units."""
    strokes = []
    with pymupdf.open(stream=pdf, filetype='pdf') as document:
        native_page = document[page.page_number-1]
        matrix = pdf_to_native_matrix(native_page, page.zoom)
        for index, path in enumerate(native_page.get_drawings()):
            width = path.get('width') or 0
            color = path.get('color')
            wall = bool(re.fullmatch(r'A-WAL[0-9]*', (path.get('layer') or '').split('|')[-1], re.I))
            if (not color or max(color) > .46 or width < (.45 if wall else .65)
                    or (path.get('stroke_opacity') or 0) < .8
                    or path.get('dashes') not in ('[] 0', '[] 0.0', None)
                    or (path.get('layer') and not wall)):
                continue
            for item_index, item in enumerate(path['items']):
                if item[0] != 'l':
                    continue
                a, b = np.asarray(transform_points([list(item[1]), list(item[2])], matrix))
                axis = int(abs(b[1]-a[1]) > abs(b[0]-a[0]))
                along = sorted([a[axis], b[axis]])
                if abs(a[1-axis]-b[1-axis]) > .15*page.zoom or along[1]-along[0] < 2*page.zoom:
                    continue
                strokes.append(Stroke(axis,float((a[1-axis]+b[1-axis])/2),*map(float,along),
                                      (f'pdf_path:{index}:{item_index}:0',),wall))
    return strokes


def merged_strokes(strokes, zoom):
    """Combine contiguous collinear strokes while retaining their source IDs."""
    axes = defaultdict(list)
    for s in strokes:
        axes[s.axis].append(s)
    for axis, group in axes.items():
        bands = []
        for s in sorted(group,key=lambda v:v.normal):
            if not bands or s.normal-bands[-1][0].normal > .20*zoom:
                bands.append([])
            bands[-1].append(s)
        for band in bands:
            previous = None
            for s in sorted(band,key=lambda v:v.lo):
                if not previous or s.lo > previous.hi + .36*zoom:
                    if previous:
                        yield previous
                    previous = s
                else:
                    previous = Stroke(axis, previous.normal, previous.lo, max(previous.hi,s.hi),
                                      previous.refs+s.refs, previous.wall_layer or s.wall_layer)
            if previous:
                yield previous


def sections(strokes, zoom, scope):
    by_axis = [[], []]
    for stroke in merged_strokes(strokes, zoom):
        a,b = stroke.points()
        if any(max(a[i],b[i]) < scope[i] or min(a[i],b[i]) > scope[i+2] for i in (0,1)):
            continue
        by_axis[stroke.axis].append(stroke)
    output = []
    eps = .6*zoom
    for axis, group in enumerate(by_axis):
        ordered = sorted(group,key=lambda s:s.normal)
        perpendicular = by_axis[1-axis]
        for i, a in enumerate(ordered):
            if a.hi-a.lo < 12*zoom:
                continue
            for b in ordered[i+1:]:
                width = b.normal-a.normal
                if width > 24*zoom:
                    break
                overlap = min(a.hi,b.hi)-max(a.lo,b.lo)
                if width < 4*zoom or overlap < max(12*zoom, .60*max(a.hi-a.lo,b.hi-b.lo)):
                    continue
                ends = []
                for ea, eb in ((a.lo,b.lo),(a.hi,b.hi)):
                    caps = [s for s in perpendicular if abs(s.normal-ea)<=eps and abs(s.normal-eb)<=eps
                            and abs(s.lo-a.normal)<=eps and abs(s.hi-b.normal)<=eps]
                    ends.append(caps[0] if len(caps)==1 else None)
                output.append(Section(a,b,tuple(ends),f'wall-faces:{len(output)+1}'))
                if len(output)>256:
                    return output
    return output


def paired_sections(ref_sections, target_sections, guide, zoom):
    """Rank by the common support of both faces, with reciprocal ambiguity checks."""
    by_ref = defaultdict(list)
    by_target = defaultdict(list)
    for t in target_sections:
        mapped = np.array([transform(s.points(),guide) for s in (t.first,t.second)])
        along = mapped[:,:,t.axis]
        normal = mapped[:,:,1-t.axis].mean(axis=1)
        for r in ref_sections:
            if r.axis != t.axis:
                continue
            ref_faces = (r.first,r.second)
            face_error = np.abs(normal-[s.normal for s in ref_faces])
            if face_error.max() > 10*zoom:
                continue
            starts = np.array([s.lo for s in ref_faces])
            ends = np.array([s.hi for s in ref_faces])
            overlap = np.maximum(0.,np.minimum(along[:,1],ends)-np.maximum(along[:,0],starts))
            fraction = overlap/np.maximum(ends-starts,along[:,1]-along[:,0])
            if fraction.min() < .60:
                continue
            score = float(fraction.mean()-face_error.mean()/(20*zoom))
            entry = (score,r,t)
            by_ref[r.id].append(entry)
            by_target[t.id].append(entry)
    for entries in (*by_ref.values(),*by_target.values()):
        entries.sort(key=lambda e:-e[0])
    for options in by_target.values():
        best = options[0]
        reverse = by_ref[best[1].id]
        if (reverse[0] is best and all(len(values)==1 or values[0][0]-values[1][0]>=.12
                                       for values in (options,reverse))):
            yield best[1:]


def raster_guide(grays, polygon, zooms):
    matches, _ = automatic_anchors(*grays, polygon)
    if len(matches)<5:
        return None, len(matches), 0
    src = np.float32([a['target'] for a in matches])
    dst = np.float32([a['ref'] for a in matches])
    affine, inliers = cv2.estimateAffinePartial2D(src, dst, ransacReprojThreshold=1.5*zooms[0])
    if affine is None:
        return None, len(matches), 0
    hit = src[inliers.ravel()!=0]
    scale = np.linalg.norm(affine[:,0])/(zooms[0]/zooms[1])
    if (len(hit)<max(5, .65*len(matches)) or min(np.ptp(hit,axis=0))<30*zooms[1]
            or not .80<=scale<=1.25 or abs(affine[1,0])>.035*abs(affine[0,0])):
        return None, len(matches), len(hit)
    return np.vstack([affine,[0,0,1]]), len(matches), len(hit)


def anchor_pairs(paired, guide, zoom):
    """Reserve candidates with real shape differences for the comparison pass."""
    result = []
    for r,t in paired:
        faces_r = np.array([s.points() for s in (r.first,r.second)])
        faces_t = np.array([s.points() for s in (t.first,t.second)])
        projected = transform(faces_t,guide)
        if (np.max(np.abs(projected[:,:,t.axis]-faces_r[:,:,t.axis]))>1.5*zoom
                or abs(r.width-t.width*np.linalg.norm(guide[:2,0]))>zoom):
            continue
        result.append(dict(id=f'V{len(result)+1:03}', axis=t.axis, ref_parent=r.id, target_parent=t.id,
                           ref_fragment=r.id,target_fragment=t.id,ref_segment=faces_r[0].tolist(),
                           ref_other_face=faces_r[1].tolist(),target_segment=faces_t[0].tolist(),
                           target_other_face=faces_t[1].tolist(),interval_stability='UNTESTED',
                           physical_endpoints='UNKNOWN', evidence={'Ref':r.refs,'Target':t.refs}))
    return result


def ink_support(gray, points):
    """Sample the central segment, avoiding its perpendicular face junctions."""
    samples = np.linspace(*points, 31)[5:-5]
    tangent = np.subtract(points[1],points[0])
    normal = np.array([-tangent[1],tangent[0]])/np.linalg.norm(tangent)
    coordinates = np.rint(samples[:,None,:]+np.arange(-1,2)[None,:,None]*normal).astype(int)
    x,y = coordinates[:,:,0],coordinates[:,:,1]
    if np.any(x<0) or np.any(y<0) or np.any(x>=gray.shape[1]) or np.any(y>=gray.shape[0]):
        return None
    return float(np.mean(np.min(gray[y,x],axis=1)<160))


def local_witnesses(box, parent, coverage, zoom):
    center = np.asarray(box).reshape(2,2).mean(axis=0)
    centers = []
    for tile in coverage['tiles']:
        if tile['status']!='SUPPORTED':
            continue
        bounds = np.array(tile['target_bbox_native']).reshape(2,2)
        if np.all(bounds[0] <= parent[2:]+zoom*4) and np.all(bounds[1] >= parent[:2]-zoom*4):
            continue
        tile_center = bounds.mean(axis=0)
        distance = np.linalg.norm(center-tile_center)
        if distance <= 85*zoom:
            centers.append((distance,tile_center,tile))
    centers.sort(key=lambda v:v[0])
    if not centers:
        return []
    first = centers[0]
    for second in centers[1:]:
        # Independent nearby patches from different directions around the observation.
        va,vb = first[1]-center,second[1]-center
        if np.dot(va,vb) <= .8*np.linalg.norm(va)*np.linalg.norm(vb):
            return [first[2],second[2]]
    return []


def section_changes(pages, strokes, alignment):
    """A cap displacement requires reciprocal geometry, positive ink and loss at the old locus."""
    if not alignment.get('mapping') or not alignment.get('matrix'):
        return []
    matrix = np.asarray(alignment['matrix'])
    inverse = np.asarray(alignment['inverse_matrix'])
    coverage = alignment['coverage']
    hulls = [np.float32(h) for h in coverage['directional_validation_hulls_target']]
    if not hulls or abs(matrix[1,0]) > .01*np.linalg.norm(matrix[:2,0]):
        return []
    limit = coverage.get('max_distance_outside_holdout_hull_px',0)
    rz,tz = [p.zoom for p in pages]
    tol = alignment['settings']['normal_tolerance_ref_px']
    ref_scope,target_scope = [np.asarray(s['scope']) for s in alignment['sources']]
    polygon = np.float32(alignment['target_roi']['vertices'])
    grays = [cv2.imdecode(np.frombuffer(p.png,np.uint8),0) for p in pages]
    stocks = [sections(s,z,scope) for s,z,scope in zip(strokes,(rz,tz),(ref_scope,target_scope))]
    if max(map(len,stocks))>256:
        return []
    # IDs are local to the search windows, so exclude actual face evidence across windows.
    fit_refs = {ref for p in alignment['pairs'] if p['usage']=='FIT' for ref in p.get('evidence',{}).get('Target',[])}
    fitted = [p for p in alignment['pairs'] if p['usage']=='FIT']
    changes = []
    for r,t in paired_sections(*stocks,matrix,rz):
        if not (r.first.wall_layer and r.second.wall_layer or t.first.wall_layer and t.second.wall_layer):
            continue
        if fit_refs.intersection(t.refs):
            continue
        ref_faces = np.array([s.points() for s in (r.first,r.second)])
        tar_faces = np.array([s.points() for s in (t.first,t.second)])
        shares_fit_parent = False
        for anchor in fitted:
            if anchor['axis'] != t.axis:
                continue
            anchor_faces = np.asarray([anchor['target_segment'],anchor['target_other_face']])
            distances = np.abs(anchor_faces[:,:,1-t.axis].mean(axis=1)
                               - tar_faces[:,:,1-t.axis].mean(axis=1))
            if distances.max()>1.5*tz:
                continue
            spans = np.sort(anchor_faces[:,:,t.axis],axis=1)
            if np.any(np.minimum(spans[:,1],tar_faces[:,1,t.axis]) >=
                      np.maximum(spans[:,0],tar_faces[:,0,t.axis])):
                shares_fit_parent = True
                break
        if shares_fit_parent:
            continue
        registered = transform(tar_faces,matrix)
        if np.max(np.abs(registered[:,:,1-t.axis]-ref_faces[:,:,1-t.axis])) > tol:
            continue
        parent_points = np.concatenate((tar_faces.reshape(-1,2),transform(ref_faces.reshape(-1,2),inverse)))
        parent = np.r_[parent_points.min(axis=0),parent_points.max(axis=0)]
        cap_changes = []
        for i,(rc,tc) in enumerate(zip(r.returns,t.returns)):
            if not rc or not tc or not (rc.wall_layer or tc.wall_layer):
                continue
            old_ref = rc.points()
            new_tar = tc.points()
            new_ref = transform(new_tar,matrix)
            old_tar = transform(old_ref,inverse)
            delta = float(np.mean(new_ref[:,t.axis])-rc.normal)
            if abs(delta)<=tol:
                continue
            points = np.concatenate([new_tar,old_tar])
            lower = np.floor(points.min(axis=0))-1
            upper = np.ceil(points.max(axis=0))+1
            x,y = lower.tolist(); u,v = upper.tolist()
            corners = [(x,y),(u,y),(u,v),(x,v)]
            if (any(cv2.pointPolygonTest(polygon,c,True)<1 for c in corners)
                or any(cv2.pointPolygonTest(h,c,True)<-limit for h in hulls for c in corners)
                or np.any(lower<target_scope[:2]) or np.any(upper>target_scope[2:])
                or np.any(np.min(transform(corners,matrix),axis=0)<ref_scope[:2])
                or np.any(np.max(transform(corners,matrix),axis=0)>ref_scope[2:])):
                continue
            tiles = local_witnesses([x,y,u,v],parent,coverage,tz)
            if not tiles:
                continue
            support = [ink_support(im,pts) for im,pts in
                       ((grays[0],old_ref),(grays[1],new_tar),(grays[0],new_ref),(grays[1],old_tar))]
            if any(s is None for s in support) or min(support[:2])<.85 or max(support[2:])>.40:
                continue
            stationary_r,stationary_t = r.returns[1-i],t.returns[1-i]
            span_delta = None
            if stationary_r and stationary_t:
                fixed = transform(stationary_t.points(),matrix)
                fixed_delta = abs(fixed[:,t.axis].mean()-stationary_r.normal)
                fixed_support = (ink_support(grays[0],stationary_r.points()),
                                 ink_support(grays[1],stationary_t.points()))
                if fixed_delta <= tol and all(v is not None and v>=.85 for v in fixed_support):
                    span_delta = float(np.linalg.norm(matrix[:2,0]) * abs(tc.normal-stationary_t.normal)
                                       - abs(rc.normal-stationary_r.normal))
            cap_changes.append(dict(end=i, span_delta_ref_px=span_delta, delta_ref_px=delta,target_bbox_native=[int(x),int(y),int(u),int(v)],
                                    old_cap_ref_native=old_ref,new_cap_target_native=new_tar,
                                    ink_support=support, raster_support_tiles=[tile['target_bbox_native'] for tile in tiles],
                                    evidence={'Ref':list(rc.refs),'Target':list(tc.refs)}))
        if not cap_changes:
            continue
        # A single changed cap does not certify the other end or the overall wall length.
        changes.append(dict(ref=r,target=t,caps=cap_changes))
    return changes


def adjoining_names(geometry, section, cap, zoom):
    """Associate names through observed boundary runs next to a wall face."""
    names = {}
    axis = section.axis
    face_tolerance = min(5*zoom, section.width*.50)
    for region in geometry['space_closure']['regions']:
        for run in region.get('boundary_runs',[]):
            if run.get('owner',0)<=0:
                continue
            points = np.asarray(run['points_native'],float)
            found = False
            for a,b in zip(points,points[1:]):
                lo,hi = sorted([a[axis],b[axis]])
                if hi-lo<2*zoom or cap.normal+12*zoom<lo or cap.normal-12*zoom>hi:
                    continue
                if any(max(abs(a[1-axis]-f.normal),abs(b[1-axis]-f.normal))<=face_tolerance
                       for f in (section.first,section.second)):
                    found = True
                    break
            if found:
                names[region['name']] = region['observation_id']
                break
    return names
