"""PoC 2 local wall/attachment hypotheses. No registration or model calls.

Observed intervals, uncertainty and rejected alternatives are retained. A line
or a geometric contact never by itself verifies an architectural wall.
"""
from collections import defaultdict
from dataclasses import dataclass,asdict,replace
import math
import hashlib

import cv2
import numpy as np

from space_grounding_geometry import axis_points,axis_segment,rectangle_for,sample_line,merge_lines,Settings


@dataclass(frozen=True)
class RefinementSettings:
    version:str='geometry_topology_refinement/2.1-installation-shapes'
    pixel_tolerance:float=1.5
    min_visibility:float=.80
    min_pair_overlap:float=.55
    min_pair_length_text_ratio:float=.45
    min_aspect:float=2.0
    max_thickness_quantile_factor:float=1.3
    mode_log_bandwidth:float=.22
    thin_dominant_ratio:float=.60
    graph_node_tolerance:float=1.5


def weighted_quantile(values,weights,q):
    order=np.argsort(values);v=np.asarray(values)[order];w=np.asarray(weights)[order]
    return float(np.interp(q,np.cumsum(w)/max(float(np.sum(w)),1e-12),v))


def thickness_distribution(pairs,lines,height):
    vals=[float(p['thickness_px']) for p in pairs if p['end']>p['start']]
    weights=[float(p['end']-p['start']) for p in pairs if p['end']>p['start']]
    strokes=[float(x) for p in pairs for x in p['boundary_stroke_px']]
    if not vals:
        return {'status':'NO_MEASURED_THICKNESS','modes':[],'dominant_px':None,'thin_upper_px':None,
            'max_pair_distance_px':height*3,'strong_stroke_px':float(np.quantile([l['stroke_px'] for l in lines],.75)) if lines else 1.0,
            'values_px':[],'weights_native_length_px':[]}
    tolerance=max(.5,float(np.median(strokes))*.45);groups=[]
    for value,weight in sorted(zip(vals,weights)):
        if groups and abs(value-groups[-1]['center'])<=tolerance:
            g=groups[-1];g['center']=(g['center']*g['weight']+value*weight)/(g['weight']+weight);g['weight']+=weight;g['count']+=1
        else:groups.append({'center':value,'weight':weight,'count':1})
    total=sum(weights);modes=[{'thickness_px':g['center'],'length_weight':g['weight'],'count':g['count']} for g in groups if g['weight']>=total*.025]
    dominant=max(modes,key=lambda g:g['length_weight'])['thickness_px'] if modes else float(np.median(vals))
    return {'status':'MEASURED_FROM_OWN_ROLE_BASELINE_BOUNDARIES','modes':modes,'dominant_px':dominant,
        'thin_upper_px':dominant*.60,'weighted_quantiles_px':{str(q):weighted_quantile(vals,weights,q) for q in [.1,.25,.5,.75,.95]},
        'max_pair_distance_px':max(vals)*1.15,'strong_stroke_px':float(np.quantile(strokes,.25)),
        'values_px':vals,'weights_native_length_px':weights,
        'meaning':'measured hypothesis distribution, not a universal wall thickness or calibrated architectural truth'}


def mode_affinity(width,distribution,settings):
    modes=distribution['modes']
    return max((math.exp(-abs(math.log(width/m['thickness_px']))/settings.mode_log_bandwidth) for m in modes),default=0.)


def point_segment_distance(p,a,b):
    p=np.asarray(p,float);a=np.asarray(a,float);b=np.asarray(b,float);d=b-a
    t=np.clip(np.dot(p-a,d)/max(float(np.dot(d,d)),1e-12),0,1);q=a+t*d
    return float(np.linalg.norm(p-q)),q.tolist()


def point_polygon_distance(point,polygon):
    p=np.asarray(polygon,np.float32)
    inside=cv2.pointPolygonTest(p,tuple(map(float,point)),False)>=0
    choices=[point_segment_distance(point,a,b) for a,b in zip(polygon,polygon[1:]+polygon[:1])]
    distance,q=min(choices,key=lambda x:x[0]);return (0. if inside else distance),q


def line_flags(line,all_lines,distribution,height):
    tags=' '.join(line.get('layers',[])).upper();flags=[]
    if any(t in tags for t in ['DIM','CEN','HID','SHE','A-TE','실명']):flags.append('DIMENSION_PROJECTION_OR_ANNOTATION_LAYER_HINT')
    if any(t in tags for t in ['FUR','TOI']):flags.append('FURNITURE_FIXTURE_LAYER_HINT')
    if any(t in tags for t in ['A-WIN','A-DOR','A-DOR-C','A-FIN']):flags.append('FRAME_DOOR_WINDOW_OR_FINISH_HINT')
    peers=[l for l in all_lines if l['axis']==line['axis'] and abs(l['normal']-line['normal'])<=1.5 and l['id']!=line['id']]
    intervals=sorted([(line['start'],line['end'])]+[(l['start'],l['end']) for l in peers if l['end']>line['start']-height*4 and l['start']<line['end']+height*4])
    gaps=[b[0]-a[1] for a,b in zip(intervals,intervals[1:]) if b[0]>a[1]+1.5]
    if len(gaps)>=3 and np.std(gaps)/max(np.mean(gaps),1)<.5 and (line['end']-line['start'])<height*3:flags.append('REPEATED_DASH_LIKE_INTERVALS')
    return flags


