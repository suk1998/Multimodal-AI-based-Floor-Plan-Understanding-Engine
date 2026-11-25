"""Compare independent objects within the registration support of Stage 9."""
from collections import Counter, defaultdict
import math

import cv2
import numpy as np

from backend.comparison_objects import catalog
from backend.spatial_alignment import verify
from backend.structural_alignment import fingerprint, polygon_mask, transform
from backend.marker_locus import measure_marker_locus,VERSION as MARKER_LOCUS_VERSION
from backend.observation_trace import TraceJournal
from backend.comparison_constraints import SearchAudit,unsupported_source_reasons


class ComparisonScope:
    def __init__(self, alignment):
        self.roi = np.array(alignment['target_roi']['vertices'],np.float32)
        self.matrix = self.inverse = None
        image = alignment['target_roi']['image']
        self.size = (image['height'],image['width'])
        self.roi_mask = polygon_mask(self.roi,self.size)
        if alignment['status'] not in ('VERIFIED_ROI','PARTIAL') or not alignment['mapping']:
            return
        self.matrix = np.array(alignment['matrix'])
        self.inverse = np.array(alignment['inverse_matrix'])
        valid = np.zeros(self.size,np.uint8)
        for index in alignment['mapping']['supported_tile_indices']:
            tile = alignment['coverage']['tiles'][index]
            if tile['status']=='SUPPORTED':
                x,y,u,v = tile['target_bbox_native']
                valid[y:v,x:u]=1
        valid &= (self.roi_mask!=0).astype(np.uint8)
        # Guard fractional boundaries and avoid testing just the centroid/corners.
        self.invalid = cv2.integral(1-cv2.erode(valid,np.ones((3,3),np.uint8),borderType=cv2.BORDER_CONSTANT,borderValue=0))

    def contains(self, ref, target):
        if self.inverse is None:
            return False
        points=[transform(np.concatenate(o['paths']), self.inverse) for o in ref]+[np.concatenate(o['paths']) for o in target]
        p=np.concatenate(points)
        x,y=np.floor(p.min(axis=0)).astype(int)
        u,v=np.ceil(p.max(axis=0)).astype(int)+1
        if x<0 or y<0 or u>self.size[1] or v>self.size[0]:
            return False
        m=self.invalid
        return int(m[v,u])-int(m[y,u])-int(m[v,x])+int(m[y,x])==0

    def intersects_target(self,obj):
        x,y,u,v=obj['bbox']
        return bool(np.any(self.roi_mask[max(0,math.floor(y)):math.ceil(v)+1,max(0,math.floor(x)):math.ceil(u)+1]))


def center(o):
    return np.asarray(o['bbox']).reshape(2,2).mean(axis=0)


def line_metrics(ref,target,matrix):
    a,b=np.array(ref['paths'][0],float)
    c,d=transform(target['paths'][0],matrix)
    length=np.linalg.norm(b-a)
    other=np.linalg.norm(d-c)
    if min(length,other)<1e-6:
        return None
    unit=(b-a)/length
    normal=np.array([-unit[1],unit[0]])
    if (d-c)@unit<0:
        c,d=d,c
    lo,hi=sorted([float((p-a)@unit) for p in (c,d)])
    overlap=max(0.,min(length,hi)-max(0.,lo))
    return dict(normal_max_ref_px=float(max(abs((c-a)@normal),abs((d-a)@normal))),
                angle_deg=math.degrees(math.acos(np.clip((d-c)@unit/other,-1,1))),
                observed_span_delta_ref_px=float(other-length),
                overlap_shorter=float(overlap/min(length,other)),
                overlap_longer=float(overlap/max(length,other)),
                projected_interval=[lo,hi],ref_span=float(length),
                endpoint_displacement_ref_px=[float(np.linalg.norm(c-a)),float(np.linalg.norm(d-b))]
                    if ref['physical_ends'] and target['physical_ends'] else None,
                thickness_delta_ref_px=float(target['width']*np.linalg.norm(matrix[:2,0])-ref['width'])
                    if 'width' in ref and 'width' in target else None)


def region_metrics(ref,target,matrix,*,recenter=False):
    # Rasterize only the union bbox at bounded resolution, retaining holes.
    rings=[[np.asarray(p,float) for p in ref['paths']], [transform(p,matrix) for p in target['paths']]]
    if recenter:
        displacement=center(ref)-transform([center(target)],matrix)[0]
        rings[1]=[p+displacement for p in rings[1]]
    pts=np.concatenate(rings[0]+rings[1])
    origin=pts.min(axis=0)
    factor=min(1.,510/max(1.,np.ptp(pts,axis=0).max()))
    w,h=np.ceil((pts.max(axis=0)-origin)*factor).astype(int)+3
    masks=[]
    for polys in rings:
        mask=np.zeros((h,w),np.uint8)
        cv2.fillPoly(mask,[np.rint((p-origin)*factor*16).astype(np.int32) for p in polys],1,shift=4)
        masks.append(mask)
    union=np.count_nonzero(masks[0]|masks[1])
    return float(np.count_nonzero(masks[0]&masks[1])/union) if union else 0.


