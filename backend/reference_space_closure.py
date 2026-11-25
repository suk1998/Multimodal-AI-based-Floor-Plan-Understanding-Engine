"""Partition hypotheses from observed faces on a single Reference frame.

The connected components are computed on a boundary graph rasterized at native
resolution. Raw ink, OCR boxes and the page border never close a partition.
"""
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
import math

import cv2
import numpy as np

from backend.reference_restoration import layer_role
from integrations.fpue_ontology.vocabulary import resolve_space_term
from poc.space_grounding_geometry import axis_segment, sample_line


@dataclass(frozen=True)
class ClosureSettings:
    # Lengths use the native median room-label height. These are PoC settings.
    minimum_face_heights: float = .5
    max_gap_heights: float = 5.0
    min_visibility: float = .80
    min_area_heights_squared: float = 6.0
    max_inferred_fraction: float = .35
    max_faces: int = 6000
    max_rooms: int = 128


def read_labels(observations, scope):
    labels = []
    for o in observations:
        if o['observation_type'] != 'TextRegion':
            continue
        raw = o['source_evidence']['text'].strip()
        term = resolve_space_term(raw)
        if not term and raw.upper() != 'PS':
            continue
        poly = np.asarray(o['geometry_native']['points_native'])
        x, y = poly.mean(axis=0)
        if scope[0] <= x < scope[2] and scope[1] <= y < scope[3]:
            labels.append({'observation_id': o['observation_id'], 'name': term[0] if term else 'PS',
                           'functional_label':bool(term and term[1] in ('LivingRoom','KitchenDining')),
                           'seed_native': [float(x), float(y)], 'text_height': float(np.ptp(poly[:,1]))})
    return labels


def boundary_faces(observations, assessment, distance, origin, height, cfg):
    faces = []
    for o in observations:
        if o['observation_type'] != 'PdfLine':
            continue
        source = o['source_evidence']
        hint = layer_role([source.get('layer','')])
        if hint not in ('STRUCTURAL_BOUNDARY_HINT','WINDOW_ASSEMBLY_BOUNDARY_HINT') or source.get('path_type') == 'f':
            continue
        points = np.asarray(o['geometry_native']['points_native'])
        if np.linalg.norm(points[1]-points[0]) < cfg.minimum_face_heights*height:
            continue
        visibility = float(np.mean(sample_line(distance, points-origin) <= 1.5))
        if visibility >= cfg.min_visibility:
            faces.append({'points_native': points.tolist(), 'source_refs':[o['observation_id']],
                          'kind':'VISIBLE_PDF_BOUNDARY_HYPOTHESIS','layer_hint':hint,'visible_fraction':visibility})
    for parent in assessment['structural_parents']:
        for f in parent['fragments']:
            if f['decision'] != 'ADMISSIBLE_GEOMETRIC_HYPOTHESIS':
                continue
            for face, support in zip(f['boundary_faces_native'], f['raster_support']):
                faces.append({'points_native':face,'source_refs':[f['fragment_id']],
                              'parent_structure_id':parent['parent_structure_id'],
                              'kind':'ADMISSIBLE_FRAGMENT_FACE','visible_fraction':support})
    return faces


def paint(owners, points, index, origin):
    a,b=np.rint(np.asarray(points)-origin).astype(int)
    cv2.line(owners,tuple(a),tuple(b),index,1,cv2.LINE_8)