def candidate_pairs(lines,raster,distribution,height,settings):
    pairs=[];rejected=[];by_axis=defaultdict(list)
    minimum_stroke=max(.10,distribution['strong_stroke_px']*.13)
    for l in lines:
        if l['stroke_px']>=minimum_stroke and l['raster_support_fraction']>=settings.min_visibility:by_axis[l['axis']].append(l)
    for axis,ls in by_axis.items():
        ls=sorted(ls,key=lambda l:l['normal'])
        for i,a in enumerate(ls):
            for b in ls[i+1:]:
                width=b['normal']-a['normal']
                if width>distribution['max_pair_distance_px']:break
                if width<max(.8,min(a['stroke_px'],b['stroke_px'])):continue
                start=max(a['start'],b['start']);end=min(a['end'],b['end']);length=end-start
                if length<height*settings.min_pair_length_text_ratio or length<width*settings.min_aspect:continue
                overlap=length/min(a['end']-a['start'],b['end']-b['start'])
                if overlap<settings.min_pair_overlap:continue
                balance=min(a['stroke_px'],b['stroke_px'])/max(a['stroke_px'],b['stroke_px'])
                affinity=mode_affinity(width,distribution,settings)
                flags=sorted(set(a.get('refinement_flags',[])+b.get('refinement_flags',[])))
                inner=[l for l in ls if a['normal']+settings.pixel_tolerance<l['normal']<b['normal']-settings.pixel_tolerance and min(l['end'],end)-max(l['start'],start)>.7*length and l['stroke_px']>=min(a['stroke_px'],b['stroke_px'])*.85]
                x1,y1,x2,y2=(int(start),int(a['normal']),math.ceil(end),math.ceil(b['normal'])) if axis=='H' else (int(a['normal']),int(start),math.ceil(b['normal']),math.ceil(end))
                patch=raster['ink'][max(0,y1):min(y2+1,raster['ink'].shape[0]),max(0,x1):min(x2+1,raster['ink'].shape[1])]
                density=float(np.mean(patch>0)) if patch.size else 0.
                text_patch=raster['text_mask'][max(0,y1):min(y2+1,raster['ink'].shape[0]),max(0,x1):min(x2+1,raster['ink'].shape[1])]
                text_fraction=float(np.mean(text_patch>0)) if text_patch.size else 0.
                score=2*balance+1.5*affinity+math.log1p(length/height)+min(2,min(a['stroke_px'],b['stroke_px'])/max(distribution['strong_stroke_px'],.1))+min(.6,density)-1.4*len(inner)
                reason=None
                if text_fraction>.15:reason='TEXT_OR_LABEL_FRAME_INTERIOR_NOT_WALL'
                elif any('DIMENSION' in f or 'FURNITURE' in f or 'DASH' in f for f in flags):reason='NONSTRUCTURAL_OR_DASH_CONTEXT'
                elif len(inner)>0:reason='SPANS_OTHER_COMPATIBLE_BOUNDARY_NOT_LOCAL_BAND'
                elif affinity<.18 and density<.30:reason='RARE_GAP_LOW_SOLID_SUPPORT'
                pair={'axis':axis,'normal':(a['normal']+b['normal'])/2,'n1':a['normal'],'n2':b['normal'],'start':start,'end':end,
                    'thickness_px':width,'boundary_ids':[a['id'],b['id']],'boundary_stroke_px':[a['stroke_px'],b['stroke_px']],
                    'boundary_support':[a['raster_support_fraction'],b['raster_support_fraction']],
                    'polygon':rectangle_for(axis,a['normal'],b['normal'],start,end),'flags':flags,'score':score,
                    'mode_affinity':affinity,'stroke_balance':balance,'interior_ink_fraction':density,
                    'interior_text_fraction':text_fraction,
                    'source_overlap_length_px':length,'source_ids':a['source_ids']+b['source_ids']}
                if reason:rejected.append({**pair,'rejection_reason':reason})
                else:pairs.append(pair)
    return pairs,rejected


def select_local_bands(pairs,tolerance=1.5):
    """Suppress overlapping intervals, never a whole long band for a short patch."""
    selected=[];rejected=[]
    for i,p in enumerate(pairs):
        competitors=[];cuts={p['start'],p['end']}
        for j,q in enumerate(pairs):
            if i==j or q['axis']!=p['axis']:continue
            overlap_normal=min(p['n2'],q['n2'])-max(p['n1'],q['n1'])
            if overlap_normal<=.7*min(p['thickness_px'],q['thickness_px']):continue
            a=max(p['start'],q['start']);b=min(p['end'],q['end'])
            if b-a<=tolerance:continue
            if q['score']>p['score']+1e-9 or (abs(q['score']-p['score'])<=1e-9 and j<i):competitors.append(q);cuts.update([a,b])
        kept=[]
        for a,b in zip(sorted(cuts),sorted(cuts)[1:]):
            if b-a<.25:continue
            if any(q['start']<=(a+b)/2<=q['end'] for q in competitors):continue
            if kept and abs(kept[-1][1]-a)<1e-6:kept[-1][1]=b
            else:kept.append([a,b])
        if not kept:rejected.append({**p,'rejection_reason':'LOCALLY_OUTRANKED_PARALLEL_ALTERNATIVE'})
        for a,b in kept:
            selected.append({**p,'start':a,'end':b,'polygon':rectangle_for(p['axis'],p['n1'],p['n2'],a,b),
                'endpoints_semantics':'visible interval termination or local arbitration cut, physical endpoint not VERIFIED',
                'arbitration_cut':a!=p['start'] or b!=p['end']})
    return selected,rejected


def endpoint_contacts(band,others,raster,tolerance):
    result=[]
    for label,pos in [('start',band['start']),('end',band['end'])]:
        points=axis_points('V' if band['axis']=='H' else 'H',pos,band['n1'],band['n2'])
        matches=[]
        for other in others:
            if other is band or other.get('id')==band.get('id') or other['axis']==band['axis']:continue
            for point in points:
                distance,q=point_polygon_distance(point,other['polygon'])
                if distance<=tolerance:
                    visible=float(np.mean(sample_line(raster['visible_distance'],[point,q])<=tolerance))
                    if visible>=.7:matches.append({'other_segment_id':other.get('id'),'point_native':point,'contact_native':q,'gap_px':distance,'visible_fraction':visible})
        result.append({'end':label,'contacts':matches})
    return result


