"""Geometry observations, not architectural truth. All lengths are native pixels."""
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
import math

import cv2
import numpy as np
import pymupdf


@dataclass(frozen=True)
class Settings:
    version: str = 'space_grounding_geometry/1'
    axial_angle_degrees: float = 1.0
    merge_tolerance_px: float = 1.25
    raster_distance_px: float = 1.5
    min_line_text_height_ratio: float = .75
    min_pair_thickness_px: float = 2.0
    max_pair_text_height_ratio: float = 2.3
    min_boundary_stroke_px: float = .90
    min_boundary_support: float = .70
    max_text_overlap: float = .16
    min_overlap_ratio: float = .55
    max_opening_text_height_ratio: float = 12.0
    arc_fit_error_ratio: float = .035
    min_arc_angle: float = 65.0
    max_arc_angle: float = 115.0
    min_wall_length_thickness_ratio: float = 2.0
    min_closed_area_text_height_sq: float = 5.0
    boundary_ray_count: int = 96
    boundary_search_page_fraction: float = .30
    visibility_min_contrast_8bit: int = 10
    partial_boundary_local_distance_factor: float = 2.5


def transform_points(points, matrix):
    p=np.c_[np.asarray(points,float),np.ones(len(points))]@np.asarray(matrix,float).T
    return (p[:,:2]/p[:,2:]).tolist()


def pdf_to_native_matrix(page,zoom,pixmap_origin=(0,0)):
    m=page.rotation_matrix*pymupdf.Matrix(zoom,zoom)
    return [[m.a,m.c,m.e-pixmap_origin[0]],[m.b,m.d,m.f-pixmap_origin[1]],[0,0,1]]


def sample_line(field,points,count=None):
    p=np.asarray(points,float)
    count=count or max(3,int(np.linalg.norm(p[1]-p[0]))+1)
    q=np.linspace(p[0],p[1],count)
    x=np.clip(np.rint(q[:,0]).astype(int),0,field.shape[1]-1)
    y=np.clip(np.rint(q[:,1]).astype(int),0,field.shape[0]-1)
    result=field[y,x].astype(float)
    outside=(q[:,0]<0)|(q[:,0]>=field.shape[1])|(q[:,1]<0)|(q[:,1]>=field.shape[0])
    result[outside]=np.nan
    return result


def axis_segment(points,tolerance=1.0):
    a,b=np.asarray(points,float);delta=b-a
    if np.linalg.norm(delta)<1e-5:return None
    angle=math.degrees(math.atan2(abs(delta[1]),abs(delta[0])))
    if angle<=tolerance:return 'H',float(np.mean([a[1],b[1]])),*sorted([float(a[0]),float(b[0])])
    if 90-angle<=tolerance:return 'V',float(np.mean([a[0],b[0]])),*sorted([float(a[1]),float(b[1])])
    return None


def axis_points(axis,n,start,end):
    return [[start,n],[end,n]] if axis=='H' else [[n,start],[n,end]]


def rectangle_for(axis,n1,n2,start,end):
    return [[start,n1],[end,n1],[end,n2],[start,n2]] if axis=='H' else [[n1,start],[n2,start],[n2,end],[n1,end]]


def fit_arc(control):
    p=np.asarray(control,float);t=np.linspace(0,1,25)[:,None]
    q=(1-t)**3*p[0]+3*(1-t)**2*t*p[1]+3*(1-t)*t*t*p[2]+t**3*p[3]
    design=np.c_[2*q[:,0],2*q[:,1],np.ones(len(q))]
    x,y,k=np.linalg.lstsq(design,np.sum(q*q,axis=1),rcond=None)[0]
    radius=math.sqrt(max(0,k+x*x+y*y));dist=np.linalg.norm(q-[x,y],axis=1)
    angles=np.unwrap(np.arctan2(q[:,1]-y,q[:,0]-x))
    return {'center':[float(x),float(y)],'radius':radius,
        'angle_degrees':float(abs(angles[-1]-angles[0])*180/math.pi),
        'radial_rms_px':float(np.sqrt(np.mean((dist-radius)**2))),
        'endpoints':[q[0].tolist(),q[-1].tolist()],'polyline':q.tolist()}