def partition_witness(one,children,walls,child_to_single,domain,tolerance):
    """Check a newly observed common divider against independently observed free area."""
    rects=np.asarray(domain.get('layers',{}).get('F',{}).get('rects_native_xyxy',[]))
    if len(children)!=2 or not len(rects):
        return None
    ids={c['id'] for c in children}
    rings=[np.asarray(p,np.float32) for p in one['paths']]
    for w in walls:
        if w['kind']!='Wall' or not ids.issubset(w['spaces']):
            continue
        ends=transform(w['paths'][0],child_to_single)
        if np.linalg.norm(ends[1]-ends[0])<6*tolerance:
            continue
        if any(min(abs(cv2.pointPolygonTest(ring,tuple(pt),True)) for ring in rings)>3*tolerance for pt in ends):
            continue
        samples=ends[0]+np.linspace(.1,.9,17)[:,None]*(ends[1]-ends[0])
        if not all(sum(cv2.pointPolygonTest(ring,tuple(pt),False)>=0 for ring in rings)%2 for pt in samples):
            continue
        if all(np.any((rects[:,0]<=x)&(x<rects[:,2])&(rects[:,1]<=y)&(y<rects[:,3])) for x,y in samples):
            return w['id']
    return None


def paired_observations(a,b):
    """Intrinsic drawing observations, available even if coordinates cannot be mapped."""
    if a['kind']=='CodeMarker':
        readings=[o['name'] for o in (a,b)]
        complete=all(o['complete_text'] for o in (a,b))
        note=('번호를 포함한 표기를 양쪽에서 읽었습니다.' if complete else
              '한쪽 이상에서 번호가 빠졌습니다. 표기 차이와 OCR 누락을 구분할 수 없습니다.')
        return {'readings':readings,'complete':complete,'description':note}
    if a['kind']=='Space':
        if not (a['closed'] and b['closed']):
            note = '한쪽 이상에서 공간 경계가 닫히지 않아 면적과 방 분할 비교에 한계가 있습니다.'
        elif not (a['supported'] and b['supported']):
            note = '윤곽이 닫혀 있으나 일부 구간은 추론으로 연결되어 물리적 경계를 확인해야 합니다.'
        else:
            note = '양쪽 공간 윤곽 후보가 닫혀 있습니다.'
        return {'labels':[a['name'], b['name']], 'closed':[a['closed'], b['closed']],
                'inferred_run_counts':[a['inferred_run_count'], b['inferred_run_count']], 'description':note}
    return {'observed_spans_native_px':[float(np.linalg.norm(np.diff(o['paths'][0],axis=0))) for o in (a,b)],
            'host_counts':[len(a['hosts']),len(b['hosts'])],
            'description':'각 원본에서 측정한 구간 길이입니다. 끝점의 물리적 의미와 도면 배율은 별도로 확인해야 합니다.'}


def native_catalog(objects):
    result=[]
    for o in objects:
        obj={k:o[k] for k in ('kind','id','bbox','refs','context_refs','hosts','spaces','parent','name','supported')}
        obj['native_observations']={k:o[k] for k in ('width','physical_ends','closed','boundary_count','inferred_run_count','bracketed','complete_text') if k in o}
        if o['kind']=='CodeMarker':
            obj['native_observations'].update(o.get('native_observations',{}))
            obj['canonical_code']=o.get('canonical_code')
            obj['ontology_type_code']=o.get('ontology_type_code')
        if len(o['paths'][0])==2:
            obj['native_observations']['interval_native']=o['paths'][0]
            obj['native_observations']['observed_span_px']=float(np.linalg.norm(np.diff(o['paths'][0],axis=0)))
        result.append(obj)
    return result


def search_map(ref,target,matrix,ratio):
    if matrix is not None:
        return matrix
    # A label-based guess ranks candidates only. It cannot measure a change.
    def anchors(items):
        result=defaultdict(list)
        for o in items:
            if o['kind']=='Space':
                result[o['name']].append(o['seed'])
        return result
    a,b=anchors(ref),anchors(target)
    shifts=[np.array(a[n][0])-ratio*np.array(b[n][0]) for n in a.keys()&b.keys() if len(a[n])==len(b[n])==1]
    if len(shifts)<2:
        return None
    guess=np.diag([ratio,ratio,1.])
    guess[:2,2]=np.median(shifts,axis=0)
    return guess


