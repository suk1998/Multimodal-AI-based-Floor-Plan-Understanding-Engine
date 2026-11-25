"""Conditional native-pixel accounting for a single Reference dwelling.

Face groups propose the envelope. A logical gap can help bound the domain,
but only an admitted occupancy fragment can remove physical wall area from F.
"""
from collections import defaultdict

import cv2
import numpy as np

from backend.reference_space_closure import paint, sample_line


def components(mask):
    return cv2.connectedComponentsWithStats(mask.astype(np.uint8), connectivity=4)[:3]


def describe(mask, origin):
    """Lossless, half-open native pixel runs, coalesced across identical rows."""
    rectangles = []
    active = {}
    for y, row in enumerate(mask, int(origin[1])):
        edges = np.flatnonzero(np.diff(np.pad(row.astype(np.int8), 1))) + origin[0]
        following = {}
        for left, right in edges.reshape(-1, 2).tolist():
            rect = active.get((left, right))
            if rect is None:
                rect = [left, y, right, y + 1]
                rectangles.append(rect)
            else:
                rect[3] = y + 1
            following[left, right] = rect
        active = following
    count, _, _ = components(mask)
    inverse_count, _, _ = components(np.pad(~mask, 1, constant_values=True))
    return {'pixel_count':int(mask.sum()), 'components':count-1, 'holes':inverse_count-2,
            'rects_native_xyxy':rectangles}


def face_groups(partition, observations):
    """PDF external references can separate adjacent plans on the same sheet."""
    layers = {o['observation_id']:o['source_evidence'].get('layer','') for o in observations}
    groups = defaultdict(set)
    for index, face in enumerate(partition['faces'], 1):
        layer = layers.get(face['source_refs'][0]) or ''
        prefix, separator, _ = layer.rpartition('|')
        groups[prefix if separator else ''].add(index)
    return groups