def vector_observations(page,matrix):
    paths=page.get_drawings();segments=[];arcs=[];symbols=[];rectangles=[];types=Counter();widths=Counter()
    for index,path in enumerate(paths):
        items=path['items'];curve_count=sum(i[0]=='c' for i in items)
        path_id=f'pdf_path:{index}';width=float(path.get('width') or 0)*np.linalg.norm(np.asarray(matrix)[:2,0])
        widths[round(width,3)]+=1
        source={'path_id':path_id,'path_index':index,'layer':path.get('layer',''),
            'stroke_px':width,'dash':path.get('dashes'),'path_type':path['type'],
            'closed':bool(path.get('closePath')),'seqno':path.get('seqno'),
            'stroke_color':path.get('color'),'stroke_opacity':path.get('stroke_opacity')}
        for item_index,item in enumerate(items):
            kind=item[0];types[kind]+=1;edges=[]
            if kind=='l':edges=[[item[1],item[2]]]
            elif kind=='qu':
                q=item[1];p=[q.ul,q.ur,q.lr,q.ll];edges=[[p[i],p[(i+1)%4]] for i in range(4)]
                corners=transform_points([list(point) for point in p],matrix)
                box=[min(p[0] for p in corners),min(p[1] for p in corners),max(p[0] for p in corners),max(p[1] for p in corners)]
                if 12<box[2]-box[0]<220 and 8<box[3]-box[1]<65:
                    rectangles.append({**source,'id':f'{path_id}:rectangle:{item_index}','bbox':box})
            elif kind=='re':
                r=item[1];p=[r.tl,r.tr,r.br,r.bl];edges=[[p[i],p[(i+1)%4]] for i in range(4)]
            elif kind=='c':
                control=transform_points([list(p) for p in item[1:]],matrix)
                if curve_count==1:
                    arc=fit_arc(control);arcs.append({**source,**arc,'id':f'{path_id}:curve:{item_index}','control':control})
            for edge_index,edge in enumerate(edges):
                segments.append({**source,'id':f'{path_id}:{item_index}:{edge_index}',
                    'points':transform_points([list(p) for p in edge],matrix)})
        if curve_count==4 and len(items)==4:
            r=path['rect'];corners=transform_points([[r.x0,r.y0],[r.x1,r.y1]],matrix)
            b=[min(p[0] for p in corners),min(p[1] for p in corners),max(p[0] for p in corners),max(p[1] for p in corners)]
            w,h=b[2]-b[0],b[3]-b[1]
            if 12<=min(w,h) and .8<w/max(h,1)<1.25 and max(w,h)<80:
                symbols.append({**source,'id':f'{path_id}:closed_curve','bbox':b,'center':[(b[0]+b[2])/2,(b[1]+b[3])/2],
                    'claim':'approximately circular closed PDF path, not a window or door'})
    return {'summary':{'path_count':len(paths),'item_counts':dict(types),'stroke_width_native_px_counts':dict(widths),
        'layer_counts':dict(Counter(p.get('layer','') for p in paths)),
        'dash_array_counts':dict(Counter(str(p.get('dashes')) for p in paths)),
        'closed_path_flag_count':sum(bool(p.get('closePath')) for p in paths),
        'multi_line_path_count':sum(sum(item[0]=='l' for item in p['items'])>1 for p in paths),
        'closed_quad_item_count':types.get('qu',0),'rectangle_item_count':types.get('re',0),
        'embedded_image_count':len(page.get_image_info()),'extractable_word_count':len(page.get_text('words')),
        'line_style_limitation':'All dash arrays may be solid; drawn short dashed strokes need separate interpretation.'},
        'segments':segments,'arcs':arcs,'symbols':symbols,'rectangles':rectangles}


def raster_observations(gray,text_polygons,height,settings):
    threshold,ink=cv2.threshold(gray,0,255,cv2.THRESH_BINARY_INV|cv2.THRESH_OTSU)
    text=np.zeros(gray.shape,np.uint8)
    for p in text_polygons:cv2.fillPoly(text,[np.rint(p).astype(np.int32)],255)
    distance=cv2.distanceTransform(255-ink,cv2.DIST_L2,5)
    visible_ink=(gray<255-settings.visibility_min_contrast_8bit).astype(np.uint8)*255
    visible_distance=cv2.distanceTransform(255-visible_ink,cv2.DIST_L2,5)
    raw=cv2.createLineSegmentDetector(cv2.LSD_REFINE_STD).detect(gray)
    segments=[]
    if raw[0] is not None:
        widths=raw[1].reshape(-1) if raw[1] is not None else np.ones(len(raw[0]))
        for i,(line,width) in enumerate(zip(raw[0].reshape(-1,4),widths)):
            points=line.reshape(2,2).tolist()
            if np.linalg.norm(np.diff(points,axis=0))<height*settings.min_line_text_height_ratio:continue
            segments.append({'id':f'raster_lsd:{i}','points':points,'stroke_px':float(width),'layer':'',
                'dash':None,'path_type':'raster','source':'OpenCV LSD; width is detector support width, not measured wall thickness'})
    no_text=ink.copy();no_text[text>0]=0
    run=max(3,round(height*2))
    horizontal=cv2.morphologyEx(no_text,cv2.MORPH_OPEN,np.ones((1,run),np.uint8))
    vertical=cv2.morphologyEx(no_text,cv2.MORPH_OPEN,np.ones((run,1),np.uint8))
    morphology=cv2.bitwise_or(horizontal,vertical)
    return {'threshold':float(threshold),'ink':ink,'text_mask':text,'distance':distance,'segments':segments,
        'visible_ink':visible_ink,'visible_distance':visible_distance,
        'morphology':morphology,'morphology_audit':{'input_ink_pixels':int(np.count_nonzero(ink)),
            'after_text_exclusion_pixels':int(np.count_nonzero(no_text)),
            'after_axis_opening_pixels':int(np.count_nonzero(morphology)),
            'note':'Diagnostic only; loss is retained, this mask is not a wall truth or the sole barrier.',
            'kernel_length_px':run}}