def match_edges(aa,bb,guide,scope,zoom,tol,kind,linked,*,audit=None):
    edges=[]
    by_r,by_t=defaultdict(list),defaultdict(list)
    for a in aa:
        for b in bb:
            if guide is None:
                if audit:audit.attempt(a,b,['NO_SEARCH_GUIDE'])
                continue
            d=float(np.linalg.norm(center(a)-transform([center(b)],guide)[0]))
            host=linked(a,b,'hosts')
            space=linked(a,b,'spaces')
            context=len(set(a['neighbors'])&set(b['neighbors']))>=2
            marker_locus=(measure_marker_locus(a['bbox'],b['bbox'],scope,zoom,tol)
                          if kind=='CodeMarker' and a['closed'] and b['closed'] and a['supported'] and b['supported'] else None)
            direct_marker=bool(marker_locus and marker_locus['supported'])
            partition_candidate=kind=='Space' and scope.matrix is not None and a['closed'] and b['closed']
            reasons=[]
            if d>max(100*zoom,.65*np.linalg.norm(np.ptp(a['paths'][0],axis=0))):reasons.append('OUTSIDE_CANDIDATE_SEARCH_RADIUS')
            if not (context or space or host or partition_candidate or direct_marker):reasons.append('NO_HOST_SPACE_OR_LOCAL_CONTEXT_SEARCH_CUE')
            if reasons:
                if audit:audit.attempt(a,b,reasons)
                continue
            line=None; iou=None
            if kind in ('Space','CodeMarker'):
                if a['closed'] and b['closed']:
                    iou=region_metrics(a,b,guide)
                    if iou<.12 and not (host or space):
                        if audit:audit.attempt(a,b,['CLOSED_OUTLINE_SEARCH_OVERLAP_INSUFFICIENT'])
                        continue
                elif a['name']!=b['name'] or d>20*zoom or min(a.get('boundary_count',0),b.get('boundary_count',0))==0:
                    if audit:audit.attempt(a,b,['PARTIAL_BOUNDARY_LABEL_OR_SEARCH_GEOMETRY_INSUFFICIENT'])
                    continue
                quality=iou or 0.
            else:
                line=line_metrics(a,b,guide)
                if not line or line['angle_deg']>15 or (line['overlap_shorter']<.30 and not (space or host)):
                    if audit:audit.attempt(a,b,['LINE_DIRECTION_OR_SEARCH_OVERLAP_INSUFFICIENT'])
                    continue
                if line['normal_max_ref_px']>max(12*zoom,2*tol) and not (space or host):
                    if audit:audit.attempt(a,b,['LINE_SEARCH_NORMAL_DISTANCE_EXCEEDED'])
                    continue
                quality=line['overlap_shorter']-.5*min(1,line['normal_max_ref_px']/(12*zoom))
            score=quality+.30*host+.25*space+.10*context-.25*min(1,d/(30*zoom))
            edge=dict(a=a,b=b,score=score,line=line,iou=iou,distance=d,host=host,space=space,context=context,
                      direct_marker_locus=direct_marker,marker_locus_evidence=marker_locus)
            edges.append(edge);by_r[a['id']].append(edge);by_t[b['id']].append(edge)
            if audit:audit.attempt(a,b,[])
    for options in (*by_r.values(),*by_t.values()):
        options.sort(key=lambda e:-e['score'])
    return edges,by_r,by_t


def space_groups(scope,guide,ref,target,geometries,tol,by_r,by_t,used_r,used_t,record):
    for rev,groups,other_groups in [(False,by_r,by_t),(True,by_t,by_r)]:
        for options in groups.values():
            if not 2<=len(options)<=6:
                continue
            one=options[0]['b' if rev else 'a']
            many=[e['a' if rev else 'b'] for e in options]
            if not one['closed'] or any(not o['closed'] or len(other_groups[o['id']])!=1 for o in many):
                continue
            if any(region_metrics(a,b,np.eye(3))>.03 for i,a in enumerate(many) for b in many[:i]):
                continue
            combined={'paths':[p for o in many for p in o['paths']]}
            matrix=np.linalg.inv(guide) if rev else guide
            iou=region_metrics(one,combined,matrix)
            if iou<.88:
                continue
            rs,ts=(many,[one]) if rev else ([one],many)
            used_r.update(o['id'] for o in rs);used_t.update(o['id'] for o in ts)
            valid=scope.contains(rs,ts)
            domain=geometries[1].get('target_domains',{}) if rev else geometries[0].get('reference_domains',{})
            grounded = valid and all(o['supported'] for o in [one, *many])
            witness=partition_witness(one,many,ref if rev else target,matrix,domain,tol) if grounded else None
            record(rs,ts,'GROUP_HYPOTHESIS',
                   '공통 구획 벽과 상대 도면의 자유 영역이 관찰됐습니다.' if witness else '합친 윤곽은 대응하나 물리적 구획과 추출 분할을 구분할 근거가 부족합니다.',
                   support={'common_divider':witness},judgment='CHANGED' if witness else 'UNDETERMINED',
                   events=['MODIFIED'] if witness else [],facets=['PHYSICAL_PARTITION'] if witness else [],
                   measurements={'union_iou':iou,'partition':'MERGE' if rev else 'SPLIT'} if valid else None,
                   alternatives=[] if witness else ['PHYSICAL_PARTITION','EXTRACTION_SEGMENTATION'])


