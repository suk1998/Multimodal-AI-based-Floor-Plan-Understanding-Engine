"""Opening/host drawing geometry, no GT or fixed location input.

Search the whole user ROI from each source's own wall strokes. The global
transform is estimated by the preserved alignment engine, never refitted here.
"""
from dataclasses import dataclass,asdict
import hashlib,math
import cv2,numpy as np
from backend.wall_sections import read_strokes,merged_strokes,ink_support
from backend.structural_alignment import transform

VERSION="opening_drawing_geometry/1"

@dataclass(frozen=True)
class Settings:
    endpoint_tolerance_zoom:float=.6
    minimum_depth_zoom:float=4
    maximum_depth_zoom:float=24
    minimum_gap_zoom:float=12
    maximum_gap_zoom:float=180
    minimum_flank_zoom:float=4
    minimum_context_overlap_zoom:float=12
    context_guard_zoom:float=2
    minimum_ink:float=.85
    normal_tolerance_zoom:float=1.5


def corners(axis,left,right,lo,hi):
    p=np.array([[left,lo],[right,lo],[right,hi],[left,hi]],float)
    return p if axis==0 else p[:,::-1]


def bounds(p):
    p=np.asarray(p);return [*p.min(0).tolist(),*p.max(0).tolist()]


def find_openings(strokes,zoom,roi,gray,role,cfg=Settings()):
    lines=list(merged_strokes(strokes,zoom));eps=cfg.endpoint_tolerance_zoom*zoom
    accepted=[];audit=[]
    for axis in [0,1]:
        caps=[s for s in lines if s.axis!=axis and cfg.minimum_depth_zoom*zoom<=s.hi-s.lo<=cfg.maximum_depth_zoom*zoom]
        faces=[s for s in lines if s.axis==axis]
        for i,first in enumerate(caps):
            for second in caps[i+1:]:
                a,b=sorted([first,second],key=lambda s:s.normal)
                width=b.normal-a.normal
                if not cfg.minimum_gap_zoom*zoom<=width<=cfg.maximum_gap_zoom*zoom:continue
                if max(abs(a.lo-b.lo),abs(a.hi-b.hi))>eps:continue
                if width<1.5*(a.hi-a.lo):continue
                if any(a.normal+eps<c.normal<b.normal-eps and max(abs(c.lo-a.lo),abs(c.hi-a.hi))<=eps for c in caps):continue
                polygon=corners(axis,a.normal,b.normal,(a.lo+b.lo)/2,(a.hi+b.hi)/2)
                if any(cv2.pointPolygonTest(np.float32(roi),tuple(p),False)<0 for p in polygon):continue
                witnesses=[];reason=None
                for side,cap in [('left',a),('right',b)]:
                    for normal in [cap.lo,cap.hi]:
                        matches=[s for s in faces if abs(s.normal-normal)<=eps and
                            ((abs(s.hi-cap.normal)<=eps and cap.normal-s.lo>=cfg.minimum_flank_zoom*zoom) if side=='left'
                             else (abs(s.lo-cap.normal)<=eps and s.hi-cap.normal>=cfg.minimum_flank_zoom*zoom))]
                        if not matches:reason='MISSING_PAIRED_OUTWARD_HOST_FACE';break
                        witnesses.append(max(matches,key=lambda s:s.hi-s.lo))
                    if reason:break
                ident=role+':opening:'+hashlib.sha256(str((axis,a.refs,b.refs)).encode()).hexdigest()[:14]
                entry=dict(id=ident,role=role,axis=axis,bbox_native=bounds(polygon),jamb_refs=[list(a.refs),list(b.refs)],
                    status='REVIEW',reason=reason,source_refs=list(a.refs+b.refs))
                audit.append(entry)
                if reason:continue
                # A bold face across this band would contradict an observed gap.
                if any(abs(s.normal-n)<=eps and s.lo<a.normal+eps and s.hi>b.normal-eps for s in faces for n in [a.lo,a.hi]):
                    entry['reason']='CONTINUOUS_BOLD_FACE_ACROSS_GAP';continue
                support=[ink_support(gray,s.points()) for s in [a,b,*witnesses]]
                if any(s is None or s<cfg.minimum_ink for s in support):entry['reason']='INSUFFICIENT_ORIGINAL_HOST_INK';continue
                refs=list(dict.fromkeys([*a.refs,*b.refs,*(r for s in witnesses for r in s.refs)]))
                item=dict(id=ident,source_role=role,axis=axis,left=a.normal,right=b.normal,normal_lo=a.lo,normal_hi=a.hi,
                    polygon_native=polygon.tolist(),bbox_native=bounds(polygon),source_refs=refs,host_face_refs=[list(s.refs) for s in witnesses],
                    jamb_refs=[list(a.refs),list(b.refs)],
                    jamb_segments_native=[a.points(),b.points()],host_face_segments_native=[s.points() for s in witnesses],
                    source_ink_support=support,claim_status='SUPPORTED_GEOMETRIC_HYPOTHESIS',
                    physical_passage_status='UNKNOWN',installation_identity_status='UNKNOWN')
                accepted.append(item);entry.update(status='SUPPORTED_GEOMETRIC_HYPOTHESIS',reason='PAIRED_HOST_FACES_AND_OPPOSED_JAMBS',source_refs=refs)
    return accepted,audit