def merge_lines(segments,raster,height,settings,source='pdf',*,audit_sink=None):
    groups=defaultdict(list);rejected=Counter();examples=[]
    minimum=height*settings.min_line_text_height_ratio
    for s in segments:
        line=axis_segment(s['points'],settings.axial_angle_degrees)
        reason=None
        if line is None:reason='NON_AXIAL'
        elif line[3]-line[2]<minimum:reason='SHORT_FRAGMENT_OR_GLYPH'
        elif any(p[0]<0 or p[1]<0 or p[0]>raster['ink'].shape[1] or p[1]>raster['ink'].shape[0] for p in s['points']):reason='OUTSIDE_NATIVE_FRAME_NOT_CLIPPED'
        elif s.get('path_type')=='f':reason='FILL_OUTLINE_NOT_STROKED_BOUNDARY'
        elif float(np.mean(sample_line(raster['text_mask'],s['points'])>0))>settings.max_text_overlap:reason='OCR_TEXT_OVERLAP'
        elif source=='pdf' and any(k in s.get('layer','').upper() for k in ['DIM','A-FUR','A-TOI','A-SHE','A-TE','실명'.upper()]):reason='NONSTRUCTURAL_LAYER_HINT'
        if reason:
            if audit_sink is not None:audit_sink(s,'REJECTED',reason)
            rejected[reason]+=1
            if len(examples)<200 or reason not in {x['reason'] for x in examples}:
                if line and line[3]-line[2]>=minimum:examples.append({'reason':reason,'source':s})
            continue
        if audit_sink is not None:audit_sink(s,'ACCEPTED_FOR_MERGE','EXISTING_LINE_FILTER_PASSED')
        axis,n,a,b=line;group=round(n/settings.merge_tolerance_px)
        groups[(axis,group,round(s['stroke_px'],1) if source=='pdf' else 0)].append((a,b,n,s))
    merged=[]
    for (axis,group,width),entries in sorted(groups.items()):
        entries.sort(key=lambda v:v[0]);parts=[]
        for a,b,n,s in entries:
            if parts and a<=parts[-1][1]+settings.merge_tolerance_px:
                parts[-1][1]=max(parts[-1][1],b);parts[-1][2].append(n);parts[-1][3].append(s)
            else:parts.append([a,b,[n],[s]])
        for start,end,ns,ss in parts:
            n=float(np.median(ns));points=axis_points(axis,n,start,end)
            merged.append({'id':f'{source}:line:{len(merged):05d}','axis':axis,'normal':n,'start':start,'end':end,
                'points':points,'stroke_px':float(np.median([s['stroke_px'] for s in ss])),
                'raster_support_fraction':float(np.mean(sample_line(raster['visible_distance'],points)<=settings.raster_distance_px)),
                'dark_raster_support_fraction':float(np.mean(sample_line(raster['distance'],points)<=settings.raster_distance_px)),
                'source_ids':[s['id'] for s in ss],'layers':sorted(set(s.get('layer','') for s in ss))})
    return merged,{'rejected_counts':dict(rejected),'examples':examples}