def wall_groups(scope,guide,ref,target,tol,by_r,by_t,used_r,used_t,record):
    for rev,groups,other_groups in [(False,by_r,by_t),(True,by_t,by_r)]:
        for group in groups.values():
            if not 2<=len(group)<=12:
                continue
            one=group[0]['b' if rev else 'a']
            many=[e['a' if rev else 'b'] for e in group]
            if any(len(other_groups[o['id']])!=1 for o in many):
                continue
            parts=[line_metrics(one,o,np.linalg.inv(guide)) for o in many] if rev else [e['line'] for e in group]
            if any(m is None or m['normal_max_ref_px']>tol/(np.linalg.norm(guide[:2,0]) if rev else 1)
                   or m['angle_deg']>2 or m['overlap_shorter']<.85 for m in parts):
                continue
            spans=sorted(m['projected_interval'] for m in parts)
            gaps=[s[0]-p[1] for p,s in zip(spans,spans[1:])]
            local_tol=tol/(np.linalg.norm(guide[:2,0]) if rev else 1)
            if min(gaps)<-local_tol or abs(spans[0][0])>2*local_tol or abs(spans[-1][1]-parts[0]['ref_span'])>2*local_tol:
                continue
            rs,ts=(many,[one]) if rev else ([one],many)
            used_r.update(o['id'] for o in rs);used_t.update(o['id'] for o in ts)
            same_width=all(abs(m['thickness_delta_ref_px'])<=local_tol for m in parts)
            contiguous=max(gaps)<=local_tol and same_width and len({o['parent'] for o in many})==1
            valid=scope.contains(rs,ts)
            # A through-opening is positive split evidence only if its own
            # admissible brackets span the measured gap on this very parent.
            split_evidence=None
            if valid and not contiguous and len(many)==2:
                side=ref if rev else target
                m=np.linalg.inv(guide) if rev else guide
                for opening in side:
                    if opening['kind']!='Opening' or not opening['supported'] or not set(opening['hosts'])&{o['parent'] for o in many}:
                        continue
                    geom=line_metrics(one,opening,m)
                    if (geom and geom['angle_deg']<2 and geom['normal_max_ref_px']<=local_tol
                        and np.allclose(geom['projected_interval'],[spans[0][1],spans[1][0]],atol=local_tol,rtol=0)):
                        split_evidence=opening['id']
                        break
            split=split_evidence is not None
            record(rs,ts,'GROUP_HYPOTHESIS','분절 구간의 합과 공통 벽선을 비교했습니다.',
                   support={'opposing_gap_opening':split_evidence,'same_fragment_parent':len({o['parent'] for o in many})==1},
                   judgment='UNCHANGED_WITHIN_SCOPE' if valid and contiguous else 'CHANGED' if split else 'UNDETERMINED',
                   events=['MODIFIED'] if split else [],facets=['OBSERVED_BOUNDARY_CONTINUITY'] if valid else [],
                   measurements={'interpretation':'OPENING_INTERRUPTS_WALL' if split else 'EXTRACTION_SEGMENTATION' if contiguous and valid else 'SPLIT_OR_MERGE_UNRESOLVED',
                                 'gap_lengths_native':gaps,'measurement_frame':('Target' if rev else 'Ref')} if valid else None,
                   alternatives=[] if valid and (split or contiguous) else ['PHYSICAL_PARTITION','EXTRACTION_SEGMENTATION'])