def context_pairs(strokes,zoom,box,gray,cfg):
    radius=max(4*(box[2]-box[0]),4*(box[3]-box[1]),60*zoom);center=np.array(box).reshape(2,2).mean(0)
    lines=list(merged_strokes(strokes,zoom));out=[]
    for axis in [0,1]:
        group=[s for s in lines if s.axis==axis and abs(s.normal-center[1-axis])<=radius]
        for i,a in enumerate(group):
            for b in group[i+1:]:
                depth=abs(a.normal-b.normal)
                if not cfg.minimum_depth_zoom*zoom<=depth<=cfg.maximum_depth_zoom*zoom:continue
                lo=max(a.lo,b.lo,center[axis]-radius);hi=min(a.hi,b.hi,center[axis]+radius)
                intervals=[(lo,hi)];guard=cfg.context_guard_zoom*zoom
                if max(a.normal,b.normal)>=box[1-axis]-guard and min(a.normal,b.normal)<=box[3-axis]+guard:
                    intervals=[(lo,min(hi,box[axis]-guard)),(max(lo,box[axis+2]+guard),hi)]
                for lo,hi in intervals:
                    if hi-lo<cfg.minimum_context_overlap_zoom*zoom:continue
                    points=[a.points(lo,hi),b.points(lo,hi)]
                    support=[ink_support(gray,p) for p in points]
                    if any(v is None or v<cfg.minimum_ink for v in support):continue
                    out.append(dict(axis=axis,normals=sorted([a.normal,b.normal]),interval=[lo,hi],points=points,
                                    source_refs=list(a.refs+b.refs),ink_support=support))
    return out