def pair_boundaries(lines,raster,height,settings,*,audit_sink=None):
    by_axis=defaultdict(list)
    for line in lines:by_axis[line['axis']].append(line)
    pairs=[];rejected=Counter();examples=[]
    for axis,ls in by_axis.items():
        ls.sort(key=lambda x:x['normal'])
        for i,a in enumerate(ls):
            for b in ls[i+1:]:
                thickness=b['normal']-a['normal']
                if thickness>height*settings.max_pair_text_height_ratio:break
                if thickness<settings.min_pair_thickness_px:continue
                start=max(a['start'],b['start']);end=min(a['end'],b['end']);overlap=end-start
                if overlap<height*settings.min_line_text_height_ratio:continue
                ratio=overlap/min(a['end']-a['start'],b['end']-b['start'])
                if ratio<settings.min_overlap_ratio:continue
                reason=None
                if overlap<thickness*settings.min_wall_length_thickness_ratio:reason='SHORT_WIDE_PATCH_NOT_A_WALL_SEGMENT'
                elif min(a['stroke_px'],b['stroke_px'])<settings.min_boundary_stroke_px:reason='THIN_PARALLEL_NOT_ENOUGH_WALL_EVIDENCE'
                elif min(a['raster_support_fraction'],b['raster_support_fraction'])<settings.min_boundary_support:reason='BOUNDARY_NOT_VISIBLE'
                pad=min(2,thickness/4)
                poly=rectangle_for(axis,a['normal']+pad,b['normal']-pad,start,end)
                box=np.array(poly);x1,y1=np.floor(box.min(axis=0)).astype(int);x2,y2=np.ceil(box.max(axis=0)).astype(int)
                x1=max(0,x1);y1=max(0,y1);x2=min(raster['ink'].shape[1],x2);y2=min(raster['ink'].shape[0],y2)
                area=raster['ink'][y1:y2+1,x1:x2+1]
                density=float(np.mean(area>0)) if area.size else 0
                if reason is None and max(a['stroke_px'],b['stroke_px'])<1.15 and density<.30:reason='WEAK_STROKE_AND_LOW_STRIP_SUPPORT'
                if reason:
                    if audit_sink is not None:audit_sink(dict(status='REJECTED',reason=reason,boundary_ids=[a['id'],b['id']],polygon=rectangle_for(axis,a['normal'],b['normal'],start,end)))
                    rejected[reason]+=1
                    if len(examples)<160:examples.append({'reason':reason,'boundary_ids':[a['id'],b['id']],
                        'polygon':rectangle_for(axis,a['normal'],b['normal'],start,end),'thickness_px':thickness,'interior_ink_fraction':density})
                    continue
                pairs.append({'axis':axis,'normal':(a['normal']+b['normal'])/2,'start':start,'end':end,
                    'thickness_px':thickness,'boundary_ids':[a['id'],b['id']],
                    'boundaries':[axis_points(axis,a['normal'],start,end),axis_points(axis,b['normal'],start,end)],
                    'polygon':rectangle_for(axis,a['normal'],b['normal'],start,end),'interior_ink_fraction':density,
                    'boundary_support':[a['raster_support_fraction'],b['raster_support_fraction']],
                    'boundary_stroke_px':[a['stroke_px'],b['stroke_px']],
                    'score_for_deduplication':min(a['stroke_px'],b['stroke_px'])*2+density+ratio})
    accepted=[]
    for p in sorted(pairs,key=lambda p:p['score_for_deduplication'],reverse=True):
        duplicate=False
        for q in accepted:
            if q['axis']!=p['axis']:continue
            a=max(p['start'],q['start']);b=min(p['end'],q['end'])
            normal_overlap=min(p['normal']+p['thickness_px']/2,q['normal']+q['thickness_px']/2)-max(p['normal']-p['thickness_px']/2,q['normal']-q['thickness_px']/2)
            if (b-a)>.75*min(p['end']-p['start'],q['end']-q['start']) and normal_overlap>.75*min(p['thickness_px'],q['thickness_px']):duplicate=True;break
        if not duplicate:accepted.append(p)
        if audit_sink is not None:audit_sink(dict(status='DEDUPLICATED' if duplicate else 'ACCEPTED',
            reason='OVERLAPPING_PARALLEL_PROPOSAL' if duplicate else 'EXISTING_BOUNDARY_PAIR_FILTER_PASSED',
            boundary_ids=p['boundary_ids'],polygon=p['polygon'],
            duplicate_of_boundary_ids=q['boundary_ids'] if duplicate else []))
    return sorted(accepted,key=lambda p:(p['axis'],p['normal'],p['start'])),{'rejected_counts':dict(rejected),'examples':examples,'before_deduplication':len(pairs)}


def group_walls(pairs,role,height):
    walls=[]
    for index,pair in enumerate(pairs):
        pair={**pair,'observation_id':f'{role}:parallel_pair:{index:04d}'}
        group=None
        for wall in walls:
            if wall['axis']!=pair['axis'] or abs(wall['normal']-pair['normal'])>1.8:continue
            if abs(wall['thickness_px']-pair['thickness_px'])>max(2,wall['thickness_px']*.22):continue
            if min(abs(pair['start']-p['end']) for p in wall['pieces'])>height*12 and not any(pair['start']<=p['end'] and pair['end']>=p['start'] for p in wall['pieces']):continue
            group=wall;break
        if group is None:
            group={'id':f'{role}:Wall:{len(walls):04d}','axis':pair['axis'],'normal':pair['normal'],
                'thickness_px':pair['thickness_px'],'pieces':[]};walls.append(group)
        group['pieces'].append(pair)
    return walls


def wall_intervals(wall):
    intervals=[]
    for p in sorted(wall['pieces'],key=lambda p:p['start']):
        if intervals and p['start']<=intervals[-1][1]+1.5:intervals[-1][1]=max(intervals[-1][1],p['end'])
        else:intervals.append([p['start'],p['end']])
    return intervals