def compare_registered(geometries, alignment):
    """Internal entry, called only with an alignment produced on these server-owned revisions."""
    for g,s in zip(geometries,alignment['sources']):
        if g['run_id']!=s['geometry_run_id'] or g['frame_id']!=s['frame_id']:
            raise ValueError('기하 실행과 정렬 근거의 Frame이 다릅니다.')
    catalog_journals=[TraceJournal({**g['source_binding'],'source_role':g['source_role'],'frame_id':g['frame_id']},
                       alignment['input_fingerprint'],'CATALOG_AND_COMPARISON') for g in geometries]
    catalogs=[catalog(g,audit_sink=lambda event,j=j:j.record(**event)) for g,j in zip(geometries,catalog_journals)]
    (ref,skip_ref),(target,skip_target)=catalogs
    scope=ComparisonScope(alignment)
    zoom=geometries[0]['source_binding']['extraction_zoom']
    ratio=zoom/geometries[1]['source_binding']['extraction_zoom']
    guide=search_map(ref,target,scope.matrix,ratio)
    tol=float(alignment['settings']['normal_tolerance_ref_px'])
    fit_parents={side:{p[side+'_parent'] for p in alignment.get('pairs',[]) if p['usage']=='FIT'} for side in ('ref','target')}
    entries=[]
    search_audit=SearchAudit(catalog_journals)
    # Only identities supported in the comparison scope can ground another object's relation.
    links=defaultdict(set)
    back=defaultdict(set)
    def linked(a,b,field):
        return any(links[t]=={r} and back[r]=={t} for r in a[field] for t in b[field])

    def record(rs,ts,identity,reason,*,support=None,measurements=None,judgment='UNDETERMINED',events=(),facets=(),alternatives=(),domain=None,constraints=()):
        constraints=list(constraints)
        if ts and not any(scope.intersects_target(t) for t in ts):
            for journal,objects in zip(catalog_journals,[rs,ts]):
                for o in objects:journal.record(o['id'],o['kind'],'EXCLUDED_FROM_COMPARISON','TARGET_PAIR_OUTSIDE_USER_ROI')
            return
        kind=(ts or rs)[0]['kind']
        in_fit=any(o['parent'] in fit_parents[side] for side,objs in [('ref',rs),('target',ts)] for o in objs)
        if in_fit and judgment!='UNDETERMINED':
            constraints.append('COMPARISON_PARENT_USED_FOR_ALIGNMENT_FIT')
            judgment,events='UNDETERMINED',[]
            reason='같은 부모 벽이 정렬 적합에 쓰였습니다. 독립적인 객체 변화 측정이 필요합니다.'
        target_box = None
        if judgment == 'CHANGED' and scope.inverse is not None:
            points = (np.concatenate([np.concatenate(o['paths']) for o in ts]) if ts else
                      transform(np.concatenate([np.concatenate(o['paths']) for o in rs]), scope.inverse))
            lower = np.floor(points.min(axis=0)).astype(int)
            upper = np.maximum(np.ceil(points.max(axis=0)).astype(int), lower + 1)
            x, y = lower.tolist()
            u, v = upper.tolist()
            box = {'paths': [[[x, y], [u, v]]]}
            if scope.contains([], [box]):
                target_box = [x, y, u, v]
        entries.append(dict(correspondence_id=f'C{len(entries)+1:05}',entity_type=kind,
                    ref_ids=[o['id'] for o in rs],target_ids=[o['id'] for o in ts],
                    cardinality=f'{len(rs)}:{len(ts)}',identity_status=identity,judgment=judgment,
                    scope_status='TARGET_BBOX_INTERSECTS_ROI' if ts else 'REF_ONLY_LOCATION_UNCONFIRMED',
                    verified_target_bbox_native=target_box, alignment_fit_parent=in_fit, domain=domain or ('ANNOTATION' if kind=='CodeMarker' else 'STRUCTURAL'),
                    coordinate_support={'status':'SUPPORTED_COMPARISON_SCOPE' if scope.contains(rs,ts) else 'UNCONFIRMED',
                        'scope':'EXISTING_STAGE9_COMPARISON_COVERAGE','alignment_fit_parent':in_fit},
                    state='REVIEW',change_types=list(events),decision_reason=reason, measured_facets=list(facets),
                    identity_evidence=support,source_observations=paired_observations(rs[0],ts[0]) if len(rs)==len(ts)==1 else None,
                    measurements=measurements,alternatives=list(alternatives),
                    decision_constraints=constraints,
                    search_diagnostics=[search_audit.snapshot(role,o) for role,objs in [('Ref',rs),('Target',ts)] for o in objs],
                    evidence_refs=list(dict.fromkeys(v for o in rs+ts for v in [*o['refs'],*o['context_refs']]))))
        for journal,objects in zip(catalog_journals,[rs,ts]):
            for o in objects:journal.record(o['id'],o['kind'],'COMPARISON_RECORDED',judgment,
                output_ids=['comparison:'+alignment['input_fingerprint']+':'+entries[-1]['correspondence_id']],
                correspondence_id=entries[-1]['correspondence_id'],identity_status=identity,decision_reason=reason)

    for kind in ('Space','Wall','Opening','Door','Window','SlidingDoor','WindowOrSlidingDoor','CodeMarker'):
        aa=[o for o in ref if o['kind']==kind]
        bb=[o for o in target if o['kind']==kind]
        if len(aa)*len(bb)>50000:
            for o in bb:
                record([],[o],'UNASSESSED','이 유형의 대응 조합이 탐색 상한을 초과했습니다.',constraints=['COMPARISON_SEARCH_BUDGET_EXCEEDED'])
            skip_ref['comparison_budget_'+kind]=len(aa)
            skip_target['comparison_budget_'+kind]=len(bb)
            continue
        edges,by_r,by_t=match_edges(aa,bb,guide,scope,zoom,tol,kind,linked,audit=search_audit)
        used_r,used_t=set(),set()
        # Disjoint collinear observations can cover one longer wall. Evaluate the
        # complete group before reciprocal 1:1 scoring can swallow its first piece.
        if kind=='Space' and guide is not None:
            space_groups(scope,guide,ref,target,geometries,tol,by_r,by_t,used_r,used_t,record)
        if kind=='Wall' and guide is not None:
            wall_groups(scope,guide,ref,target,tol,by_r,by_t,used_r,used_t,record)
        for e in sorted(edges,key=lambda e:-e['score']):
            a,b=e['a'],e['b']
            if a['id'] in used_r or b['id'] in used_t or not (e['context'] or e['host'] or e['space'] or e['direct_marker_locus']):
                continue
            def decisive(options):
                return options[0] is e and (len(options)==1 or options[0]['score']-options[1]['score']>=.15)
            if not decisive(by_r[a['id']]) or not decisive(by_t[b['id']]):
                continue
            used_r.add(a['id']);used_t.add(b['id'])
            support={'same_kind':kind,'common_room_context':sorted(set(a['neighbors'])&set(b['neighbors'])),
                     'corresponding_host':e['host'],'corresponding_boundary_space':e['space'],
                     'mutually_preferred':True,'ref_geometry_supported':a['supported'],'target_geometry_supported':b['supported'],
                     'direct_registered_marker_locus':e['direct_marker_locus'],
                     'marker_locus_evidence':e['marker_locus_evidence'],
                     'search_geometry':'CLOSED_OUTLINE' if e['iou'] is not None else 'PAIRED_LINE_INTERVAL' if e['line'] else 'PARTIAL_BOUNDARY_AND_LABEL'}
            if not scope.contains([a],[b]):
                record([a],[b],'PROPOSED','두 객체와 그 사이가 검증된 비교 범위 안에 들어오지 않습니다.',support=support,constraints=['LOCAL_COORDINATE_SCOPE_UNCONFIRMED'])
                continue
            if not (a['supported'] and b['supported']):
                record([a],[b],'PROPOSED','부분 경계 또는 설치·호스트 근거가 보류 상태입니다.',support=support,constraints=unsupported_source_reasons([a,b]))
                continue
            metrics,events,facets={},[],[]
            if kind in ('Space','CodeMarker'):
                metrics.update(shape_iou=e['iou'],center_distance_ref_px=e['distance'])
                facets.append('REGION_SHAPE' if kind=='Space' else 'MARKER_OUTLINE')
                if e['iou'] is None or e['iou']<.55:
                    record([a],[b],'PROPOSED','형상·위치가 함께 달라 동일 객체와 교체를 구분하기 어렵습니다.',measurements=metrics,alternatives=['MOVED','REPLACED'],constraints=['MOVED_OR_REPLACED_IDENTITY_UNRESOLVED'])
                    continue
                aligned_shape=region_metrics(a,b,guide,recenter=True)
                metrics['centered_shape_iou']=aligned_shape
                if e['distance']>tol and aligned_shape>.95:
                    # With no independent host/partition continuity a translation and
                    # replacement remain competing explanations, including same-name rooms.
                    record([a],[b],'PROPOSED','유사한 윤곽의 위치가 달라 이동과 교체를 구분하기 어렵습니다.',
                           support=support,measurements=metrics,alternatives=['MOVED','REMOVED_AND_ADDED'],constraints=['MOVED_OR_REPLACED_IDENTITY_UNRESOLVED'])
                    continue
                if e['iou']<.90 and aligned_shape<.95:
                    events.append('MODIFIED')
                if kind=='CodeMarker':
                    if not (a['complete_text'] and b['complete_text']):
                        record([a],[b],'PROPOSED','기호 번호 판독이 불완전합니다. 창호 사양은 미확인입니다.',measurements=metrics,constraints=['INCOMPLETE_TYPE_CODE_READING'])
                        continue
                    facets.append('MARKER_TEXT')
                    metrics['marker_readings']=[a['name'],b['name']]
                    if a['name']!=b['name'] and 'MODIFIED' not in events:
                        events.append('MODIFIED')
            else:
                m=e['line']
                metrics.update(m)
                facets.extend(['NORMAL_POSITION','DIRECTION'])
                bracketed=a.get('bracketed') and b.get('bracketed')
                if bracketed:
                    facets.append('OBSERVED_JAMB_INTERVAL')
                    metrics['jamb_interval_delta_ref_px']=m['observed_span_delta_ref_px']
                    if abs(m['observed_span_delta_ref_px'])>tol:
                        events.append('MODIFIED')
                parallel=m['angle_deg']<=alignment['settings']['angle_tolerance_degrees']
                moved=parallel and (m['normal_max_ref_px']>tol or ((bracketed or m['endpoint_displacement_ref_px'] is not None)
                        and abs(m['observed_span_delta_ref_px'])<=tol and e['distance']>tol))
                if moved and not (e['host'] or e['space']):
                    record([a],[b],'PROPOSED','위치 차이가 있으나 같은 호스트나 경계 공간의 대응이 부족합니다.',measurements=metrics,alternatives=['MOVED','REMOVED_AND_ADDED'],constraints=['PHYSICAL_IDENTITY_WITH_MOVEMENT_UNRESOLVED'])
                    continue
                if kind in ('Door','Opening','Window','SlidingDoor') and not e['host']:
                    record([a],[b],'PROPOSED','대응하는 부모 벽 또는 개구부를 확인할 수 없습니다.',measurements=metrics,constraints=['PHYSICAL_HOST_CORRESPONDENCE_UNCONFIRMED'])
                    continue
                if moved:
                    events.append('MOVED')
                if m['angle_deg']>alignment['settings']['angle_tolerance_degrees'] or (
                        m['thickness_delta_ref_px'] is not None and abs(m['thickness_delta_ref_px'])>tol):
                    if 'MODIFIED' not in events:
                        events.append('MODIFIED')
                if m['thickness_delta_ref_px'] is not None:
                    facets.append('OBSERVED_FACE_SEPARATION')
                if m['endpoint_displacement_ref_px'] is not None:
                    facets.append('SUPPORTED_ENDPOINTS_AND_LENGTH')
                    if abs(m['observed_span_delta_ref_px'])>tol and 'MODIFIED' not in events:
                        events.append('MODIFIED')
                elif (m['overlap_longer']<.85 or abs(m['observed_span_delta_ref_px'])>tol
                      or abs(m['projected_interval'][0])>tol or abs(m['projected_interval'][1]-m['ref_span'])>tol) and not events and not bracketed:
                    record([a],[b],'PROPOSED','공통 선은 겹치지만 물리 끝점과 길이 변화를 구분할 수 없습니다.',measurements=metrics,facets=facets,constraints=['PHYSICAL_ENDPOINT_SEMANTICS_UNVERIFIED'])
                    continue
                if a['spaces'] and b['spaces'] and kind=='Door':
                    known=all(len(links[x])==1 for x in b['spaces']) and all(len(back[x])==1 for x in a['spaces'])
                    if known:
                        facets.append('CONNECTED_SPACES')
                        metrics['connected_space_ids']=[a['spaces'],b['spaces']]
                        if {next(iter(links[x])) for x in b['spaces']}!=set(a['spaces']):
                            events.append('RELATION_CHANGED')
            # A parent used to align can still identify a host, but its own
            # post-fit residual is not independent evidence of unchanged geometry.
            links[b['id']].add(a['id']);back[a['id']].add(b['id'])
            if a['parent'] and b['parent']:
                links[b['parent']].add(a['parent']);back[a['parent']].add(b['parent'])
            metrics['identity_support']={'host':e['host'],'bounded_space':e['space'],'local_room_context':e['context']}
            record([a],[b],'SUPPORTED','검증된 기호 위치·외곽으로 표기를 대응했습니다. 공간 문맥과 실제 설치 요소의 동일성은 별도로 검토합니다.' if e['direct_marker_locus'] else '독립 기하와 주변 관계를 함께 비교했습니다.',
                   support=support,measurements=metrics,judgment='CHANGED' if events else 'UNCHANGED_WITHIN_SCOPE',events=events,facets=facets)
            if kind=='CodeMarker' and a['name']!=b['name']:
                record([a],[b],'UNASSESSED','기호 표기는 달라졌으나 설치 객체와 사양표를 연결하는 독립 근거가 없습니다.',
                       domain='SPECIFICATION',support={'marker_readings':[a['name'],b['name']]},constraints=['INSTALLATION_SPECIFICATION_UNGROUNDED'])
            if kind=='Space' and a['name']!=b['name']:
                record([a],[b],'SUPPORTED','대응 공간의 표기가 달라 원문 검토가 필요합니다.',judgment='CHANGED',
                       domain='ANNOTATION',events=['MODIFIED'],facets=['SPACE_LABEL'],measurements={'readings':[a['name'],b['name']]})
        for objects,used,options,side in [(bb,used_t,by_t,'Target'),(aa,used_r,by_r,'Ref')]:
            for o in objects:
                if o['id'] in used:
                    continue
                candidates=[e['a' if side=='Target' else 'b']['id'] for e in options[o['id']]]
                rs,ts=([o],[]) if side=='Ref' else ([],[o])
                if kind=='Opening' and o['supported'] and not candidates and scope.matrix is not None:
                    opposite=ref if side=='Target' else target
                    host_map=links if side=='Target' else back
                    reverse_map=back if side=='Target' else links
                    parents={p for h in o['hosts'] for p in host_map[h] if len(host_map[h])==1 and reverse_map[p]=={h}}
                    # Any opening hypothesis on the opposite host leaves relocation
                    # or missed detection unresolved, even if its upstream status was HOLD.
                    other_assessment=geometries[0 if side=='Target' else 1]['architectural_assessment']
                    competing=any(parents.intersection(v['host_parent_ids']) and v['decision']!='CONTRADICTED'
                                  for v in other_assessment.get('openings',[]))
                    wall=None
                    if parents and not competing:
                        direction=scope.matrix if side=='Target' else scope.inverse
                        local_tol=tol if side=='Target' else tol/np.linalg.norm(scope.matrix[:2,0])
                        for w in opposite:
                            if w['kind']!='Wall' or w['parent'] not in parents:
                                continue
                            m=line_metrics(w,o,direction)
                            if (m and m['angle_deg']<=2 and m['normal_max_ref_px']<=local_tol
                                and m['projected_interval'][0]>local_tol and m['projected_interval'][1]<m['ref_span']-local_tol
                                and scope.contains([w] if side=='Target' else rs,ts if side=='Target' else [w])):
                                wall=w
                                break
                    if wall:
                        record(rs,ts,'SUPPORTED_AT_LOCUS','대응 부모 벽의 연속 구간과 양쪽 잼이 관찰된 개구부가 같은 위치를 차지합니다.',
                               support={'opposite_continuous_wall':wall['id'],'wall_observations':wall['refs']},
                               judgment='CHANGED',events=['ADDED' if side=='Target' else 'REMOVED'],
                               facets=['OPENING_OCCUPANCY_AT_THIS_HOST_LOCATION'],alternatives=['INSTALLATION_IDENTITY_UNKNOWN'])
                        continue
                record(rs,ts,'AMBIGUOUS' if candidates else 'UNMATCHED',
                       '복수 대응 가능성이 남습니다.' if candidates else '상대 도면의 관찰 누락과 실제 신설·철거를 구분할 근거가 부족합니다.',
                       alternatives=candidates,constraints=['CORRESPONDENCE_COMPETITION_OR_PRIOR_ASSIGNMENT_UNRESOLVED'] if candidates else ['NO_ELIGIBLE_CATALOG_COUNTERPART'])
    return dict(schema_version='structural_comparison/1',input_fingerprint=alignment['input_fingerprint'],
                run_id=fingerprint(['structural_comparison/1',MARKER_LOCUS_VERSION,alignment['input_fingerprint']]),
                marker_locus_matching_version=MARKER_LOCUS_VERSION,
                _catalog_trace_streams=[j.finish() for j in catalog_journals],
                sources=alignment['sources'],target_roi=alignment['target_roi'],alignment_status=alignment['status'],
                coverage=alignment['coverage'],status='REVIEW_AVAILABLE' if any(e['judgment']!='UNDETERMINED' for e in entries) else 'UNDETERMINED',
                judgments=dict(Counter(e['judgment'] for e in entries)),correspondences=entries,
                source_objects={'Ref':native_catalog(ref),'Target':native_catalog(target)},
                excluded_upstream={'Ref':skip_ref,'Target':skip_target},confirmed_changes=None,
                comparison_search=search_audit.finish(),
                specification_status='NO_GROUNDED_SPECIFICATION_COMPARISON',independent_gt_metrics=None)