def local_registration(ref,target,pages,strokes,grays,alignment,cfg=Settings()):
    if alignment.get('status') not in ['PARTIAL','VERIFIED_ROI'] or alignment.get('matrix') is None:return dict(status='UNCONFIRMED',reason='NO_INDEPENDENT_GLOBAL_TRANSFORM')
    M=np.asarray(alignment['matrix']);inverse=np.linalg.inv(M)
    # This reader observes drawing-axis line bands; do not silently treat a
    # rotated/sheared band as the same axis-aligned opening.
    if max(abs(M[0,1]),abs(M[1,0]))>1e-6 or min(M[0,0],M[1,1])<=0:
        return dict(status='UNCONFIRMED',reason='OBLIQUE_OR_REFLECTED_MAPPING_NOT_SUPPORTED')
    holdout=[r for r in alignment.get('rows',[]) if r.get('usage')=='VALIDATION']
    pairs={p['id']:p for p in alignment.get('pairs',[])}
    if (not holdout or not all(r.get('passed') for r in holdout) or
        len({pairs[r['id']]['parent_structure_id'] for r in holdout if r['id'] in pairs})<2):
        return dict(status='UNCONFIRMED',reason='INDEPENDENT_GLOBAL_HOLDOUT_NOT_SUPPORTED')
    # Keep the previous independent validation hull; do not certify arbitrary extrapolation.
    hulls=alignment.get('coverage',{}).get('directional_validation_hulls_target',[])
    limit=alignment.get('coverage',{}).get('max_distance_outside_holdout_hull_px',0)
    if not hulls or any(cv2.pointPolygonTest(np.float32(h),tuple(p),True)<-limit for h in hulls for p in target['polygon_native']):
        return dict(status='UNCONFIRMED',reason='OUTSIDE_EXISTING_STRUCTURAL_SUPPORT_HULL')
    union=bounds([*target['polygon_native'],*transform(ref['polygon_native'],inverse).tolist()])
    ref_box=bounds(transform(corners(0,union[0],union[2],union[1],union[3]),M))
    for box,page in zip([ref_box,union],pages):
        if not (0<=box[0]<box[2]<page.width and 0<=box[1]<box[3]<page.height):
            return dict(status='UNCONFIRMED',reason='CORRESPONDING_LOCUS_OUTSIDE_NATIVE_IMAGE')
    guard=cfg.context_guard_zoom*pages[1].zoom
    for pair in alignment.get('pairs',[]):
        if pair.get('usage')!='FIT':continue
        for key in ['target_segment','target_other_face']:
            lo=np.min(pair[key],axis=0);hi=np.max(pair[key],axis=0)
            if np.all(hi>=np.array(union[:2])-guard) and np.all(lo<=np.array(union[2:])+guard):
                return dict(status='UNCONFIRMED',reason='CHANGED_LOCUS_INTERSECTS_ALIGNMENT_FIT_INTERVAL')
    r=context_pairs(strokes[0],pages[0].zoom,ref_box,grays[0],cfg)
    t=context_pairs(strokes[1],pages[1].zoom,union,grays[1],cfg)
    matches=[];tol=cfg.normal_tolerance_zoom*pages[0].zoom
    for a in r:
        for b in t:
            if a['axis']!=b['axis']:continue
            axis=a['axis'];mapped=transform(np.asarray(b['points']),M)
            residual=np.max(np.abs(np.sort(mapped[:,:,1-axis].mean(1))-a['normals']))
            lo=max(a['interval'][0],mapped[:,:,axis].min());hi=min(a['interval'][1],mapped[:,:,axis].max())
            if residual>tol or hi-lo<cfg.minimum_context_overlap_zoom*pages[0].zoom:continue
            matches.append(dict(axis=axis,Ref=a,Target=b,maximum_normal_residual_ref_px=float(residual),common_span_ref_px=float(hi-lo),
                center_distance_target_px=float(np.linalg.norm(np.mean(np.array(b['points']).reshape(-1,2),0)-np.array(union).reshape(2,2).mean(0)))))
    selected=[]
    for axis in [0,1]:
        pool=[m for m in matches if m['axis']==axis]
        if not pool:return dict(status='UNCONFIRMED',reason='MISSING_TWO_DIRECTIONAL_EXTERNAL_WALL_WITNESSES',witnesses=selected)
        selected.append(min(pool,key=lambda m:(m['maximum_normal_residual_ref_px'],m['center_distance_target_px'])))
    return dict(status='SUPPORTED_LOCAL_COORDINATE_CORRESPONDENCE',method='UNCHANGED_GLOBAL_TRANSFORM_AND_TWO_EXTERNAL_WALL_FACE_DIRECTIONS',
        matrix_target_to_ref=M.tolist(),witnesses=selected,core_used_for_fit=False,core_image_agreement_required=False,
        global_holdout_maximum_normal_residual_ref_px=alignment.get('wall_registration',{}).get('independent_max_normal_ref_px'))