def annotation_context(band,lines,ocr,rectangles,height):
    """Own-role text/line/frame evidence, never an inferred building ROI."""
    aidx=0 if band['axis']=='H' else 1;nidx=1-aidx;text_ids=[];interior=[]
    for text in ocr:
        polygon=np.asarray(text['polygon_native']);center=polygon.mean(axis=0)
        content=''.join(c for c in text['text'] if c.isalnum())
        if band['start']<=center[aidx]<=band['end'] and band['n1']+1.5<center[nidx]<band['n2']-1.5:interior.append(text['id'])
        digits=sum(c.isdigit() for c in content)
        if digits>=2 and digits>=len(content)*.5 and band['start']-height<center[aidx]<band['end']+height and band['n1']-height*2<center[nidx]<band['n2']+height*2:text_ids.append(text['id'])
    peers=[l for l in lines if l['axis']==band['axis'] and abs(l['normal']-band['normal'])<height*3
        and min(l['end'],band['end'])-max(l['start'],band['start'])>.55*(band['end']-band['start'])]
    levels={round(l['normal'],1) for l in peers}
    frames=[]
    for rectangle in rectangles:
        x1,y1,x2,y2=rectangle['bbox'];w,h=x2-x1,y2-y1
        if min(w,h)<=0 or max(w,h)>height*8 or w*h>height*height*16:continue
        expected=(y1,y2,x1,x2) if band['axis']=='H' else (x1,x2,y1,y2)
        if max(abs(a-b) for a,b in zip([band['n1'],band['n2'],band['start'],band['end']],expected))<1.6:frames.append(rectangle['id'])
    return {'interior_text_region_ids':interior,'nearby_numeric_text_region_ids':text_ids,
        'parallel_context_line_ids':[l['id'] for l in peers],'parallel_levels':len(levels),
        'small_closed_frame_ids':frames,
        'dimension_pattern_suspected':len(text_ids)>=2 and len(levels)>=3 and band['interior_ink_fraction']<.25,
        'meaning':'annotation/context contradiction only; neither an architectural label nor dimension GT'}


def reconstruct_bands(lines,raster,baseline_pairs,height,role,settings=None,ocr=(),rectangles=()):
    settings=settings or RefinementSettings();distribution=thickness_distribution(baseline_pairs,lines,height)
    for line in lines:line['refinement_flags']=line_flags(line,lines,distribution,height)
    pairs,rejected=candidate_pairs(lines,raster,distribution,height,settings)
    selected,competitive=select_local_bands(pairs,settings.pixel_tolerance);rejected+=competitive
    # First establish measured strong bands; thin bands need structural context.
    for i,b in enumerate(sorted(selected,key=lambda p:(p['axis'],p['normal'],p['start']))):
        b['id']=f'{role}:Segment2:{i:04d}';b['status']='PROPOSED';b['geometry_status']='SUPPORTED'
        b['annotation_context']=annotation_context(b,lines,ocr,rectangles,height)
    strong=[b for b in selected if min(b['boundary_stroke_px'])>=distribution['strong_stroke_px']*.95 and b['stroke_balance']>.90 and b['mode_affinity']>.25 and not any('FRAME' in f for f in b['flags'])
        and not b['annotation_context']['dimension_pattern_suspected'] and not b['annotation_context']['interior_text_region_ids']]
    thin_limit=distribution['thin_upper_px'] or height
    for b in selected:
        contacts=endpoint_contacts(b,strong,raster,settings.pixel_tolerance)
        b['endpoint_contacts']=contacts;b['is_thin_hypothesis']=b['thickness_px']<=thin_limit
        both=all(c['contacts'] for c in contacts);one=any(c['contacts'] for c in contacts)
        frame_hint=any('FRAME' in f for f in b['flags'])
        annotation=b['annotation_context']
        tracks=[l for l in lines if l['axis']==b['axis'] and b['n1']-b['thickness_px']<=l['normal']<=b['n2']+b['thickness_px']
            and min(l['end'],b['end'])-max(l['start'],b['start'])>.7*(b['end']-b['start']) and l['stroke_px']<distribution['strong_stroke_px']*.8
            and not any('DIMENSION' in f or 'FURNITURE' in f for f in l['refinement_flags'])]
        track_normals=set(round(l['normal'],1) for l in tracks)
        if min(b['boundary_stroke_px'])<distribution['strong_stroke_px']*.65 and len(track_normals)>=3:frame_hint=True;b['flags'].append('MULTI_TRACK_CONTEXT_NOT_A_SOLID_WALL')
        if annotation['dimension_pattern_suspected'] or annotation['interior_text_region_ids']:
            b['classification']='ANNOTATION_CONTEXT_UNCERTAIN';b['space_barrier_eligible']=False
        elif annotation['small_closed_frame_ids'] and not one:
            b['classification']='SMALL_CLOSED_FRAME_UNCERTAIN';b['space_barrier_eligible']=False
        elif frame_hint:
            b['classification']='FRAME_OR_FINISH_BAND';b['space_barrier_eligible']=False
        elif b in strong:
            b['classification']='MEASURED_WALL_BAND';b['space_barrier_eligible']=True
        elif b['is_thin_hypothesis'] and (both or (one and b['interior_ink_fraction']>.35 and b['stroke_balance']>.85)):
            b['classification']='THIN_WALL_CONTEXT_SUPPORTED';b['space_barrier_eligible']=True
        else:b['classification']='UNKNOWN_PARALLEL_BAND';b['space_barrier_eligible']=False
        b['uncertainties']=['Architectural wall type PROPOSED; parallel/raster/contact facts are dependent geometric evidence.']
        if not b['space_barrier_eligible']:b['uncertainties'].append('Excluded from physical space barrier until frame/partition semantics are reviewed.')
    singles=[]
    for l in lines:
        if l['end']-l['start']<height*2 or l['raster_support_fraction']<.9 or l['refinement_flags']:continue
        if any(l['id'] in b['boundary_ids'] for b in selected):continue
        pseudo={'axis':l['axis'],'normal':l['normal'],'n1':l['normal'],'n2':l['normal'],'start':l['start'],'end':l['end'],
            'polygon':rectangle_for(l['axis'],l['normal']-.25,l['normal']+.25,l['start'],l['end'])}
        contacts=endpoint_contacts(pseudo,strong,raster,settings.pixel_tolerance)
        if any(c['contacts'] for c in contacts):singles.append({'id':f'{role}:SinglePartition2:{len(singles):03d}',
            'axis':l['axis'],'points_native':l['points'],'source_line_id':l['id'],'endpoint_contacts':contacts,
            'thickness_px':None,'classification':'UNKNOWN_SINGLE_LINE_PARTITION','status':'PROPOSED','space_barrier_eligible':False})
    return selected,singles,rejected,distribution