def compare(pages, geometries, roi, strokes=None, *, dimension_vectors=None, annotation_decisions=False, opening_geometry_decisions=False):
    """Keep legacy library callers structural-only; the web workflow explicitly
    enables scoped annotation decisions. This is independent of vector/OCR type.
    """
    alignment = verify(pages,geometries,roi,strokes)
    result = compare_registered(geometries,alignment)
    result['opening_geometry_decisions_enabled']=bool(opening_geometry_decisions)
    result['alignment_diagnostics'] = {
        key: alignment.get(key) for key in ('reasons','fit_model','distribution','matrix','wall_registration')
    }
    if not strokes or not all(strokes):
        return _finalize_changes(_with_openings(_with_dimensions(result,pages,geometries,alignment,dimension_vectors),pages,geometries,alignment,strokes,dimension_vectors),pages,geometries,alignment,annotation_decisions,strokes,dimension_vectors)

    from backend.wall_sections import adjoining_names, section_changes

    for candidate in section_changes(pages,strokes,alignment):
        ref, target = (candidate[k] for k in ('ref','target'))
        for change in candidate['caps']:
            end = change['end']
            a,b = ref.returns[end],target.returns[end]
            ref_names = adjoining_names(geometries[0],ref,a,pages[0].zoom)
            target_names = adjoining_names(geometries[1],target,b,pages[1].zoom)
            space_name = ' / '.join(sorted(target_names or ref_names))
            if ref_names and not target_names:
                space_name += ' (Ref 경계 인접)'
            elif target_names:
                space_name += ' (Target 경계 인접)'
            refs = [*(target_names or ref_names).values()]
            ids = []
            for role, section, cap in (('Ref',ref,a),('Target',target,b)):
                item_id = f'{role}:{section.id}:end-{end}'
                ids.append(item_id)
                evidence = [f'{role}:{v}' for v in section.refs]
                refs.extend(evidence)
                points = np.asarray(cap.points())
                bbox = [*points.min(axis=0).tolist(),*points.max(axis=0).tolist()]
                result['source_objects'][role].append(dict(id=item_id,kind='Wall',name=space_name or None,
                    bbox=bbox,refs=evidence,parent=section.id,supported=True,
                    native_observations={'wall_end_native':points.tolist(),'paired_face_refs':section.refs}))
            delta = change['delta_ref_px']
            description = f'벽 끝선 위치 {delta:+.2f}px (Ref 원본의 {"가로" if target.axis==0 else "세로"}축)'
            facets = ['WALL_END_POSITION']
            types = ['WALL_END_SHIFT']
            if change['span_delta_ref_px'] is not None:
                facets.append('CAPPED_WALL_SPAN')
                types.append('WALL_LENGTH')
                description += f' · 양 끝 사이 길이 {change["span_delta_ref_px"]:+.2f}px'
            result['correspondences'].append(dict(
                correspondence_id=f'W{len(result["correspondences"])+1:05}', entity_type='Wall',
                ref_ids=ids[:1],target_ids=ids[1:],cardinality='1:1',identity_status='SUPPORTED',
                judgment='CHANGED',state='CONFIRMED',domain='STRUCTURAL',change_types=types,
                automatic_confirmation='VECTOR_WALL_END_WITH_INDEPENDENT_RASTER_SUPPORT',
                scope_status='VERIFIED_LOCAL_WALL_END', verified_target_bbox_native=change['target_bbox_native'],
                alignment_fit_parent=False,space_name=space_name or None, change_description=description,
                decision_reason='벽의 양면과 직교 끝선을 대응했습니다. 양쪽 원본에서 각 끝선이 보이고 상대 위치에서 같은 끝선이 사라졌으며 인접한 두 영상 구간에서 좌표 대응을 확인했습니다.',
                measured_facets=facets,measurements=change,
                source_observations=None,evidence_refs=refs))
    result['judgments'] = dict(Counter(r['judgment'] for r in result['correspondences']))
    if result['judgments'].get('CHANGED'):
        result['status'] = 'REVIEW_AVAILABLE'
    result['run_id'] = fingerprint(['wall-end-comparison/1',result['input_fingerprint']])
    return _finalize_changes(_with_openings(_with_dimensions(result,pages,geometries,alignment,dimension_vectors),pages,geometries,alignment,strokes,dimension_vectors),pages,geometries,alignment,annotation_decisions,strokes,dimension_vectors)