def pair_openings(refs,targets,M,zoom):
    edges=[]
    for r in refs:
        for t in targets:
            if r['axis']!=t['axis']:continue
            axis=r['axis'];mapped=transform(t['polygon_native'],M);p=np.array(r['polygon_native']);delta=np.linalg.norm(p.mean(0)-mapped.mean(0))
            overlap=min(p[:,axis].max(),mapped[:,axis].max())-max(p[:,axis].min(),mapped[:,axis].min())
            if delta>10*zoom or overlap/min(r['right']-r['left'],t['right']-t['left'])<.65:continue
            error=max(abs(p[:,1-axis].min()-mapped[:,1-axis].min()),abs(p[:,1-axis].max()-mapped[:,1-axis].max()))
            if error>1.5*zoom:continue
            edges.append((float(delta+error),r,t))
    out=[]
    for edge in edges:
        _,r,t=edge;rr=sorted([e for e in edges if e[1]['id']==r['id']],key=lambda e:e[0]);tt=sorted([e for e in edges if e[2]['id']==t['id']],key=lambda e:e[0])
        if rr[0] is edge and tt[0] is edge and all(len(pool)==1 or pool[1][0]-pool[0][0]>=zoom for pool in [rr,tt]):out.append((r,t))
    return out


def assembly_features(opening,vector,gray,zoom,cfg=Settings()):
    axis=opening['axis'];lo,hi=opening['normal_lo'],opening['normal_hi'];left,right=opening['left'],opening['right'];features=[]
    for s in vector['lines']:
        if s['axis']!=axis or not lo-.1*zoom<=s['normal']<=hi+.1*zoom:continue
        a,b=max(left,s['lo']),min(right,s['hi'])
        if b-a<.45*(right-left):continue
        if not (left-.2*zoom<=s['lo'] and s['hi']<=right+.2*zoom):continue
        if s['source_evidence'].get('stroke_width_native',0)>=.65*zoom:continue
        points=[[a,s['normal']],[b,s['normal']]]
        if axis:points=[p[::-1] for p in points]
        ink=ink_support(gray,points)
        if ink is None or ink<cfg.minimum_ink:continue
        features.append(dict(source_ref=s['id'],points_native=points,normal_native=s['normal'],
            relative_depth=(s['normal']-lo)/(hi-lo),ink_support=ink,source_evidence=s['source_evidence']))
    return features