def closures(faces, owners, origin, height, cfg, labels):
    """Nearest disjoint collinear runs, and a supported three-sided U.

    A U requires two seed-flanking arms and an observed transverse back.
    Every extension keeps its generating faces; intermediate observed strokes
    prevent a bridge from cutting across another partition.
    """
    bins=defaultdict(list)
    for index,f in enumerate(faces,1):
        axial=axis_segment(f['points_native'])
        if axial:
            axis,n,a,b=axial
            bins[axis,round(n)].append((a,b,index))
    result=[]
    def bridge(points,refs,kind):
        length=float(np.linalg.norm(np.diff(points,axis=0)))
        if length <= 1:
            return
        interior=sample_line(owners,np.asarray(points)-origin)[2:-2]
        if np.any(interior > 0):
            return
        result.append({'points_native':points,'support_face_indices':sorted(set(refs)),
                       'kind':kind,'length_px':length,'physical_wall_existence':'UNKNOWN'})
    runs={'H':[],'V':[]}
    for (axis,n), intervals in bins.items():
        def point(tangent):
            return [tangent,n] if axis=='H' else [n,tangent]
        a,b,first=sorted(intervals)[0]
        last=first
        for start,end,index in sorted(intervals)[1:]:
            if start<=b+1:
                if end>b:
                    b,last=end,index
                continue
            runs[axis].append((n,a,b,first,last))
            if start-b <= cfg.max_gap_heights*height and start-b <= 1.5*((b-a)+(end-start)):
                bridge([point(b),point(start)],[last,index],'COLLINEAR_GAP')
            a,b,first,last=start,end,index,index
        runs[axis].append((n,a,b,first,last))
    # Short extensions to an observed perpendicular face. The intersection may
    # lie anywhere along the receiving face, never beyond both of its ends.
    for axis,other in [('H','V'),('V','H')]:
        perpendicular=defaultdict(list)
        for row in runs[other]:
            perpendicular[row[0]].append(row)
        reach=max(1,round(height*.65))
        for n,start,end,first,last in runs[axis]:
            for t,step,ref in [(start,-1,first),(end,1,last)]:
                for shift in range(1,reach+1):
                    cut=round(t)+step*shift
                    target=next((r for r in perpendicular[cut] if r[1]<=n<=r[2]),None)
                    if target is not None:
                        path=[[t,n],[cut,n]]
                        bridge(path if axis=='H' else [p[::-1] for p in path],[ref,target[3],target[4]],'FACE_INTERSECTION_EXTENSION')
                        break
    seen=set()
    for label in labels:
        for axis,other in [('H','V'),('V','H')]:
            x,y=label['seed_native']
            tangent,normal=(x,y) if axis=='H' else (y,x)
            arms=[r for r in runs[axis] if r[1]<tangent<r[2]]
            near=[min((r for r in arms if r[0]<normal),key=lambda r:normal-r[0],default=None),
                  min((r for r in arms if r[0]>normal),key=lambda r:r[0]-normal,default=None)]
            if None in near:
                continue
            low,high=near
            span=high[0]-low[0]
            if not 2*height < span <= 1.3*min(low[2]-low[1],high[2]-high[1]):
                continue
            for front,back in [(1,2),(2,1)]:
                if abs(low[front]-high[front])>1.5 or abs(low[back]-high[back])>1.5:
                    continue
                cross=next((r for r in runs[other] if abs(r[0]-low[back])<=1.5 and r[1]<=low[0]+1.5 and r[2]>=high[0]-1.5),None)
                if cross:
                    points=[[low[front],low[0]],[high[front],high[0]]]
                    if axis=='V':
                        points=[p[::-1] for p in points]
                    key=tuple(map(tuple,points))
                    if key not in seen:
                        seen.add(key)
                        bridge(points,[low[front+2],high[front+2],cross[3],cross[4]],'OPEN_ENDS_OF_U')
    return result


def inner_rectangle(component_patch, tolerance=2):
    """Fit four sides only within native raster discretization error.

    The rectangle must lie entirely in the traced component. Interior notches
    or holes cannot be bridged by replacing the contour with its bounding box.
    """
    h,w=component_patch.shape
    if min(h,w)<=2*tolerance+1:
        return None
    rows=component_patch[tolerance:h-tolerance]
    cols=component_patch[:,tolerance:w-tolerance]
    if not (rows.any(axis=1).all() and cols.any(axis=0).all()):
        return None
    left=int(rows.argmax(axis=1).max())
    right=w-1-int(rows[:,::-1].argmax(axis=1).max())
    top=int(cols.argmax(axis=0).max())
    bottom=h-1-int(cols[::-1].argmax(axis=0).max())
    if max(left,w-1-right,top,h-1-bottom)>tolerance or not component_patch[top:bottom+1,left:right+1].all():
        return None
    return np.array([[left,top],[right,top],[right,bottom],[left,bottom]])