def _with_openings(result,pages,geometries,alignment,strokes,vectors):
    if not result['opening_geometry_decisions_enabled']:return result
    from backend.opening_comparison import attach_opening_comparison
    return attach_opening_comparison(result,pages,geometries,alignment,strokes,vectors)


def _with_dimensions(result,pages,geometries,alignment,vectors):
    """Add locally verified printed-label changes without altering Understanding
    or registration. Existing callers without vector inputs retain their path.
    """
    if vectors is None:
        return result
    from backend.dimension_comparison import compare_dimensions,VERSION
    entries,objects,audit=compare_dimensions(pages,geometries,alignment,vectors)
    for role in ('Ref','Target'):
        result['source_objects'][role].extend(o for o in objects if o['source_role']==role)
    result['correspondences'].extend(entries)
    result['dimension_comparison']=audit
    result['judgments']=dict(Counter(r['judgment'] for r in result['correspondences']))
    if entries:result['status']='REVIEW_AVAILABLE'
    result['run_id']=fingerprint([VERSION,result['run_id'],[(r['correspondence_id'],r['before'],r['after']) for r in entries]])
    return result


def _finalize_changes(result,pages,geometries,alignment,annotation_decisions,strokes=None,vectors=None):
    from backend.element_grounding import attach_element_grounding, VERSION as grounding_version
    from backend.door_correspondence import attach_door_correspondence
    from backend.comparison_claims import attach_claim_assessment
    from backend.symbol_comparison import enrich_marker_changes
    from backend.change_candidate_audit import attach_candidate_audit,VERSION
    from backend.upstream_lineage import attach_upstream_lineage,VERSION as upstream_version
    from integrations.fpue_ontology.detection_contract import VERSION as policy_version
    if annotation_decisions:
        enrich_marker_changes(result,pages,geometries,alignment,ComparisonScope(alignment))
    result['annotation_decisions_enabled']=bool(annotation_decisions)
    attach_element_grounding(result,pages,geometries)
    attach_door_correspondence(result,pages,geometries,alignment,strokes,vectors,ComparisonScope(alignment))
    attach_claim_assessment(result,geometries)
    attach_candidate_audit(result)
    result['run_id']=fingerprint([VERSION,policy_version,MARKER_LOCUS_VERSION,upstream_version,grounding_version,result['run_id'],bool(annotation_decisions),result.get('opening_geometry_decisions_enabled',False)])
    attach_upstream_lineage(result,geometries)
    return result