def arc_evidence(arc,axis,n,start,end,raster,height,thickness):
    center=arc['center'];normal_index=1 if axis=='H' else 0;axial_index=1-normal_index
    width=end-start;radius=arc['radius']
    if not .80<radius/max(width,1)<1.20:return None
    normal_error=abs(center[normal_index]-n)
    # Hinge must lie inside the observed wall band, with rasterization tolerance.
    # A nearby swing at a corner does not license choosing either wall direction.
    if normal_error>thickness/2+1.5:return None
    endpoint_error=min(abs(center[axial_index]-start),abs(center[axial_index]-end))
    if endpoint_error>max(height*.7,width*.18):return None
    curve=np.asarray(arc['polyline']);x=np.clip(np.rint(curve[:,0]).astype(int),0,raster['distance'].shape[1]-1);y=np.clip(np.rint(curve[:,1]).astype(int),0,raster['distance'].shape[0]-1)
    curve_support=float(np.mean(raster['visible_distance'][y,x]<2.0))
    leaf_support=max(float(np.mean(sample_line(raster['visible_distance'],[center,p])<2.5)) for p in arc['endpoints'])
    if curve_support<.70 or leaf_support<.65:return None
    return {'arc_id':arc['id'],'radius_px':radius,'gap_width_px':width,'normal_offset_px':normal_error,
        'hinge_to_bracket_distance_px':endpoint_error,'curve_raster_support':curve_support,'radial_leaf_support':leaf_support,
        'score':normal_error+endpoint_error+abs(radius-width),'claim':'quarter-circle and visible radial line near a structural gap; not GT'}


def track_evidence(lines,axis,n,start,end,thickness):
    tracks=[]
    width=end-start
    for line in lines:
        if line['axis']!=axis or abs(line['normal']-n)>max(2,thickness/2+1):continue
        overlap=max(0,min(end,line['end'])-max(start,line['start']))/max(width,1)
        if overlap>.65 and line['raster_support_fraction']>.70:
            if not any(abs(line['normal']-other['normal'])<1.2 for other in tracks):tracks.append(line)
    return {'line_ids':[l['id'] for l in tracks],'count':len(tracks),'normals':[l['normal'] for l in tracks],
        'claim':'parallel track/glazing/frame candidates; not sufficient alone to distinguish sliding door from window'}


def gap_observation(wall,start,end,lines,arcs,raster,height,settings,used_arcs,origin,other_wall=None):
    axis=wall['axis'];n=wall['normal'];thickness=wall['thickness_px'];width=end-start
    if not height*1.25<=width<=height*settings.max_opening_text_height_ratio:return None
    line=axis_points(axis,n,start,end)
    center_solid=float(np.mean(sample_line(raster['ink'],line)>0))
    possible=[]
    for arc in arcs:
        if arc['id'] in used_arcs:continue
        e=arc_evidence(arc,axis,n,start,end,raster,height,thickness)
        if e:possible.append(e)
    best=min(possible,key=lambda e:e['score']) if possible else None
    tracks=track_evidence(lines,axis,n,start,end,thickness)
    caps=[]
    for pos in [start,end]:
        cap=axis_points('V' if axis=='H' else 'H',pos,n-thickness/2,n+thickness/2)
        caps.append(float(np.mean(sample_line(raster['distance'],cap)<2.0)))
    eligible=bool(best) or tracks['count']>=2 or (min(caps)>.70 and center_solid<.25 and origin=='BRACKETED_WALL_GAP')
    return {'host_wall_ids':[wall['id']]+([other_wall['id']] if other_wall else []),
        'axis':axis,'normal':n,'start':start,'end':end,'width_px':width,'host_thickness_px':thickness,
        'logical_segment':line,'polygon':rectangle_for(axis,n-thickness/2,n+thickness/2,start,end),
        'origin':origin,'arc':best,'tracks':tracks,'bracket_end_cap_support':caps,'center_ink_fraction':center_solid,
        'eligible_logical_boundary':eligible,'logical_boundary_claim':'conditional space separator, not a physical filled wall',
        'failure_reasons':[] if eligible else ['Gap has insufficient arc, track or matching end-cap evidence; no automatic closure.']}