def compare_openings(pages,strokes,vectors,alignment,cfg=Settings()):
    for p,v,source in zip(pages,vectors,alignment['sources']):
        if v is None:return dict(candidates=[],source_candidates=[[],[]],upstream=[],reason='PDF_VECTOR_INPUT_UNAVAILABLE')
        if (v.get('source_sha256')!=p.source_sha256 or v.get('page')!=p.page_number or
            v.get('extraction_zoom')!=p.zoom or v.get('image_size')!=[p.width,p.height] or
            source['image']['source_sha256']!=p.source_sha256 or source['image_sha256']!=hashlib.sha256(p.png).hexdigest()):
            raise ValueError('Opening geometry source/frame mismatch')
    grays=[cv2.imdecode(np.frombuffer(p.png,np.uint8),0) for p in pages];roi=alignment['target_roi']['vertices']
    M=np.array(alignment['matrix']) if alignment.get('matrix') else None
    if M is None:return dict(candidates=[],source_candidates=[[],[]],reason='NO_TRANSFORM',upstream=[])
    rois=[transform(roi,M),roi];sources=[];audit=[]
    for role,p,s,g,r in zip(['Ref','Target'],pages,strokes,grays,rois):
        candidates,records=find_openings(s,p.zoom,r,g,role,cfg)
        binding=dict(source_role=role,frame_id=f'{role}:{p.source_sha256}:{p.page_number}:{p.zoom:g}:{p.width}x{p.height}',
                     source_sha256=p.source_sha256,page=p.page_number,extraction_zoom=p.zoom,image_size=[p.width,p.height])
        for item in candidates:item['source_binding']=binding
        sources.append(candidates)
        audit.append(dict(role=role,input_bold_stroke_count=len(s),merged_bold_stroke_count=len(list(merged_strokes(s,p.zoom))),
                          scope='GEOMETRICALLY_PLAUSIBLE_JAMB_PAIRS_AFTER_EXISTING_BOLD_STROKE_FILTER',
                          missing_scope='TEXT, CURVES, DASHED, THIN, UNPAIRED AND EARLIER DROPPED OBSERVATIONS NOT FULLY TRACED',records=records))
    # Own-source profiles are retained before any Ref/Target pairing, so an
    # unmatched assembly remains available to the independent grounding graph.
    source_features=[{o['id']:assembly_features(o,v,g,p.zoom,cfg) for o in items}
                     for items,v,g,p in zip(sources,vectors,grays,pages)]
    results=[]
    for r,t in pair_openings(*sources,M,pages[0].zoom):
        registration=local_registration(r,t,pages,strokes,grays,alignment,cfg)
        rf,tf=[features[o['id']] for o,features in zip([r,t],source_features)]
        axis=r['axis'];rj=np.array(r['jamb_segments_native']);tj=transform(np.array(t['jamb_segments_native']),M)
        deltas=(tj[:,:,axis].mean(1)-rj[:,:,axis].mean(1)).tolist()
        jamb_support=[]
        for original_ref,original_target,mapped_target in zip(rj,t['jamb_segments_native'],tj):
            jamb_support.append(dict(Ref_original=ink_support(grays[0],original_ref),
                Target_original=ink_support(grays[1],original_target),
                Ref_at_Target_locus=ink_support(grays[0],mapped_target),
                Target_at_Ref_locus=ink_support(grays[1],transform(original_ref,np.linalg.inv(M)))))
        unmatched=[]
        for side,features,other,im in [('Ref',rf,tf,grays[1]),('Target',tf,rf,grays[0])]:
            for f in features:
                pts=transform(f['points_native'],np.linalg.inv(M) if side=='Ref' else M)
                # Positive original ink and absence at the registered other locus.
                absence=ink_support(im,pts)
                if absence is not None and absence<.4:unmatched.append(dict(source_role=side,feature=f,other_locus_ink_support=absence))
        changed_jamb=any(abs(d)>cfg.normal_tolerance_zoom*pages[0].zoom and
            all(v is not None for v in s.values()) and min(s['Ref_original'],s['Target_original'])>=cfg.minimum_ink and
            max(s['Ref_at_Target_locus'],s['Target_at_Ref_locus'])<.4 for d,s in zip(deltas,jamb_support))
        changed_profile=bool(unmatched) and len(rf)>=2 and len(tf)>=2
        supported=registration['status'].startswith('SUPPORTED')
        state=('SUPPORTED_DRAWING_GEOMETRY_CHANGE' if changed_jamb or changed_profile else 'NO_DETECTED_DRAWING_DIFFERENCE') if supported else 'REVIEW'
        result=dict(id='pair:'+hashlib.sha256((r['id']+t['id']).encode()).hexdigest()[:14],Ref=r,Target=t,registration=registration,
            assembly_features={'Ref':rf,'Target':tf},jamb_displacements_ref_px=deltas,
            jamb_ink_support=jamb_support,
            opening_span_delta_ref_px=float((tj[1,:,axis].mean()-tj[0,:,axis].mean())-(rj[1,:,axis].mean()-rj[0,:,axis].mean())),
            changed_assembly_observations=unmatched,observed_jamb_change=changed_jamb,observed_profile_change=changed_profile,
            state=state,
            semantic_status='PROPOSED',geometry_GT_verification='NOT_PERFORMED',
            physical_product_replacement='UNKNOWN',opening_passage_status='UNKNOWN',GT_used=False)
        results.append(result)
    return dict(settings=asdict(cfg),source_candidates=sources,source_assembly_features=source_features,upstream=audit,candidates=results,
                unchanged_global_transform=M.tolist(),new_alignment_fit=False)