def trace_region(component, components, stats, owners, origin, height, cfg):
    x,y,w,h,area=map(int,stats[component])
    rows,cols=components.shape
    if x<=1 or y<=1 or x+w>=cols-1 or y+h>=rows-1:
        return None,'OPEN_AT_ANALYSIS_SCOPE'
    if area<cfg.min_area_heights_squared*height**2 or min(w,h)<height:
        return None,'INSUFFICIENT_FREE_AREA'
    patch=(components[y:y+h,x:x+w]==component).astype(np.uint8)
    # A long, narrow appendage can run along the cavity between two wall
    # faces. Opening is only a diagnostic: never trim the polygon to make it fit.
    radius=max(2,min(30,round(height*.3)))
    padded=np.pad(patch,radius+1)
    kernel=cv2.getStructuringElement(cv2.MORPH_ELLIPSE,(2*radius+1,2*radius+1))
    bulk=cv2.morphologyEx(padded,cv2.MORPH_OPEN,kernel)
    if not bulk.any():
        return None,'THIN_COMPONENT'
    clearance=cv2.distanceTransform(1-bulk,cv2.DIST_L2,5)
    if np.max(clearance[padded>0])>height*1.25:
        return None,'LONG_NARROW_EXTENSION_REQUIRES_REOBSERVATION'
    void=1-padded
    solid_core=cv2.morphologyEx(void,cv2.MORPH_OPEN,kernel)
    if cv2.distanceTransform(1-solid_core,cv2.DIST_L2,5)[void>0].max()>height*1.25:
        return None,'DEEP_INTERIOR_CUT_REQUIRES_REOBSERVATION'
    contours,hierarchy=cv2.findContours(patch,cv2.RETR_CCOMP,cv2.CHAIN_APPROX_NONE)
    if len(contours)>32 or sum(map(len,contours))>24000:
        return None,'CONTOUR_BUDGET'
    outer=[]; holes=[]; edges=[]; inferred=total=0.
    for contour,entry in zip(contours,hierarchy[0]):
        local=contour[:,0,:]+[x,y]
        q=cv2.approxPolyDP(contour,1.0,True)[:,0,:]
        if len(q)<3 or len(set(map(tuple,q)))!=len(q):
            return None,'DANGLING_OR_DEGENERATE_BOUNDARY'
        # Thin excursions on both sides of a dangling interior stroke cannot
        # form a room notch. A real enclosed column has a 2-D hole.
        if entry[3]>=0 and cv2.contourArea(contour)/max(cv2.arcLength(contour,True),1)<height*.16:
            return None,'UNRESOLVED_INTERIOR_STROKE'
        ring=(q+[x,y]+origin).tolist()
        (outer if entry[3]<0 else holes).append(ring)
        # Attribute the unsimplified inner trace to adjacent source/closure
        # strokes. Prefer a four-neighbour before diagonal neighbours.
        ids=np.zeros(len(local),np.int32)
        for dx,dy in ((0,1),(1,0),(0,-1),(-1,0),(1,1),(-1,1),(1,-1),(-1,-1)):
            v=owners[local[:,1]+dy,local[:,0]+dx]
            take=(ids==0)&(v!=0)
            ids[take]=v[take]
        if np.any(ids==0):
            return None,'UNSUPPORTED_CONTOUR'
        closed=np.vstack((local,local[0]))
        lengths=np.linalg.norm(np.diff(closed,axis=0),axis=1)
        total+=float(lengths.sum());inferred+=float(lengths[ids<0].sum())
        cuts=np.r_[0,np.flatnonzero(ids[1:]!=ids[:-1])+1,len(ids)]
        for start,end in zip(cuts[:-1],cuts[1:]):
            run=closed[start:end+1].astype(np.int32)
            simple=cv2.approxPolyDP(run,.65,False).reshape(-1,2)
            edges.append({'points_native':(simple+origin).tolist(),'owner':int(ids[start])})
    if len(outer)!=1 or inferred>cfg.max_inferred_fraction*total:
        return None,'AMBIGUOUS_RING_OR_INSUFFICIENT_SUPPORT'
    # Resolve thin slits left by one-sided wall observations. Pinched outlines
    # cannot stand in for a valid general polygon.
    from backend.architectural_spatial import polygon_measures
    rectangle=inner_rectangle(patch) if not holes else None
    if rectangle is not None:
        outer=[(rectangle+[x,y]+origin).tolist()]
    rings={'outer_rings':outer,'holes':holes}
    measures=polygon_measures(rings) if sum(len(r) for r in outer+holes)<=256 else None
    if not measures:
        return None,'INVALID_OR_COMPLEX_RING'
    return {'polygon_native':rings,'boundary_runs':edges,'inferred_perimeter_fraction':inferred/total,
            'shape_model':'INSCRIBED_RECTANGLE' if rectangle is not None else 'TRACED_POLYGON',
            'component_pixel_count':area,'area_native_px2':measures['area_native_px2'],
            'bbox_native':measures['bbox_native']},None