def opening_observations(walls,lines,arcs,raster,height,settings,role):
    eligible_arcs=[a for a in arcs if height*.8<a['radius']<height*12 and settings.min_arc_angle<a['angle_degrees']<settings.max_arc_angle and a['radial_rms_px']<a['radius']*settings.arc_fit_error_ratio]
    gaps=[];used=set()
    for wall in walls:
        intervals=wall_intervals(wall)
        for a,b in zip(intervals,intervals[1:]):
            gap=gap_observation(wall,a[1],b[0],lines,eligible_arcs,raster,height,settings,used,'BRACKETED_WALL_GAP')
            if gap:gaps.append(gap)
    # Assign each swing to its best bracketed gap before considering thresholds.
    for gap in sorted(gaps,key=lambda g:g['arc']['score'] if g['arc'] else 1e9):
        if gap['arc']:
            if gap['arc']['arc_id'] in used:gap['arc']=None
            else:used.add(gap['arc']['arc_id'])
    # Opening ending at a perpendicular wall, with actual tracks or a swing.
    for wall in walls:
        for interval in wall_intervals(wall):
            for endpoint,sign in [(interval[0],-1),(interval[1],1)]:
                candidates=[]
                for other in walls:
                    if other['axis']==wall['axis']:continue
                    if not any(p['start']-2<=wall['normal']<=p['end']+2 for p in other['pieces']):continue
                    target=other['normal']-sign*other['thickness_px']/2
                    if not height*1.25<(target-endpoint)*sign<height*settings.max_opening_text_height_ratio:continue
                    a,b=sorted([endpoint,target])
                    covered=sum(max(0,min(b,e)-max(a,s)) for s,e in wall_intervals(wall))
                    if covered>.10*(b-a):continue
                    gap=gap_observation(wall,a,b,lines,eligible_arcs,raster,height,settings,used,'TRACK_OR_SWING_TO_PERPENDICULAR_WALL',other)
                    if gap and (gap['arc'] or gap['tracks']['count']>=2):candidates.append(gap)
                if candidates:
                    gap=min(candidates,key=lambda g:g['width_px']);gaps.append(gap)
                    if gap['arc']:used.add(gap['arc']['arc_id'])
    # A visible double-line sill can cross a door opening. Existing bracketed
    # gaps take precedence over reinterpreting a continuous band at a corner.
    for arc in eligible_arcs:
        if arc['id'] in used:continue
        candidates=[]
        for wall in walls:
            normal_index=1 if wall['axis']=='H' else 0;axial_index=1-normal_index
            for endpoint in arc['endpoints']:
                if abs(endpoint[normal_index]-arc['center'][normal_index])>arc['radius']*.12:continue
                start,end=sorted([arc['center'][axial_index],endpoint[axial_index]])
                covered=sum(max(0,min(end,b)-max(start,a)) for a,b in wall_intervals(wall))/(end-start)
                if covered<.65:continue
                gap=gap_observation(wall,start,end,lines,[arc],raster,height,settings,used,'ARC_OVER_VISIBLE_THRESHOLD')
                if gap and gap['arc']:candidates.append(gap)
        if candidates:
            best=min(candidates,key=lambda g:g['arc']['score']);gaps.append(best);used.add(arc['id'])
    dedup=[]
    for gap in gaps:
        if any(gap['axis']==g['axis'] and abs(gap['normal']-g['normal'])<3 and abs(gap['start']-g['start'])<5 and abs(gap['end']-g['end'])<5 for g in dedup):continue
        gap['id']=f'{role}:opening_observation:{len(dedup):03d}'
        gap['eligible_logical_boundary']=bool(gap['arc']) or gap['tracks']['count']>=2 or (min(gap['bracket_end_cap_support'])>.70 and gap['center_ink_fraction']<.25 and gap['origin']=='BRACKETED_WALL_GAP')
        crossbars=[l['id'] for l in lines if l['axis']!=gap['axis'] and gap['start']+2<l['normal']<gap['end']-2
            and l['start']<=gap['normal']+gap['host_thickness_px']/2 and l['end']>=gap['normal']-gap['host_thickness_px']/2
            and l['end']-l['start']<gap['host_thickness_px']*2+4 and l['raster_support_fraction']>.70]
        gap['transverse_frame_line_ids']=crossbars
        gap['kind']='Door' if gap['arc'] else 'Opening'
        if not gap['arc'] and gap['origin']=='BRACKETED_WALL_GAP' and gap['tracks']['count']>=3 and crossbars:gap['kind']='Window'
        gap['kind_uncertainty']='Swing geometry supports a Door hypothesis; actual architectural role and connectivity are not VERIFIED.' if gap['arc'] else 'Parallel tracks may be glazing, frame or sliding door; Window subtype, if proposed, remains ambiguous.'
        dedup.append(gap)
    assigned={g['arc']['arc_id'] for g in dedup if g['arc']}
    for arc in eligible_arcs:
        arc['host_assignment_status']='HOSTED_PROPOSED' if arc['id'] in assigned else 'UNRESOLVED_HOST_OR_ORIENTATION'
        if arc['id'] not in assigned:arc['uncertainty']='Visible arc does not establish an opening plane/host; no Door connectivity generated from it.'
    return dedup,eligible_arcs