def visible_raw_lines(raw_segments,raster,height,role,settings):
    result=[]
    for s in raw_segments:
        p=np.asarray(s['points']);length=float(np.linalg.norm(p[1]-p[0]));a=axis_segment(s['points'])
        if not a or length<.5 or s.get('path_type')=='f':continue
        if np.any(p<0) or np.any(p[:,0]>=raster['ink'].shape[1]) or np.any(p[:,1]>=raster['ink'].shape[0]):continue
        tags=s.get('layer','').upper()
        if any(x in tags for x in ['DIM','FUR','TOI','CEN','HID','TE1','TE2','실명']):continue
        if np.mean(sample_line(raster['text_mask'],s['points'])>0)>.15:continue
        visible=float(np.mean(sample_line(raster['visible_distance'],s['points'])<=settings.pixel_tolerance))
        if visible<.8:continue
        result.append({**s,'id':role+':'+s['id'],'axis':a[0],'normal':a[1],'start':a[2],'end':a[3],'visibility':visible})
    return result


def trace_jamb(start,host_boundary_points,raw,raster,axis,n,axial_position,width,tolerance):
    """Visible local segment graph from hinge/frame endpoint to a wall bracket.

    Only the jamb neighborhood is searched: no arbitrary nearest-wall link.
    """
    normal_index=1 if axis=='H' else 0;axial_index=1-normal_index
    span=max(width*1.5,4)
    pool=[]
    for s in raw:
        p=np.asarray(s['points'])
        low=np.empty(2);high=np.empty(2)
        low[normal_index]=min(n,start[normal_index])-span;high[normal_index]=max(n,start[normal_index])+span
        low[axial_index]=axial_position-span;high[axial_index]=axial_position+span
        idx=0 if s['axis']=='H' else 1;normal=1-idx
        if not low[normal]<=s['normal']<=high[normal]:continue
        a=max(s['start'],low[idx]);b=min(s['end'],high[idx])
        if b-a<.25:continue
        pool.append({**s,'points':axis_points(s['axis'],s['normal'],a,b),'local_clip_is_not_physical_endpoint':True})
    if len(pool)>180:return {'connected':False,'reason':'AMBIGUOUS_DENSE_JAMB_GRAPH','candidate_count':len(pool)}
    starts=[i for i,s in enumerate(pool) if point_segment_distance(start,*s['points'])[0]<=tolerance]
    targets={i for i,s in enumerate(pool) if any(point_segment_distance(p,*s['points'])[0]<=tolerance for p in host_boundary_points)}
    visited={i:None for i in starts};queue=list(starts);found=None
    while queue:
        i=queue.pop(0)
        if i in targets:found=i;break
        for j,b in enumerate(pool):
            if j in visited:continue
            close=[]
            for p in pool[i]['points']:
                d,q=point_segment_distance(p,*b['points'])
                if d<=tolerance:close.append((p,q))
            for p in b['points']:
                d,q=point_segment_distance(p,*pool[i]['points'])
                if d<=tolerance:close.append((p,q))
            if close and any(np.mean(sample_line(raster['visible_distance'],[p,q])<=tolerance)>=.7 for p,q in close):visited[j]=i;queue.append(j)
    if found is None:return {'connected':False,'reason':'NO_VISIBLE_LOCAL_JAMB_PATH','tested_segment_count':len(pool)}
    path=[];cursor=found
    while cursor is not None:path.append(pool[cursor]['id']);cursor=visited[cursor]
    return {'connected':True,'source_segment_ids':list(reversed(path)),'tested_segment_count':len(pool),
        'start_native':list(map(float,start)),'host_boundary_points_native':host_boundary_points,
        'claim':'local visible segment connectivity only, architectural host remains a hypothesis'}


def gap_candidates(bands,height,role,settings):
    active=[b for b in bands if b['space_barrier_eligible']];gaps=[]
    for a in active:
        for b in active:
            if a['id']==b['id']:continue
            axis=a['axis'];same=axis==b['axis']
            if same:
                if abs(a['normal']-b['normal'])>settings.pixel_tolerance or abs(a['thickness_px']-b['thickness_px'])>max(settings.pixel_tolerance,.25*a['thickness_px']):continue
                start=a['end'];end=b['start']
                if not height<end-start<height*12:continue
                points_b=axis_points('V' if axis=='H' else 'H',end,b['n1'],b['n2'])
            else:
                if not b['start']-settings.pixel_tolerance<=a['normal']<=b['end']+settings.pixel_tolerance:continue
                start=a['end'];end=b['n1']
                if not height<end-start<height*12:continue
                points_b=axis_points('V' if axis=='H' else 'H',end,a['n1'],a['n2'])
            if any(c['id'] not in [a['id'],b['id']] and c['axis']==axis and abs(c['normal']-a['normal'])<settings.pixel_tolerance and max(0,min(c['end'],end)-max(c['start'],start))>(end-start)*.5 for c in active):continue
            gap={'axis':axis,'normal':a['normal'],'n1':a['n1'],'n2':a['n2'],'start':start,'end':end,'width_px':end-start,
                'host_segment_ids':[a['id'],b['id']],'host_thickness_px':a['thickness_px'],
                'bracket_points_native':[axis_points('V' if axis=='H' else 'H',start,a['n1'],a['n2']),points_b],
                'logical_segment':axis_points(axis,a['normal'],start,end),
                'polygon':rectangle_for(axis,a['n1'],a['n2'],start,end),'gap_kind':'COLLINEAR_GAP' if same else 'WALL_END_TO_PERPENDICULAR_BAND'}
            if not any(g['axis']==axis and abs(g['normal']-gap['normal'])<2 and abs(g['start']-start)<2 and abs(g['end']-end)<2 for g in gaps):gaps.append(gap)
    # Opposite end -> perpendicular wall is covered by a mirrored search.
    for a in active:
        for b in active:
            if a['axis']==b['axis'] or not b['start']-settings.pixel_tolerance<=a['normal']<=b['end']+settings.pixel_tolerance:continue
            start=b['n2'];end=a['start']
            if not height<end-start<height*12:continue
            if any(c['axis']==a['axis'] and abs(c['normal']-a['normal'])<settings.pixel_tolerance and c['start']<end and c['end']>start and c['id']!=a['id'] for c in active):continue
            gap={'axis':a['axis'],'normal':a['normal'],'n1':a['n1'],'n2':a['n2'],'start':start,'end':end,'width_px':end-start,
                'host_segment_ids':[b['id'],a['id']],'host_thickness_px':a['thickness_px'],
                'bracket_points_native':[axis_points('V' if a['axis']=='H' else 'H',start,a['n1'],a['n2']),axis_points('V' if a['axis']=='H' else 'H',end,a['n1'],a['n2'])],
                'logical_segment':axis_points(a['axis'],a['normal'],start,end),'polygon':rectangle_for(a['axis'],a['n1'],a['n2'],start,end),'gap_kind':'PERPENDICULAR_BAND_TO_WALL_START'}
            if not any(g['axis']==gap['axis'] and abs(g['normal']-gap['normal'])<2 and abs(g['start']-start)<2 and abs(g['end']-end)<2 for g in gaps):gaps.append(gap)
    by_id={b['id']:b for b in bands};dedup=[]
    for g in sorted(gaps,key=lambda g:sum(by_id[k]['source_overlap_length_px'] for k in g['host_segment_ids']),reverse=True):
        equivalent=next((p for p in dedup if p['axis']==g['axis'] and abs(p['normal']-g['normal'])<settings.pixel_tolerance and max(abs(p['start']-g['start']),abs(p['end']-g['end']))<=max(settings.pixel_tolerance*2,min(p['host_thickness_px'],g['host_thickness_px'])*.35)),None)
        if equivalent:
            equivalent.setdefault('bracket_alternatives',[]).append(g)
            equivalent['width_interval_px']=[min(g['width_px'],equivalent.get('width_interval_px',[equivalent['width_px']])[0]),max(g['width_px'],equivalent.get('width_interval_px',[0,equivalent['width_px']])[-1])]
        else:dedup.append(g)
    for i,g in enumerate(sorted(dedup,key=lambda g:(g['axis'],g['normal'],g['start']))):g['id']=f'{role}:Opening2:{i:03d}'
    for g in dedup:
        # A slightly offset gap plane may pass through an existing band. Check
        # physical band overlap, not only equality of its center coordinate.
        g['contradicting_solid_band_ids']=[b['id'] for b in active if b['axis']==g['axis']
            and b['n1']-settings.pixel_tolerance<=g['normal']<=b['n2']+settings.pixel_tolerance
            and min(b['end'],g['end'])-max(b['start'],g['start'])>max(settings.pixel_tolerance*2,g['width_px']*.2)]
    return dedup