def partial_runs(seed, owners, faces, origin, reach):
    """Visible portions of first-hit faces. Never return the remote end of a long line."""
    hits=defaultdict(list)
    for angle in np.linspace(0,2*math.pi,96,endpoint=False):
        samples=np.asarray(seed)-origin+np.arange(1,reach,.7)[:,None]*[math.cos(angle),math.sin(angle)]
        q=np.rint(samples).astype(int)
        q=q[(q[:,0]>=0)&(q[:,0]<owners.shape[1])&(q[:,1]>=0)&(q[:,1]<owners.shape[0])]
        occupied=np.flatnonzero(owners[q[:,1],q[:,0]]>0)
        if len(occupied):
            x,y=q[occupied[0]]
            hits[int(owners[y,x])].append([x+origin[0],y+origin[1]])
    runs=[]
    for owner,points in hits.items():
        a,b=np.array(faces[owner-1]['points_native'])
        delta=b-a
        t=np.clip((np.asarray(points)-a)@delta/max(delta@delta,1e-9),0,1)
        if (t.max()-t.min())*np.linalg.norm(delta)>1:
            runs.append({'owner':owner,'points_native':[list(a+t.min()*delta),list(a+t.max()*delta)]})
    return runs


def partition_conflicts(rows, origin, shape):
    """Check the exported, simplified polygons across alternative passes.

    Discrete components from one pass are disjoint, but polygons come from
    different passes. A shared edge is permissible; shared interior is not.
    """
    owner=np.zeros(shape,np.int32)
    conflicts=defaultdict(set)
    for index,row in enumerate(rows,1):
        rings=row['polygon_native']
        if not rings:
            continue
        x,y,X,Y=np.asarray(row['bbox_native'],int)-np.tile(origin,2)
        patch=owner[y:Y+1,x:X+1]
        inside=np.zeros(patch.shape,np.uint8)
        offset=origin+[x,y]
        for key,value in [('outer_rings',1),('holes',0)]:
            for ring in rings[key]:
                points=np.asarray(ring,np.int32)-offset
                cv2.fillPoly(inside,[points.astype(np.int32)],value)
                cv2.polylines(inside,[points.astype(np.int32)],True,0,1)
        mask=inside!=0
        for other in np.unique(patch[mask]):
            if other:
                conflicts[index-1].add(int(other)-1)
                conflicts[int(other)-1].add(index-1)
        patch[mask & (patch==0)]=index
    return conflicts