def interpreted_wall_pieces(wall,openings):
    result=[]
    for piece in wall['pieces']:
        intervals=[(piece['start'],piece['end'])];cuts=[]
        for opening in openings:
            if opening['origin']!='ARC_OVER_VISIBLE_THRESHOLD' or not opening['eligible_logical_boundary'] or wall['id'] not in opening['host_wall_ids']:continue
            remaining=[]
            for a,b in intervals:
                x,y=max(a,opening['start']),min(b,opening['end'])
                if x>=y:remaining.append((a,b));continue
                cuts.append(opening['id'])
                if x>a:remaining.append((a,x))
                if y<b:remaining.append((y,b))
            intervals=remaining
        for a,b in intervals:
            if b-a<.25:continue
            result.append({**piece,'start':a,'end':b,
                'polygon':rectangle_for(piece['axis'],piece['normal']-piece['thickness_px']/2,piece['normal']+piece['thickness_px']/2,a,b),
                'interpretation_cut_by':cuts,'endpoint_semantics':'observed interval or interpretation cut, not a verified physical endpoint'})
    return result


def barrier_masks(walls,openings,shape):
    physical=np.zeros(shape,np.uint8);owner=np.zeros(shape,np.int32)
    for i,wall in enumerate(walls,1):
        wall['physical_pieces']=interpreted_wall_pieces(wall,openings)
        for piece in wall['physical_pieces']:
            polygon=np.rint(piece['polygon']).astype(np.int32)
            cv2.fillPoly(physical,[polygon],255);cv2.fillPoly(owner,[polygon],i)
    logical=np.zeros(shape,np.uint8);opening_owner=np.zeros(shape,np.int32)
    for index,opening in enumerate(openings,1):
        if opening['eligible_logical_boundary']:
            a,b=np.rint(opening['logical_segment']).astype(int)
            cv2.line(logical,tuple(a),tuple(b),255,3)
            cv2.line(opening_owner,tuple(a),tuple(b),index,3)
    total=cv2.bitwise_or(physical,logical)
    n,components,stats,centers=cv2.connectedComponentsWithStats(255-total,connectivity=4)
    return {'physical':physical,'logical':logical,'total':total,'wall_owner':owner,
        'components':components,'component_stats':stats,'component_centers':centers,'component_count':n,'opening_owner':opening_owner}


def seed_for_label(polygon,barriers,raster,height):
    p=np.asarray(polygon,float);center=p.mean(axis=0);h,w=barriers['total'].shape
    ref_point=np.rint(center).astype(int)
    if not(0<=ref_point[0]<w and 0<=ref_point[1]<h):return None,'OCR label outside original image'
    component=int(barriers['components'][ref_point[1],ref_point[0]])
    if component==0:return None,'OCR label overlaps interpreted structure; no seed assumed'
    distance=cv2.distanceTransform(255-barriers['total'],cv2.DIST_L2,5)
    x1,y1=np.floor(p.min(axis=0)-height*3).astype(int);x2,y2=np.ceil(p.max(axis=0)+height*3).astype(int)
    x1=max(0,x1);y1=max(0,y1);x2=min(w,x2);y2=min(h,y2)
    ys,xs=np.mgrid[y1:y2,x1:x2]
    valid=(barriers['components'][ys,xs]==component)&(raster['ink'][ys,xs]==0)&(raster['text_mask'][ys,xs]==0)&(distance[ys,xs]>height*.4)
    if not np.any(valid):return None,'No clear interior seed outside text and interpreted walls'
    score=np.minimum(distance[ys,xs],height*2)-.45*np.hypot(xs-center[0],ys-center[1])
    score[~valid]=-np.inf;i=np.unravel_index(np.argmax(score),score.shape)
    return {'point':[int(xs[i]),int(ys[i])],'component_id':component,'clearance_to_barrier_px':float(distance[ys[i],xs[i]]),
        'method':'clear non-text raster point in the same geometry component as the label; label bbox is not a boundary'},None


def component_polygon(component,barriers):
    mask=(barriers['components']==component).astype(np.uint8)*255
    contours,hierarchy=cv2.findContours(mask,cv2.RETR_CCOMP,cv2.CHAIN_APPROX_SIMPLE)
    if hierarchy is None:return None
    outer=[];holes=[]
    for c,entry in zip(contours,hierarchy[0]):
        points=cv2.approxPolyDP(c,1.25,True).reshape(-1,2).tolist()
        (outer if entry[3]<0 else holes).append(points)
    return {'outer_rings':outer,'holes':holes,'derivation':'connected component of physical barrier + evidenced conditional logical separators; not raw ink flood fill'}