def installation_shapes(lines, arcs, raw, raster, height, role, settings, *, audit_sink=None):
    """Recover drawing assemblies without requiring an already-empty wall gap.

    Reuse visible curve/leaf/jamb tracing and observed frame tracks. These are
    INSTALLATION_DRAWING_ONLY: never subtract occupancy, close a room, assert a
    host wall, or infer traversability. OCR markers/codes are not inputs.
    """
    tolerance=settings.pixel_tolerance;found=[]
    raw=[s if 'visibility' in s else dict(s,visibility=float(np.mean(
        sample_line(raster['visible_distance'],s['points'])<=tolerance))) for s in raw]
    def report(kind,status,reason,points,refs):
        if audit_sink:audit_sink(dict(kind=kind,status=status,reason=reason,points=points,source_ids=refs))
    def make(axis,start,end,lo,hi,caps,identity):
        return dict(id=f'{role}:Opening2:shape:'+hashlib.sha256(str(identity).encode()).hexdigest()[:16],
            axis=axis,normal=(lo+hi)/2,n1=lo,n2=hi,start=start,end=end,width_px=end-start,
            host_segment_ids=[],host_thickness_px=hi-lo,bracket_points_native=[c['points'] for c in caps],
            logical_segment=axis_points(axis,(lo+hi)/2,start,end),polygon=rectangle_for(axis,lo,hi,start,end),
            gap_kind='INSTALLATION_SHAPE_WITH_UNRESOLVED_WALL_GAP',contradicting_solid_band_ids=[],
            gap_status='INSTALLATION_DRAWING_ONLY_GAP_UNRESOLVED',status='PROPOSED',geometry_status='SUPPORTED',
            eligible_logical_boundary=False,installation_shape_only=True,
            uncertainty='Visible installation drawing only; wall host, physical gap, passage and product type remain unresolved.',
            door_proposals=[],track_line_ids=[],track_lines_native=[],transverse_frame_ids=list(dict.fromkeys(
                r for c in caps for r in c.get('source_ids',[c['id']]))),
            bracket_cap_visibility=[c['visibility'] for c in caps])
    # Do not make a quarter-circle into a door unless the perpendicular leaf
    # and BOTH actual short frame stiles are visible and locally connected.
    for arc in arcs:
        refs=[arc['id']];radius=arc['radius'];center=arc['center']
        if not (65<arc['angle_degrees']<115 and radius>height*1.25 and arc['radial_rms_px']<radius*.035):continue
        if any(k in arc.get('layer','').upper() for k in ['DIM','FUR','TOI','A-TE','CEN','HID']):
            report('DoorShapeAttempt','REJECTED','NONSTRUCTURAL_CURVE_STYLE',arc['polyline'],refs);continue
        visibility=float(np.mean(sample_line(raster['visible_distance'],arc['polyline'])<=tolerance))
        if visibility<settings.min_visibility:
            report('DoorShapeAttempt','REJECTED','SWING_NOT_VISIBLE',arc['polyline'],refs);continue
        proposals=[]
        for axis in ['H','V']:
            along=0 if axis=='H' else 1;normal=1-along
            ends=[p for p in arc['endpoints'] if abs(p[normal]-center[normal])<=tolerance]
            other=[p for p in arc['endpoints'] if abs(p[along]-center[along])<=tolerance]
            if len(ends)!=1 or len(other)!=1:continue
            leaf=float(np.mean(sample_line(raster['visible_distance'],[center,other[0]])<=tolerance))
            if leaf<settings.min_visibility:continue
            starts=sorted([center,ends[0]],key=lambda p:p[along]);pools=[]
            for point in starts:
                choices=[s for s in raw if s['axis']!=axis and
                    max(.5,2*s['stroke_px'])<=s['end']-s['start']<radius*.5 and
                    s['start']-tolerance<=point[normal]<=s['end']+tolerance and
                    abs(s['normal']-point[along])<=max(tolerance,(s['end']-s['start'])*.5)]
                pools.append(choices)
            paired=[]
            for a in pools[0]:
                for b in pools[1]:
                    if a['id']==b['id'] or b['normal']<=a['normal']:continue
                    if max(abs(a['start']-b['start']),abs(a['end']-b['end']))>tolerance:continue
                    lo=max(a['start'],b['start']);hi=min(a['end'],b['end'])
                    if hi-lo<=tolerance or not .7<radius/(b['normal']-a['normal'])<1.25:continue
                    proofs=[trace_jamb(p,c['points'],raw,raster,axis,(lo+hi)/2,c['normal'],hi-lo,tolerance)
                            for p,c in zip(starts,[a,b])]
                    if not all(p['connected'] for p in proofs):continue
                    paired.append((a,b,lo,hi,proofs))
            if paired:
                # Rank measured stile agreement, not marker proximity. Every
                # compatible stile alternative is retained in the evidence.
                paired.sort(key=lambda p:(max(abs(p[0]['start']-p[1]['start']),abs(p[0]['end']-p[1]['end'])),
                                           -min(p[0]['visibility'],p[1]['visibility']),-(p[3]-p[2]),p[0]['id'],p[1]['id']))
                a,b,lo,hi,proofs=paired[0]
                gap=make(axis,a['normal'],b['normal'],lo,hi,[a,b],[arc['id'],axis])
                gap.update(kind='Door',door_proposals=[dict(arc_id=arc['id'],hinge_native=center,
                    swing_polyline_native=arc['polyline'],radius_px=radius,curve_visibility=visibility,
                    radial_leaf_visibility=leaf,jamb_connections=proofs,opening_plane_axis=axis,
                    hinge_offset_from_band_px=abs(center[normal]-(lo+hi)/2))],
                    recovery_evidence=dict(method='VISIBLE_SWING_PERPENDICULAR_LEAF_AND_PAIRED_SHORT_FRAME_STILES',
                        source_ids=[arc['id'],a['id'],b['id']],stile_alternatives=[dict(source_ids=[p[0]['id'],p[1]['id']],
                            bracket_points_native=[p[0]['points'],p[1]['points']]) for p in paired],
                        physical_wall_thickness_status='UNKNOWN'))
                proposals.append(gap)
        if len(proposals)==1:
            gap=proposals[0];found.append(gap)
            report('DoorShapeAttempt','SUPPORTED','VISIBLE_SWING_LEAF_AND_BOTH_FRAME_STILES',arc['polyline'],gap['recovery_evidence']['source_ids'])
        else:
            report('DoorShapeAttempt','REJECTED','AMBIGUOUS_OPENING_PLANE' if proposals else 'PAIRED_VISIBLE_FRAME_STILES_NOT_ESTABLISHED',arc['polyline'],refs)
    # Window/slider/grille drawings have independent profile evidence. The
    # old path only measured tracks AFTER a wall gap was generated, excluding
    # large assemblies and frames intentionally withheld as solid barriers.
    carriers=[l for l in lines if l['end']-l['start']>height*2 and l['raster_support_fraction']>=settings.min_visibility
              and not any(any(k in f for k in ['DIMENSION','FURNITURE','REPEATED_DASH']) for f in l.get('refinement_flags',[]))]
    # Window end stiles are commonly several contiguous PDF items. The wall
    # channel's text-scale short-line filter must not erase their topology.
    # Reuse its merging, with short observed inputs admitted in THIS channel.
    cap_lines,_=merge_lines(raw,raster,height,replace(Settings(),min_line_text_height_ratio=0),source='pdf')
    caps=[dict(l,id=l['source_ids'][0],visibility=l['raster_support_fraction']) for l in cap_lines
          if l['raster_support_fraction']>=settings.min_visibility]
    examined=set()
    for seed in carriers:
        peers=[l for l in carriers if l['axis']==seed['axis'] and
               abs(l['start']-seed['start'])<=3*tolerance and abs(l['end']-seed['end'])<=3*tolerance and
               seed['normal']-tolerance*.25<=l['normal']<=seed['normal']+height*2]
        unique=[]
        for line in sorted(peers,key=lambda l:(l['normal'],-l['end']+l['start'],l['id'])):
            if not unique or line['normal']-unique[-1]['normal']>tolerance*.5:unique.append(line)
        if len(unique)<3:continue
        # Nearby sill/slab lines must not force the whole neighborhood to be
        # one frame. Test observed rail subsets against their actual end caps.
        for count in range(3,len(unique)+1):
            group=unique[:count];lo,hi=group[0]['normal'],group[-1]['normal'];depth=hi-lo
            start=max(l['start'] for l in group);end=min(l['end'] for l in group)
            if depth<=tolerance or end-start<max(height*2,depth*5):continue
            key=(seed['axis'],round(start),round(end),round(lo),round(hi))
            if key in examined:continue
            examined.add(key)
            transverse=[s for s in caps if s['axis']!=seed['axis'] and
                        lo+tolerance>=s['start'] and s['end']>=hi-tolerance and s['end']-s['start']<=depth*2+2*tolerance]
            left=[s for s in transverse if abs(s['normal']-start)<=3*tolerance]
            right=[s for s in transverse if abs(s['normal']-end)<=3*tolerance]
            ribs=[s for s in transverse if start+3*tolerance<s['normal']<end-3*tolerance]
            points=axis_points(seed['axis'],(lo+hi)/2,start,end);refs=[l['id'] for l in group]
            if not left or not right or not ribs:
                report('ProfileAssemblyAttempt','REJECTED','BOTH_END_STILES_AND_INTERIOR_TRANSVERSE_RIB_REQUIRED',points,refs);continue
            a=max(left,key=lambda s:s['visibility']);b=max(right,key=lambda s:s['visibility'])
            gap=make(seed['axis'],a['normal'],b['normal'],lo,hi,[a,b],key)
            gap.update(kind='Window',track_line_ids=refs,track_lines_native=[l['points'] for l in group],
                transverse_frame_ids=list(dict.fromkeys(r for s in [a,b,*ribs] for r in s['source_ids'])),
                recovery_evidence=dict(method='PARALLEL_VISIBLE_PROFILES_TWO_END_STILES_AND_INTERIOR_RIB',
                    source_ids=[*refs,*(r for s in [a,b,*ribs] for r in s['source_ids'])],
                    type_alternatives=['Window','SlidingDoor','Grille'],physical_wall_thickness_status='UNKNOWN'))
            found.append(gap);report('ProfileAssemblyAttempt','SUPPORTED','VISIBLE_PROFILES_STILES_AND_INTERIOR_RIB',points,gap['recovery_evidence']['source_ids'])
    dedup=[]
    for shape in sorted(found,key=lambda g:-(g['n2']-g['n1'])):
        same=next((g for g in dedup if g['kind']==shape['kind']=='Window' and g['axis']==shape['axis']
            and max(abs(g['start']-shape['start']),abs(g['end']-shape['end']))<=tolerance
            and max(0,min(g['n2'],shape['n2'])-max(g['n1'],shape['n1']))>=.8*min(g['n2']-g['n1'],shape['n2']-shape['n1'])),None)
        if same:
            same['recovery_evidence'].setdefault('profile_alternatives',[]).append(shape['recovery_evidence'])
        else:dedup.append(shape)
    return dedup