def restore_partitions(binding, observations, assessment, gray, cfg=ClosureSettings(), *, expected_role="Ref"):
    """Return only this run's Ref partition revision; no Target inputs or GT."""
    if expected_role not in ('Ref','Target') or binding['source_role']!=expected_role or assessment['frame_id']!=binding['frame_id'] or assessment['source_role']!=expected_role:
        raise ValueError('Ref frame required')
    if assessment['source_binding']!=binding:
        raise ValueError('Geometry revision or scope mismatch')
    if any(o['frame_id']!=binding['frame_id'] or o['source_role']!=expected_role for o in observations):
        raise ValueError('Mixed observation frame')
    scope=binding['core_bbox_native']
    origin=np.floor(scope[:2]).astype(int)
    end=np.ceil(scope[2:]).astype(int)
    if gray.shape!=(binding['height'],binding['width']) or np.any(origin<0) or np.any(end>[gray.shape[1],gray.shape[0]]) or np.any(end<=origin):
        raise ValueError('Invalid Reference frame or scope')
    crop=gray[origin[1]:end[1],origin[0]:end[0]]
    labels=read_labels(observations,scope)
    height=max(4.,float(np.median([l['text_height'] for l in labels])) if labels else 8*binding['extraction_zoom'])
    distance=cv2.distanceTransform((crop>=245).astype(np.uint8),cv2.DIST_L2,5)
    faces=boundary_faces(observations,assessment,distance,origin,height,cfg)
    result={'schema_version':'reference_space_closure/1' if expected_role=='Ref' else 'target_space_closure/1','source_role':expected_role,'frame_id':binding['frame_id'],
            'source_binding':binding,'status':'PROPOSED','scope_native':scope,'settings':asdict(cfg),'label_scale_px':height,
            'boundary_run_owner_semantics':'positive = 1-based faces index; negative = negated 1-based closures index',
            'pixel_trace_semantics':'Free-side native raster sample, approx. 1 px from source face; not a surveyed dimension',
            'faces':faces,'closures':[],'regions':[],'unlabelled_regions':[],'verified_spaces':0,
            'operation':'FRAGMENT_CLOSURE','creates_physical_walls':False,'creates_connections':False}
    if len(faces)>cfg.max_faces or len(labels)>cfg.max_rooms:
        return {**result,'status':'NOT_COMPUTED_RESOURCE_BUDGET','faces':[]}
    owners=np.zeros(crop.shape,np.int32)
    for i,f in enumerate(faces,1):
        paint(owners,f['points_native'],i,origin)
    bridges=closures(faces,owners,origin,height,cfg,labels)
    bridges.sort(key=lambda b:(b['kind']=='OPEN_ENDS_OF_U',b['length_px']))
    result['closures']=bridges
    results=[{**label,'status':'PARTIAL_BOUNDARY','polygon_native':None,'boundary_runs':[],'alternatives':[]} for label in labels]
    for mode,limit in [('OBSERVED',0),('SHORT_GAPS',height),('BOUNDED_GAPS',cfg.max_gap_heights*height),('U_ENDS',math.inf)]:
        trial=owners.copy()
        if limit:
            for j,bridge in enumerate(bridges,1):
                if bridge['length_px']<=limit and (bridge['kind']!='OPEN_ENDS_OF_U' or mode=='U_ENDS'):
                    # Competing inferred strokes cannot validate one another
                    # at an invented intersection. Keep the shorter alternative.
                    if not np.any(sample_line(trial,np.asarray(bridge['points_native'])-origin)[1:-1]<0):
                        paint(trial,bridge['points_native'],-j,origin)
            trial[owners!=0]=owners[owners!=0]
        _,components,stats,_=cv2.connectedComponentsWithStats((trial==0).astype(np.uint8),connectivity=4)
        members=defaultdict(list)
        for idx,label in enumerate(labels):
            x,y=np.clip(np.rint(label['seed_native']-origin).astype(int),0,np.array(crop.shape[::-1])-1)
            members[int(components[y,x])].append(idx)
        for component,indices in members.items():
            candidate=None
            if component==0:
                reason='SEED_ON_BOUNDARY'
            elif len(indices)>1:
                reason='SHARED_COMPONENT_MULTIPLE_LABELS'
            else:
                seed_label=labels[indices[0]]
                if seed_label['functional_label']:
                    reason='FUNCTIONAL_EXTENT_UNDETERMINED'
                else:
                    candidate,reason=trace_region(component,components,stats,trial,origin,height,cfg)
                if candidate:
                    poly=candidate['polygon_native']
                    def contains(ring):
                        return cv2.pointPolygonTest(np.asarray(ring,np.float32),tuple(seed_label['seed_native']),False)
                    if contains(poly['outer_rings'][0])<=0 or any(contains(r)>=0 for r in poly['holes']):
                        candidate,reason=None,'SEED_OUTSIDE_SIMPLIFIED_RING'
            for idx in indices:
                row=results[idx]
                attempt={'mode':mode,'reason':reason,'label_ids':[labels[k]['observation_id'] for k in indices]}
                if candidate:
                    attempt['bbox_native']=candidate['bbox_native']
                    if row['polygon_native'] is None:
                        row.update(candidate,status='REGION_PROPOSED',chosen_mode=mode)
                row['alternatives'].append(attempt)
        if mode=='OBSERVED':
            for component in range(1,len(stats)):
                if component in members or stats[component,cv2.CC_STAT_AREA]<height**2*12:
                    continue
                region,_=trace_region(component,components,stats,trial,origin,height,cfg)
                if region:
                    result['unlabelled_regions'].append({**region,'status':'UNLABELLED_REGION_PROPOSED'})
                if len(result['unlabelled_regions'])>=cfg.max_rooms:
                    result['unlabelled_coverage']='OUTPUT_LIMIT';break
    prefix=binding['frame_id']+':partition:'+binding['analysis_run_id']
    all_rows=results+result['unlabelled_regions']
    for index,row in enumerate(all_rows):
        row.update(region_id=f'{prefix}:{index}',claim_status='PROPOSED')
    conflicts=partition_conflicts(all_rows,origin,crop.shape)
    for index,others in conflicts.items():
        row=all_rows[index]
        row['overlap_conflict_refs']=[all_rows[i]['region_id'] for i in sorted(others)]
        row['withheld_geometry']={key:row.pop(key) for key in (
            'polygon_native','bbox_native','area_native_px2','inferred_perimeter_fraction','boundary_runs')}
        row.update(polygon_native=None,status='PARTIAL_BOUNDARY',boundary_runs=[])
        row.pop('chosen_mode',None)
    result['withheld_unlabelled_regions']=[r for r in result['unlabelled_regions'] if not r['polygon_native']]
    result['unlabelled_regions']=[r for r in result['unlabelled_regions'] if r['polygon_native']]
    for row in results:
        if row['polygon_native'] is None:
            row['boundary_runs']=partial_runs(row['seed_native'],owners,faces,origin,height*15)
            if any(a['reason']=='SHARED_COMPONENT_MULTIPLE_LABELS' for a in row['alternatives']):
                row['interpretations']=['MISSED_BOUNDARY','UNRESOLVED_PASSAGE','SHARED_OPEN_PLAN','LABEL_ERROR']
    result['regions']=results
    result['summary']=dict(Counter(r['status'] for r in results))
    return result