def partial_boundaries(seed,walls,barriers,settings):
    p=np.asarray(seed['point'],float);limit=min(barriers['total'].shape)*settings.boundary_search_page_fraction
    hit_ids=set();rays=[]
    for angle in np.linspace(0,2*math.pi,settings.boundary_ray_count,endpoint=False):
        direction=np.array([math.cos(angle),math.sin(angle)])
        points=p+np.arange(1,limit,.75)[:,None]*direction
        valid=(points[:,0]>=0)&(points[:,0]<barriers['total'].shape[1])&(points[:,1]>=0)&(points[:,1]<barriers['total'].shape[0])
        points=points[valid];q=np.floor(points).astype(int)
        hits=np.where(barriers['total'][q[:,1],q[:,0]]>0)[0] if len(q) else []
        if len(hits):
            point=q[hits[0]];owner=int(barriers['wall_owner'][point[1],point[0]])
            if owner:hit_ids.add(walls[owner-1]['id'])
            rays.append({'angle_rad':float(angle),'hit_native':point.tolist(),'wall_id':walls[owner-1]['id'] if owner else None,
                'opening_index':int(barriers['opening_owner'][point[1],point[0]]) or None,
                'kind':'OBSERVED_PHYSICAL_CANDIDATE' if owner else 'CONDITIONAL_LOGICAL_BOUNDARY'})
        else:rays.append({'angle_rad':float(angle),'hit_native':None,'kind':'UNKNOWN_BEYOND_SEARCH_OR_IMAGE'})
    return sorted(hit_ids),rays


def junction_observations(walls,role):
    results=[]
    for i,a in enumerate(walls):
        for b in walls[i+1:]:
            if a['axis']==b['axis']:continue
            h,v=(a,b) if a['axis']=='H' else (b,a)
            if not any(p['start']-v['thickness_px']/2-1<=v['normal']<=p['end']+v['thickness_px']/2+1 for p in h.get('physical_pieces',h['pieces'])):continue
            if not any(p['start']-h['thickness_px']/2-1<=h['normal']<=p['end']+h['thickness_px']/2+1 for p in v.get('physical_pieces',v['pieces'])):continue
            results.append({'id':f'{role}:junction:{len(results):03d}','point':[v['normal'],h['normal']],
                'wall_ids':[h['id'],v['id']],'claim':'intersection/proximity of candidate wall bands, not a verified architectural joint',
                'geometry_status':'SUPPORTED','architectural_status':'PROPOSED'})
    return results


def component_is_closed(stats,shape,min_area):
    x,y,w,h,area=map(int,stats)
    return x>0 and y>0 and x+w<shape[1] and y+h<shape[0] and area>=min_area


def relation_error(relation,by_id):
    predicate=relation.get('predicate');subject=by_id.get(relation.get('subject_id'))
    objects=[by_id.get(k) for k in relation.get('object_ids',[relation.get('object_id')])]
    if subject is None or not objects or any(o is None for o in objects):return 'MISSING_ENTITY'
    if any(o['id']==subject['id'] for o in objects):return 'SELF_RELATION'
    if any(o['frame_id']!=subject['frame_id'] for o in objects):return 'CROSS_FRAME_RELATION'
    s=subject['type'];types=[o['type'] for o in objects]
    allowed={'labels':s=='TextRegion' and len(types)==1 and types[0] in ['Space','Symbol'],
        'boundedBy':s=='Space' and all(t in ['Wall','Opening','Door','Window'] for t in types),
        'hosts':s=='Wall' and all(t in ['Opening','Door','Window'] for t in types),
        'adjacentTo':s=='Space' and all(t=='Space' for t in types),
        'connects':s=='Door' and len(types)==2 and all(t=='Space' for t in types) and len({o['id'] for o in objects})==2,
        'refersTo':s=='Symbol' and len(types)==1 and types[0] in ['Opening','Door','Window']}
    if not allowed.get(predicate,False):return 'INCOMPATIBLE_ENTITY_TYPES_OR_PREDICATE'
    if predicate=='connects':
        evidence=relation.get('opening_and_two_sides_evidence')
        if not isinstance(evidence,dict) or not all(evidence.get(k) for k in ['opening_observation_id','arc','side_sample_points_native','side_component_ids','side_space_ids','conditional_logical_separator']):return 'MISSING_OPENING_AND_TWO_SIDES_EVIDENCE'
        try:
            samples=np.asarray(evidence['side_sample_points_native'],float)
            if samples.shape!=(2,2) or not np.isfinite(samples).all():return 'INVALID_TWO_SIDES_SAMPLE_COORDINATES'
        except (TypeError,ValueError):return 'INVALID_TWO_SIDES_SAMPLE_COORDINATES'
        if set(evidence['side_space_ids'])!={o['id'] for o in objects} or len(set(evidence['side_component_ids']))!=2 or any(not c for c in evidence['side_component_ids']):return 'INCONSISTENT_TWO_SIDES_EVIDENCE'
    if predicate=='refersTo' and not relation.get('direct_pointer_evidence'):return 'MISSING_DIRECT_POINTER_EVIDENCE'
    return None