def opening_grounding(bands,lines,arcs,raw,raster,height,role,settings=None,*,audit_sink=None):
    settings=settings or RefinementSettings();gaps=gap_candidates(bands,height,role,settings);arc_attempts=[]
    for gap in gaps:
        nidx=1 if gap['axis']=='H' else 0;aidx=1-nidx;proposals=[]
        for arc in ([] if gap['contradicting_solid_band_ids'] else arcs):
            if not 65<arc['angle_degrees']<115 or arc['radial_rms_px']>arc['radius']*.035 or not .7<arc['radius']/gap['width_px']<1.25:continue
            center=arc['center']
            if abs(center[nidx]-gap['normal'])>gap['host_thickness_px']*1.5+settings.pixel_tolerance:continue
            plane_endpoint=min(arc['endpoints'],key=lambda p:abs(p[nidx]-center[nidx]))
            axial=sorted([center[aidx],plane_endpoint[aidx]])
            if max(abs(axial[0]-gap['start']),abs(axial[1]-gap['end']))>max(gap['host_thickness_px']*1.5,height*.5):continue
            poly=np.asarray(arc['polyline']);q=np.rint(poly).astype(int)
            if np.any(q<0) or np.any(q[:,0]>=raster['ink'].shape[1]) or np.any(q[:,1]>=raster['ink'].shape[0]):continue
            curve_support=float(np.mean(raster['visible_distance'][q[:,1],q[:,0]]<=settings.pixel_tolerance))
            if curve_support<.8:continue
            leaf=max(float(np.mean(sample_line(raster['visible_distance'],[center,p])<=settings.pixel_tolerance)) for p in arc['endpoints'])
            if leaf<.8:continue
            starts=[center,plane_endpoint];starts.sort(key=lambda p:p[aidx])
            proofs=[trace_jamb(p,brackets,raw,raster,gap['axis'],gap['normal'],pos,gap['host_thickness_px'],settings.pixel_tolerance) for p,brackets,pos in zip(starts,gap['bracket_points_native'],[gap['start'],gap['end']])]
            proof={'arc_id':arc['id'],'hinge_native':center,'swing_polyline_native':arc['polyline'],'radius_px':arc['radius'],
                'curve_visibility':curve_support,'radial_leaf_visibility':leaf,'jamb_connections':proofs,
                'opening_plane_axis':gap['axis'],'hinge_offset_from_band_px':abs(center[nidx]-gap['normal'])}
            arc_attempts.append({'opening_id':gap['id'],**proof,'both_jambs_connected':all(p['connected'] for p in proofs)})
            if all(p['connected'] for p in proofs):proposals.append(proof)
        # Observed glazing/track geometry, distinct from OCR symbols/codes.
        tracks=[];ribs=[]
        for line in lines:
            if line['axis']==gap['axis'] and gap['n1']-settings.pixel_tolerance<=line['normal']<=gap['n2']+settings.pixel_tolerance:
                overlap=max(0,min(line['end'],gap['end'])-max(line['start'],gap['start']))/gap['width_px']
                if overlap>.65 and line['raster_support_fraction']>.8 and not any('DIMENSION' in f for f in line.get('refinement_flags',[])):
                    if not any(abs(line['normal']-p['normal'])<.75 for p in tracks):tracks.append(line)
        for s in raw:
            if s['axis']!=gap['axis'] and gap['start']+2<s['normal']<gap['end']-2 and s['start']<=gap['normal'] and s['end']>=gap['normal'] and s['end']-s['start']<=gap['host_thickness_px']*2+2:ribs.append(s['id'])
        cap_support=[float(np.mean(sample_line(raster['visible_distance'],points)<=settings.pixel_tolerance)) for points in gap['bracket_points_native']]
        failed_swing=any(a['opening_id']==gap['id'] and not a['both_jambs_connected'] for a in arc_attempts)
        window_pattern=len(tracks)>=3 and bool(ribs) and min(cap_support)>=.8 and not failed_swing and not gap['contradicting_solid_band_ids']
        gap.update(door_proposals=proposals,track_line_ids=[t['id'] for t in tracks],track_lines_native=[t['points'] for t in tracks],
            transverse_frame_ids=ribs,bracket_cap_visibility=cap_support,status='PROPOSED',geometry_status='SUPPORTED',
            kind='Door' if proposals else 'Window' if window_pattern else 'Opening',
            eligible_logical_boundary=bool(proposals) or window_pattern,
            uncertainty='Opening/host/subtype remain hypotheses; no nearest-code or nearest-arc approval.')
        if gap['contradicting_solid_band_ids']:
            gap['uncertainty']='SOLID_BAND_CONTRADICTS_GAP; retained rejected hypothesis, not a logical separator'
            gap['gap_status']='REJECTED_SOLID_OVERLAP'
        else:gap['gap_status']='PROPOSED_OBSERVED_BRACKETS'
    # A swing fitting multiple different host planes is not resolved by proximity.
    by_arc=defaultdict(list)
    for g in gaps:
        for p in g['door_proposals']:by_arc[p['arc_id']].append(g)
    for arc_id,items in by_arc.items():
        planes={(g['axis'],round(g['normal'],1),round(g['start']),round(g['end'])) for g in items}
        if len(planes)>1:
            for g in items:g['kind']='Opening';g['eligible_logical_boundary']=False;g['uncertainty']='MULTIPLE_GEOMETRIC_HOSTS_FOR_SWING; no nearest-wall tie break'
    recovered=installation_shapes(lines,arcs,raw,raster,height,role,settings,audit_sink=audit_sink)
    represented_arcs={p['arc_id'] for g in gaps if g['kind']=='Door' for p in g['door_proposals']}
    for shape in recovered:
        if shape['kind']=='Door' and shape['door_proposals'][0]['arc_id'] in represented_arcs:continue
        if shape['kind']=='Door':
            solid=[b['id'] for b in bands if b['space_barrier_eligible'] and b['axis']==shape['axis']
                and b['n1']-settings.pixel_tolerance<=shape['normal']<=b['n2']+settings.pixel_tolerance
                and min(b['end'],shape['end'])-max(b['start'],shape['start'])>max(settings.pixel_tolerance*2,shape['width_px']*.2)]
            if solid:
                # Preserve the visible drawing as a conflicting hypothesis;
                # never override a separately supported solid-wall interval.
                shape.update(kind='Opening',gap_status='REJECTED_SOLID_OVERLAP',contradicting_solid_band_ids=solid,
                             uncertainty='VISIBLE_SWING_CONFLICTS_WITH_SOLID_BAND; installation and physical gap unresolved')
        gaps.append(shape)
    for g in gaps:g['element_id']=g['id'].replace('Opening2',g['kind']+'2') if g['kind']!='Opening' else g['id']
    return gaps,arc_attempts