def envelope_options(partition, groups, shape, origin, seeds):
    """Find bounded architectural complexes without closing against crop edges."""
    options = []
    audit = []
    for group, indices in groups.items():
        ink = np.zeros(shape, np.uint8)
        for i in indices:
            paint(ink, partition['faces'][i-1]['points_native'], 1, origin)
        bridges = [j for j,c in enumerate(partition['closures'],1)
                   if c['kind'] != 'OPEN_ENDS_OF_U' and set(c['support_face_indices']) <= indices]
        for mode in ('OBSERVED','SUPPORTED_GAPS'):
            if mode == 'SUPPORTED_GAPS':
                if not bridges:
                    continue
                admitted = []
                for j in bridges:
                    points = partition['closures'][j-1]['points_native']
                    if np.any(sample_line(ink,np.asarray(points)-origin)[1:-1]==2):
                        continue
                    paint(ink, points, 2, origin)
                    admitted.append(j)
                bridges = admitted
            _, cells, stats = components(ink == 0)
            edge_ids = np.unique(np.concatenate((cells[0], cells[-1], cells[:,0], cells[:,-1])))
            bounded = (cells != 0) & ~np.isin(cells, edge_ids)
            # Only bridge the single sampled face between adjoining closed cells.
            # This silhouette groups the cells; it is not itself the unit mask.
            grouped = cv2.dilate(bounded.astype(np.uint8), np.ones((3,3),np.uint8))
            contours, _ = cv2.findContours(grouped, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            by_cell = defaultdict(list)
            for index, (x,y) in enumerate(seeds):
                if bounded[y,x]:
                    by_cell[int(cells[y,x])].append(index)
            viable = []
            for c in contours:
                members = [i for ids in by_cell.values() for i in ids
                           if cv2.pointPolygonTest(c, tuple(map(float,seeds[i])), False) >= 0]
                names = {partition['regions'][i]['name'] for i in members} - {'PS'}
                x,y,w,h = cv2.boundingRect(c)
                if len(names)<2 or x<=0 or y<=0 or x+w>=shape[1] or y+h>=shape[0]:
                    continue
                viable.append((c,members))
            audit.append({'source_group':group,'mode':mode,'bounded_complexes_with_multiple_labels':len(viable),
                          'grounded_label_counts':[len(m) for _,m in viable]})
            for contour, members in viable:
                options.append(dict(group=group, indices=indices, bridges=bridges if mode=='SUPPORTED_GAPS' else [],
                                    mode=mode, cells=cells, stats=stats, contour=contour,
                                    members=set(members), bounded=bounded))
    return options, audit


def inside_face_domain(option, seeds, scale):
    cells, stats = option['cells'], option['stats']
    silhouette = np.zeros(cells.shape,np.uint8)
    cv2.drawContours(silhouette,[option['contour']],-1,1,-1)
    enclosed = option['bounded'] & (silhouette != 0)
    padded = cv2.dilate(enclosed.astype(np.uint8),np.ones((3,3),np.uint8)).astype(bool)
    # Do not include the outside stroke or dangling branches. Remove exterior
    # wall/window cavities to reach the free-side face where it is distinguishable.
    exterior = ~padded
    near_exterior = cv2.dilate(exterior.astype(np.uint8),np.ones((5,5),np.uint8)).astype(bool)
    grounded = {int(cells[y,x]) for i,(x,y) in enumerate(seeds) if i in option['members']}
    peripheral = set(np.unique(cells[enclosed & near_exterior]).tolist())
    excluded = set()
    for i in np.unique(cells[enclosed]):
        # A broad unlabelled cavity may be a courtyard, common part or a
        # missed room. Carry it as unknown coverage rather than dwelling area.
        if i not in grounded and (i in peripheral or stats[i,4] >= 6*scale**2 and
                                  min(stats[i,2],stats[i,3]) >= 2*scale):
            excluded.add(int(i))
    outside = ~((enclosed | ((cells==0) & padded))) | np.isin(cells,list(excluded))
    domain = ~cv2.dilate(outside.astype(np.uint8),np.ones((3,3),np.uint8)).astype(bool)
    # Always keep the native samples traced on the inner side of a face.
    domain |= enclosed & ~np.isin(cells,list(excluded))
    return domain, enclosed & np.isin(cells,list(excluded))


def polygon_mask(shape, polygon, origin):
    mask = np.zeros(shape,np.uint8)
    for key, value in [('outer_rings',1),('holes',0)]:
        for ring in polygon[key]:
            cv2.fillPoly(mask, [np.rint(np.asarray(ring)-origin).astype(np.int32)], value)
    return mask.astype(bool)


def interpret(partition, seeds, option, residual, entities, origin):
    rows = partition['regions']
    cells = option['cells']
    functional = [i for i,r in enumerate(rows) if r['functional_label']]
    _, residual_components, _ = components(residual)
    labels = [{'name':rows[i]['name'], 'observation_id':rows[i]['observation_id'],
               'seed_native':rows[i]['seed_native'], 'individual_polygon_native':None,
               'residual_component':int(residual_components[seeds[i][1],seeds[i][0]]) or None}
              for i in functional]
    result = {'status':'HOLD','labels':labels,'shared_region':None,'creates_connections':False,
              'circulation_interpretation':'UNKNOWN_NONEXCLUSIVE','reason':'MISSING_OR_AMBIGUOUS_FUNCTION_LABELS'}
    if len(functional)!=2 or len({rows[i]['name'] for i in functional})!=2:
        return result
    (x,y),(u,v) = [seeds[i] for i in functional]
    cell = cells[y,x]
    if not cell or cell!=cells[v,u] or not residual[y,x] or not residual[v,u]:
        return {**result,'reason':'SEPARATE_PARTITIONS_OR_SEEDS_OUTSIDE_RESIDUAL'}
    if any(i not in functional and cells[b,a]==cell for i,(a,b) in enumerate(seeds)):
        return {**result,'reason':'OTHER_ROOM_LABEL_IN_SHARED_CELL'}
    # Door/opening proposals and inferred partition closures are separators
    # for this interpretation only. They never remove pixels from physical F.
    shared = ((cells==cell) & residual).astype(np.uint8)
    for gap in partition['closures']:
        if set(gap['support_face_indices']) <= option['indices']:
            paint(shared,gap['points_native'],0,origin)
    for e in entities:
        line = e['geometry_properties'].get('opening_segment_native')
        if line:
            paint(shared,line,0,origin)
    _, groups, _ = components(shared)
    if not groups[y,x] or groups[y,x]!=groups[v,u]:
        return {**result,'reason':'OPENING_OR_DOMAIN_BREAK_BETWEEN_LABELS'}
    # A broad direct path supplies positive local open-area evidence. A narrow
    # doorway or an indirect route alone cannot qualify as open plan.
    distance = cv2.distanceTransform(np.pad(shared,1),cv2.DIST_L2,5)[1:-1,1:-1]
    path = np.rint(np.linspace([x,y],[u,v],max(abs(u-x),abs(v-y))+1)).astype(int)
    clearance = float(distance[path[:,1],path[:,0]].min())
    if clearance < partition['label_scale_px']:
        return {**result,'reason':'INSUFFICIENT_DIRECT_CLEARANCE','min_clearance_px':clearance}
    return {**result,'status':'PROPOSED','reason':'DISTINCT_LABELS_IN_ONE_PARTITION_WITH_BROAD_CLEAR_PATH',
            'min_clearance_px':clearance,'shared_region':describe(groups==groups[y,x],origin)}


def derive_reference_domains(partition, assessment, observations, entities, *, functional_reobservations=(), expected_role="Ref"):
    binding = assessment['source_binding']
    if expected_role not in ('Ref','Target') or binding['source_role']!=expected_role or partition['source_binding']!=binding or partition['frame_id']!=binding['frame_id']:
        raise ValueError('Reference domains require matching frame, revision and scope')
    if assessment['frame_id']!=binding['frame_id'] or any(
            item['frame_id']!=binding['frame_id'] or item['source_role']!=expected_role for item in [*observations,*entities]):
        raise ValueError('Mixed domain evidence frame')
    base = {'schema_version':'reference_domains/1' if expected_role=='Ref' else 'target_domains/1','source_binding':binding,'frame_id':binding['frame_id'],
            'source_role':expected_role,'claim_status':'PROPOSED','status':'HOLD','layers':{},
            'pixel_convention':'native integer sample centres; rectangles use exclusive xmax, ymax',
            'inclusion_policy':'Partial dwelling including grounded enclosed balconies and service rooms. Common areas, outside and ungrounded broad cavities withheld. Exterior wall face assignment approximate.',
            'operations':['FREE_DOMAIN','RESIDUAL_DIFFERENCE','REGION_INTERPRETATION'],
            'inventory_complete':False}
    if partition['status']!='PROPOSED':
        return {**base,'reason':partition['status']}
    scope = partition['scope_native']
    origin = np.floor(scope[:2]).astype(int)
    size = np.ceil(scope[2:]).astype(int)-origin
    shape = tuple(size[::-1])
    seeds = [np.clip(np.rint(r['seed_native']).astype(int)-origin,0,size-1) for r in partition['regions']]
    groups = face_groups(partition,observations)
    # Bound retained label rasters across simultaneous CAD group alternatives.
    if size.prod()*max(1,len(groups))*2 > 32_000_000:
        return {**base,'reason':'DOMAIN_RASTER_WORK_BUDGET'}
    options, trials = envelope_options(partition,groups,shape,origin,seeds)
    base['envelope_trials'] = trials
    if not options:
        return {**base,'reason':'NO_BOUNDED_MULTI_LABEL_ENVELOPE'}
    option = max(options,key=lambda o:len(o['members']))
    if any(not other['members']<=option['members'] or other['group']!=option['group']
           for other in options):
        return {**base,'reason':'COMPETING_DWELLING_ENVELOPES'}
    names = [partition['regions'][i]['name'] for i in option['members']]
    if any(names.count(name)>1 for name in ('침실1','침실2','침실3','현관')):
        return {**base,'reason':'COMPETING_DWELLING_ENVELOPES'}
    domain, withheld = inside_face_domain(option,seeds,partition['label_scale_px'])
    occupied = np.zeros(shape,np.uint8)
    fragment_ids = []
    for parent in assessment['structural_parents']:
        for f in parent['fragments']:
            if f['decision']=='ADMISSIBLE_GEOMETRIC_HYPOTHESIS':
                poly = np.rint(np.asarray(f['occupancy_polygon_native'])-origin).astype(np.int32)
                cv2.fillPoly(occupied,[poly],1)
                fragment_ids.append(f['fragment_id'])
    wall = domain & (occupied != 0)
    core, allocated = np.zeros(shape,bool), np.zeros(shape,bool)
    ledger = []
    for index,r in enumerate(partition['regions']):
        if not r['polygon_native'] or r['functional_label']:
            continue
        material = polygon_mask(shape,r['polygon_native'],origin)
        is_core = r['name']=='PS'
        ledger.append({'region_id':r['region_id'],'name':r['name'],'operand':'C' if is_core else 'P',
                       'seed_in_envelope':index in option['members'], 'source_pixels':int(material.sum()),'outside_U':int((material & ~domain).sum()),
                       'wall_overlap':int((material & wall).sum())})
        if is_core and index in option['members']:
            core |= material & domain
    free = domain & ~(wall | core)
    by_id = {r['region_id']:r for r in partition['regions']}
    for row in ledger:
        if row['operand']=='P':
            clipped = polygon_mask(shape,by_id[row['region_id']]['polygon_native'],origin) & free
            if not row['seed_in_envelope']:
                clipped[:] = False
            row.update(after_clipping=int(clipped.sum()),duplicate_pixels=int((allocated & clipped).sum()))
            allocated |= clipped
    residual = free & ~allocated
    layers = {'U':domain,'W':wall,'C':core,'F':free,'P':allocated,'R':residual,'unknown_coverage':withheld}
    omitted = [r['observation_id'] for i,r in enumerate(partition['regions']) if i not in option['members']]
    interpreted_partition = {**partition, 'regions':[*partition['regions'],*functional_reobservations]}
    interpreted_seeds = [*seeds,*(np.clip(np.rint(row['seed_native']).astype(int)-origin,0,size-1) for row in functional_reobservations)]
    return {**base,'status':'PARTIAL','reason':'CONDITIONAL_INNER_FACE_DOMAIN',
            'layers':{key:describe(mask,origin) for key,mask in layers.items()},
            'envelope':{'source_group':option['group'],'mode':option['mode'],'face_indices':sorted(option['indices']),
                        'closure_indices':option['bridges'],'outside_or_unbounded_label_ids':omitted,
                        'index_convention':'1-based stage 4 face and closure inputs; not all are perimeter edges'},
            'occupancy_input_fragment_ids':fragment_ids,'partition_ledger':ledger,
            'withheld_unlabelled_partition_ids':[r['region_id'] for r in partition['unlabelled_regions']],
            'unclosed_named_regions':[r['region_id'] for r in partition['regions'] if not r['polygon_native']],
            'area_audit':{'excluded_cavity_pixels':int(withheld.sum()),
                          'partition_overlap_after_clipping':sum(row.get('duplicate_pixels',0) for row in ledger),
                          'W_intersect_C':int((wall & core).sum()),
                          'U_minus_occupied_union_minus_F':int(domain.sum()-(wall|core).sum()-free.sum()),
                          'F_minus_P_minus_R':int(free.sum()-allocated.sum()-residual.sum()),
                          'pixel_repair_or_smoothing_after_difference':False},
            'open_plan':interpret(interpreted_partition,interpreted_seeds,option,residual,entities,origin)}