def topology(bands,openings,raster,role,settings=None):
    settings=settings or RefinementSettings();active=[b for b in bands if b['space_barrier_eligible']];nodes=[];edges=[];uncertain=[]
    for b in bands:
        nodes.append({'id':b['id'],'type':'WallSegment','status':'PROPOSED','space_barrier_eligible':b['space_barrier_eligible']})
        for end,pos in [('start',b['start']),('end',b['end'])]:nodes.append({'id':b['id']+':'+end,'type':'ObservedEndpoint','point_native':axis_points(b['axis'],b['normal'],pos,pos)[0],
            'physical_endpoint_status':'UNKNOWN','analysis_cut':b['arbitration_cut']})
    for i,a in enumerate(active):
        for b in active[i+1:]:
            if a['axis']==b['axis']:
                if max(abs(a['n1']-b['n1']),abs(a['n2']-b['n2']))>settings.pixel_tolerance:continue
                left,right=(a,b) if a['start']<b['start'] else (b,a);gap=right['start']-left['end']
                if not -.25<=gap<=settings.pixel_tolerance:continue
                traces=[axis_points(a['axis'],n,left['end'],right['start']) for n in [a['n1'],a['n2']]]
                visible=[float(np.mean(sample_line(raster['visible_distance'],p)<=settings.pixel_tolerance)) for p in traces]
                if min(visible)>.8:edges.append({'id':f'{role}:Continuation2:{len(edges):04d}','type':'OBSERVED_CONTINUATION','wall_segment_ids':[left['id'],right['id']],
                    'gap_px':gap,'boundary_visibility':visible,'status':'SUPPORTED','solid_union_geometry_created':False})
                continue
            h,v=(a,b) if a['axis']=='H' else (b,a);p=[v['normal'],h['normal']]
            lo_x,hi_x=max(h['start'],v['n1']),min(h['end'],v['n2'])
            lo_y,hi_y=max(h['n1'],v['start']),min(h['n2'],v['end'])
            if lo_x>hi_x+settings.pixel_tolerance or lo_y>hi_y+settings.pixel_tolerance:continue
            contacts_a=endpoint_contacts(a,[b],raster,settings.pixel_tolerance);contacts_b=endpoint_contacts(b,[a],raster,settings.pixel_tolerance)
            hit_a=any(c['contacts'] for c in contacts_a);hit_b=any(c['contacts'] for c in contacts_b)
            kind='L' if hit_a and hit_b else 'T' if hit_a or hit_b else 'X_UNCERTAIN'
            centerline_intersection=p
            contacts=[c['contact_native'] for group in [contacts_a,contacts_b] for end in group for c in end['contacts']]
            # A thin wall can terminate at the face of a thick wall without
            # reaching its centerline. The contact location is not that crossing.
            p=np.mean(contacts,axis=0).tolist() if contacts else [(lo_x+hi_x)/2,(lo_y+hi_y)/2]
            in_opening=any(g['eligible_logical_boundary'] and cv2.pointPolygonTest(np.array(g['polygon'],np.float32),tuple(p),False)>0 for g in openings)
            e={'id':f'{role}:Junction2:{len(edges)+len(uncertain):03d}','type':'WALL_JUNCTION','wall_segment_ids':[a['id'],b['id']],
                'point_native':p,'centerline_intersection_native':centerline_intersection,'junction_type':kind,'boundary_contacts':[contacts_a,contacts_b],
                'opening_intersects':in_opening,'status':'SUPPORTED' if (hit_a or hit_b) and not in_opening else 'PROPOSED',
                'meaning':'geometric contact support only; architectural junction not VERIFIED'}
            (edges if e['status']=='SUPPORTED' else uncertain).append(e)
    for g in openings:
        nodes.append({'id':g['id'],'type':'OpeningObservation','element_id':g['element_id'],'status':'PROPOSED'})
        for host in g['host_segment_ids']:
            rejected=g.get('gap_status')=='REJECTED_SOLID_OVERLAP'
            (uncertain if rejected else edges).append({'id':f'{g["id"]}:attachment:{host}','type':'WALL_OPENING_ATTACHMENT','wall_segment_id':host,
                'opening_id':g['id'],'element_id':g['element_id'],'status':'SUPPORTED' if g['kind']=='Door' and g['door_proposals'] else 'PROPOSED',
                'attachment_status':'REJECTED_SOLID_OVERLAP' if rejected else 'PROPOSED_GEOMETRIC_ATTACHMENT',
                'attachment_role':'HOST_ALIGNED_BAND' if next(b for b in bands if b['id']==host)['axis']==g['axis'] else 'PERPENDICULAR_JAMB_BRACKET_NOT_HOST',
                'evidence':g['door_proposals'] if g['kind']=='Door' else {'tracks':g['track_line_ids'],'caps':g['bracket_cap_visibility']}})
    return {'nodes':nodes,'edges':edges,'uncertain_connections':uncertain,
        'rule':'crossing alone is insufficient; endpoints, boundary contact and visible local paths required. Openings remain separate.'}
