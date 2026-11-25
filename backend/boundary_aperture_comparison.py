"""Observed boundary aperture reconfiguration, not physical door identity.

Independently read closed PDF band contours BEFORE the bold-stroke filter. A
gap must have two positive flanks and be replaced by a positively observed
continuous contour in the other drawing. Reference space boundaries supply
comparison context only: no Target space, label or geometry is fabricated.
"""
from collections import defaultdict,Counter
from dataclasses import dataclass,asdict
import hashlib,math,re
import cv2,numpy as np
from backend.structural_alignment import fingerprint,transform
from backend.wall_sections import ink_support,Stroke,Section,adjoining_names
from backend.door_correspondence import position_evidence,bounds,polygon,roi_contains
from backend.boundary_contours import segmented_bands,corner_gaps,thinner_stroke_correction,VERSION as CONTOUR_VERSION

VERSION='boundary_aperture_configuration/1'
CONFIRMATION='PAIRED_BOUNDARY_APERTURE_CHANGES_WITH_INDEPENDENT_STRUCTURE'
CLAIM_SCOPE='OBSERVED_BOUNDARY_APERTURE_CONFIGURATION_NOT_DOOR_PRODUCT_IDENTITY'
CHANGE_TYPE='BOUNDARY_APERTURE_RECONFIGURATION'

@dataclass(frozen=True)
class Settings:
    minimum_stroke_zoom:float=.35
    minimum_depth_zoom:float=1.5
    maximum_depth_zoom:float=24.
    minimum_gap_zoom:float=6.
    maximum_gap_zoom:float=180.
    normal_tolerance_zoom:float=.6
    minimum_ink:float=.85
    maximum_gap_face_ink:float=.35

def segment(axis,normal,lo,hi):
    points=[[lo,normal],[hi,normal]]
    return points if axis==0 else [p[::-1] for p in points]

def band_box(b,start=None,end=None):
    return bounds([*segment(b['axis'],b['n0'],b['lo'] if start is None else start,b['hi'] if end is None else end),
                   *segment(b['axis'],b['n1'],b['lo'] if start is None else start,b['hi'] if end is None else end)])

def read_bands(vector,gray,zoom,role,cfg):
    groups=defaultdict(list);bands=[]
    for line in vector['lines']:groups[line['source_evidence'].get('path_id')].append(line)
    for path,items in groups.items():
        if not path or len(items)!=4 or any(l['source_evidence'].get('stroke_width_native',0)<cfg.minimum_stroke_zoom*zoom for l in items):continue
        layers={l['source_evidence'].get('layer','').split('|')[-1] for l in items}
        if any(layer and not re.fullmatch(r'A-WAL[0-9]*',layer,re.I) for layer in layers):continue
        if sum(l['axis']==0 for l in items)!=2:continue
        points=np.asarray([l['points_native'] for l in items]);box=bounds(points)
        # All four actual edges must close this rectangle; a bounding box of
        # unrelated line pieces is never a band contour.
        x,y,u,v=box;eps=.05*zoom;corners=np.asarray(polygon(box))
        if any(min(np.linalg.norm(p-c) for c in corners)>eps for p in points.reshape(-1,2)):continue
        if any(sum(np.linalg.norm(p-c)<=eps for p in points.reshape(-1,2))!=2 for c in corners):continue
        visibility=[ink_support(gray,l['points_native']) for l in items]
        if any(s is None or s<cfg.minimum_ink for s in visibility):continue
        for axis in [0,1]:
            lo,hi=(x,u) if axis==0 else (y,v);n0,n1=(y,v) if axis==0 else (x,u)
            if not cfg.minimum_depth_zoom*zoom<=n1-n0<=cfg.maximum_depth_zoom*zoom or hi-lo<.5*(n1-n0):continue
            bands.append(dict(id=role+':contour-band:'+fingerprint([path,axis])[:16],source_role=role,
                axis=axis,lo=lo,hi=hi,n0=n0,n1=n1,source_refs=[l['id'] for l in items],path_ids=[path],
                source_evidence=[l['source_evidence'] for l in items],contour_ink_support=visibility,
                claim_scope='OBSERVED_CLOSED_BAND_CONTOUR_NOT_VERIFIED_WALL'))
    return bands+segmented_bands(vector,gray,zoom,role,cfg)

def merge_bands(bands,zoom):
    result=[]
    for b in sorted(bands,key=lambda b:(b['axis'],b['n0'],b['n1'],b['lo'])):
        old=next((o for o in result if o['axis']==b['axis'] and max(abs(o['n0']-b['n0']),abs(o['n1']-b['n1']))<.05*zoom
            and b['lo']<=o['hi']+.2*zoom and b['hi']>=o['lo']-.2*zoom),None)
        if old:
            old['lo']=min(old['lo'],b['lo']);old['hi']=max(old['hi'],b['hi'])
            for k in ['source_refs','path_ids']:old[k]=list(dict.fromkeys(old[k]+b[k]))
            old['source_evidence']+=b['source_evidence'];old['contour_ink_support']+=b['contour_ink_support']
        else:result.append(dict(b))
    return result

def find_gaps(bands,zoom,cfg):
    out=[];eps=cfg.normal_tolerance_zoom*zoom
    for a in bands:
        options=[b for b in bands if a['axis']==b['axis'] and b['lo']>a['hi']+.2*zoom
            and max(abs(a['n0']-b['n0']),abs(a['n1']-b['n1']))<=eps]
        if not options:continue
        nearest=min(b['lo'] for b in options)
        for b in options:
            if b['lo']>nearest+.2*zoom:continue
            length=b['lo']-a['hi'];depth=min(a['n1']-a['n0'],b['n1']-b['n0'])
            if not cfg.minimum_gap_zoom*zoom<=length<=cfg.maximum_gap_zoom*zoom or length<1.5*depth:continue
            gap=dict(axis=a['axis'],lo=a['hi'],hi=b['lo'],n0=(a['n0']+b['n0'])/2,n1=(a['n1']+b['n1'])/2,
                flanks=[a,b],source_refs=list(dict.fromkeys(a['source_refs']+b['source_refs'])))
            gap['bbox_native']=band_box(gap);gap['id']=a['source_role']+':contour-gap:'+fingerprint([a['id'],b['id']])[:16]
            out.append(gap)
    return out+corner_gaps(bands,zoom,cfg,out)

def face_support(gray,box,axis,excluded,*,vector=None,expected_width=None):
    x,y,u,v=box;normals=[y,v] if axis==0 else [x,u];lo,hi=(x,u) if axis==0 else (y,v);results=[]
    for normal in normals:
        points=np.asarray(segment(axis,normal,lo,hi));samples=np.linspace(*points,41)[5:-5]
        visible=[];sampled=[];strips=[]
        for point in samples:
            if any(b[0]-1<=point[0]<=b[2]+1 and b[1]-1<=point[1]<=b[3]+1 for b in excluded):continue
            n=np.array([0,1]) if axis==0 else np.array([1,0]);p=np.rint(point[None,:]+np.arange(-1,2)[:,None]*n).astype(int)
            if np.any(p<0) or np.any(p[:,0]>=gray.shape[1]) or np.any(p[:,1]>=gray.shape[0]):continue
            visible.append(bool(gray[p[:,1],p[:,0]].min()<160))
            sampled.append(point);strips.append(p)
        result=dict(unoccluded_samples=len(visible),total_samples=len(samples),
            ink_fraction=float(np.mean(visible)) if visible else None)
        if sampled and vector is not None and result['ink_fraction']>.35:
            q=np.float32(sampled)
            values=cv2.remap(gray,q[:,0].reshape(1,-1),q[:,1].reshape(1,-1),cv2.INTER_LINEAR).ravel()
            central=float(np.mean(values<160))
            correction=thinner_stroke_correction(gray,sampled,strips,visible,central,axis,normal,vector,expected_width)
            if correction:
                result.update(raw_strip_ink_fraction=result['ink_fraction'],ink_fraction=central,
                    thinner_stroke_correction=correction)
        results.append(result)
    return results

def source_exclusions(g):
    return [bounds(o['geometry_native']['points_native']) for o in g.get('observations',[])
        if o.get('observation_type') in ['TextRegion','TextDetection'] and o.get('geometry_native',{}).get('points_native')]

def compare_sites(pages,geometries,vectors,strokes,alignment,roi_mask,cfg=Settings()):
    out=dict(schema_version=VERSION,contour_extraction_version=CONTOUR_VERSION,settings=asdict(cfg),sites=[],groups=[],source_bands={},source_gaps={},
        physical_identity_inferred=False,target_understanding_uses_reference=False)
    if vectors is None or strokes is None or len(vectors)!=2 or len(strokes)!=2 or any(v is None for v in vectors):
        return dict(out,status='UNAVAILABLE',reason='SOURCE_VECTOR_OR_STRUCTURAL_SUPPORT_MISSING')
    if alignment.get('status') not in ['PARTIAL','VERIFIED_ROI'] or alignment.get('matrix') is None:
        return dict(out,status='DEFERRED',reason='NO_SUPPORTED_FIXED_TRANSFORM')
    M=np.asarray(alignment['matrix'],float)
    if M.shape!=(3,3) or not np.isfinite(M).all() or not np.allclose(M[2],[0,0,1]) or max(abs(M[0,1]),abs(M[1,0]),abs(M[0,0]-M[1,1]))>1e-6 or M[0,0]<=0:
        return dict(out,status='DEFERRED',reason='NONAXIAL_OR_INVALID_TRANSFORM')
    for role,page,g,v,source in zip(['Ref','Target'],pages,geometries,vectors,alignment['sources']):
        binding=g['source_binding']
        if (v.get('source_sha256')!=page.source_sha256 or v.get('page')!=page.page_number or v.get('extraction_zoom')!=page.zoom
            or v.get('image_size')!=[page.width,page.height] or g['source_role']!=role
            or source['frame_id']!=g['frame_id'] or g['frame_id']!=f'{role}:{page.source_sha256}:{page.page_number}:{page.zoom:g}:{page.width}x{page.height}'
            or binding.get('source_sha256')!=page.source_sha256 or binding.get('page')!=page.page_number
            or binding.get('extraction_zoom')!=page.zoom or binding.get('width')!=page.width or binding.get('height')!=page.height
            or binding.get('image_sha256')!=hashlib.sha256(page.png).hexdigest()):
            raise ValueError('Boundary aperture source/frame mismatch')
    grays=[cv2.imdecode(np.frombuffer(p.png,np.uint8),0) for p in pages]
    out['source_bindings']={role:{k:g['source_binding'][k] for k in ['source_role','frame_id','source_sha256','image_sha256','page','extraction_zoom','width','height']}
        for role,g in zip(['Ref','Target'],geometries)}
    stocks=[merge_bands(read_bands(v,im,p.zoom,role,cfg),p.zoom) for role,v,im,p in zip(['Ref','Target'],vectors,grays,pages)]
    gaps=[find_gaps(items,p.zoom,cfg) for items,p in zip(stocks,pages)]
    exclusions=[source_exclusions(g) for g in geometries]
    for role,s,g in zip(['Ref','Target'],stocks,gaps):out['source_bands'][role]=s;out['source_gaps'][role]=g
    for side in [0,1]:
        to_other=np.linalg.inv(M) if side==0 else M
        for gap in gaps[side]:
            mapped=bounds(transform(polygon(gap['bbox_native']),to_other));axis=gap['axis']
            normal=[mapped[1-axis],mapped[3-axis]];lo,hi=mapped[axis],mapped[axis+2]
            pool=[b for b in stocks[1-side] if b['axis']==axis and
                max(abs(b['n0']-normal[0]),abs(b['n1']-normal[1]))<=cfg.normal_tolerance_zoom*pages[1-side].zoom
                and b['lo']<=lo-.25*(b['n1']-b['n0']) and b['hi']>=hi+.25*(b['n1']-b['n0'])]
            if not pool:continue
            boxes=[gap['bbox_native'],mapped] if side==0 else [mapped,gap['bbox_native']]
            item=dict(id='BA'+fingerprint([VERSION,gap['id'],sorted(b['id'] for b in pool)])[:16],
                source_bindings=out['source_bindings'],
                gap_source_role=['Ref','Target'][side],axis=axis,Ref_bbox_native=boxes[0],Target_bbox_native=boxes[1],
                gap=gap,continuous_candidates=pool,status='PROPOSED',reason_codes=[],registration={},
                change='DRAWN_GAP_CLOSED' if side==0 else 'DRAWN_GAP_CREATED')
            if len(pool)!=1:item['reason_codes'].append('COMPETING_CONTINUOUS_BAND_CONTOURS')
            if not roi_contains({'bbox_native':boxes[0]},{'bbox_native':boxes[1]},M,roi_mask):item['reason_codes'].append('APERTURE_SITE_OUTSIDE_USER_ROI')
            widths=[e['stroke_width_native'] for b in gap['flanks'] for e in b['source_evidence']]
            visibility=[face_support(im,box,axis,excluded,vector=vectors[i] if i==side else None,
                expected_width=min(widths)) for i,(im,box,excluded) in enumerate(zip(grays,boxes,exclusions))]
            item['face_visibility']={r:rows for r,rows in zip(['Ref','Target'],visibility)}
            if any(v['unoccluded_samples']<18 for rows in visibility for v in rows):item['reason_codes'].append('INSUFFICIENT_UNOCCLUDED_SOURCE_SAMPLES')
            elif any(v['ink_fraction']>cfg.maximum_gap_face_ink for v in visibility[side]):item['reason_codes'].append('NO_VISIBLE_GAP_ON_BOTH_SOURCE_FACES')
            elif any(v['ink_fraction']<cfg.minimum_ink for v in visibility[1-side]):item['reason_codes'].append('NO_POSITIVE_OPPOSITE_CONTINUOUS_FACES')
            if not item['reason_codes']:
                registration=position_evidence({'bbox_native':boxes[0]},{'bbox_native':boxes[1]},pages,strokes,grays,alignment)
                item['registration']=registration
                if registration['status']!='SUPPORTED_LOCAL_COORDINATE_CORRESPONDENCE':item['reason_codes'].append(registration.get('reason','LOCAL_COORDINATE_SUPPORT_MISSING'))
            if not item['reason_codes']:item['status']='SUPPORTED_DRAWING_APERTURE_CHANGE'
            out['sites'].append(item)
    out['status']='EVALUATED'
    return out

def boundary_side(region,site,zoom):
    rings=(region.get('polygon_native') or {}).get('outer_rings',[])
    if len(rings)!=1:return None
    p=np.asarray(rings[0],float)
    if p.ndim!=2 or p.shape[1]!=2 or len(p)<4 or not np.isfinite(p).all():return None
    if np.linalg.norm(p[0]-p[-1])<.1:p=p[:-1]
    edges=list(zip(p,np.roll(p,-1,axis=0)));eps=.1
    if len(p)<4 or any(np.linalg.norm(b-a)<=eps for a,b in edges):return None
    # Self-intersection or touching non-neighboring edges is not a usable
    # enclosure. Concavity is allowed; a bbox is never substituted for a ring.
    for i,(a,b) in enumerate(edges):
        for j,(c,d) in enumerate(edges):
            if j<=i or j==i+1 or (i==0 and j==len(edges)-1):continue
            if np.any(np.maximum(np.minimum(a,b),np.minimum(c,d))>np.minimum(np.maximum(a,b),np.maximum(c,d))+1e-6):continue
            def orient(u,v,w):
                q=v-u;r=w-u
                return q[0]*r[1]-q[1]*r[0]
            if orient(a,b,c)*orient(a,b,d)<=1e-6 and orient(c,d,a)*orient(c,d,b)<=1e-6:return None
    signed_area=sum(a[0]*b[1]-b[0]*a[1] for a,b in edges)/2
    if abs(signed_area)<=eps:return None
    x,y,u,v=site['Ref_bbox_native'];axis=site['axis'];lo,hi=(x,u) if axis==0 else (y,v)
    normal=(y+v)/2 if axis==0 else (x+u)/2
    tolerance=max(3*zoom,1.5*((v-y) if axis==0 else (u-x)))
    matches=[]
    for a,b in edges:
        if abs(b[1-axis]-a[1-axis])>eps:continue
        along=sorted([a[axis],b[axis]])
        if min(hi,along[1])-max(lo,along[0])<.8*(hi-lo) or abs(normal-a[1-axis])>tolerance:continue
        delta=b-a;outward=np.array([delta[1],-delta[0]])*np.sign(signed_area)
        side=('SOUTH' if outward[1]>0 else 'NORTH') if axis==0 else ('EAST' if outward[0]>0 else 'WEST')
        matches.append(side)
    return matches[0] if len(matches)==1 else None

def valid_gap_face_measurement(row):
    correction=row.get('thinner_stroke_correction')
    if correction is None:return 'raw_strip_ink_fraction' not in row
    raw=row.get('raw_strip_ink_fraction');value=row.get('ink_fraction');width=correction.get('expected_face_stroke_native')
    if (correction.get('method')!='OBSERVED_ADJACENT_THINNER_STROKE_AND_SUBPIXEL_CENTER' or
        correction.get('all_dark_strip_pixels_explained') is not True or not correction.get('source_lines') or
        not isinstance(raw,(float,int)) or not math.isfinite(raw) or not .35<raw<=1 or
        not isinstance(value,(float,int)) or not math.isfinite(value) or not 0<=value<=.35 or
        correction.get('central_ink_fraction')!=value or not isinstance(width,(float,int)) or not math.isfinite(width) or width<=0):return False
    for line in correction['source_lines']:
        stroke=line.get('source_evidence',{}).get('stroke_width_native');offset=line.get('offset_from_face_native')
        if (not line.get('id') or not isinstance(stroke,(float,int)) or not math.isfinite(stroke) or not 0<stroke<=.5*width or
            not isinstance(offset,(float,int)) or not math.isfinite(offset) or not .5*width+.25<offset<=2.):return False
    return True

def group_configurations(audit,geometry,zoom):
    observations={o['observation_id']:o for o in geometry.get('observations',[])};groups=[]
    for region in geometry.get('space_closure',{}).get('regions',[]):
        label=observations.get(region.get('observation_id'))
        if not label or label.get('observation_type')!='TextRegion' or label.get('frame_id')!=geometry['frame_id']:continue
        if not region.get('region_id') or region.get('inferred_perimeter_fraction',1)>.2:continue
        candidates=[(site,boundary_side(region,site,zoom)) for site in audit['sites']]
        candidates=[(site,side) for site,side in candidates if side is not None]
        removed=[(s,d) for s,d in candidates if s['change']=='DRAWN_GAP_CLOSED']
        added=[(s,d) for s,d in candidates if s['change']=='DRAWN_GAP_CREATED']
        if not removed or not added:continue
        group=dict(id='BC'+fingerprint([VERSION,region['region_id'],sorted(s['id'] for s,d in candidates)])[:16],
            region_context_id=region['region_id'],source_label=region['name'],source_label_observation_id=region['observation_id'],
            source_label_role='Ref',source_label_claim_status=region.get('claim_status','PROPOSED'),
            target_space_identity_status='UNKNOWN_NOT_CREATED_FROM_REFERENCE',
            side_coordinate_system='NATIVE_IMAGE_AXES_NOT_VERIFIED_SURVEY_NORTH',
            status='PROPOSED',reason_codes=[],sites=[s['id'] for s,d in candidates],
            removed_sites=[dict(site_id=s['id'],side=d) for s,d in removed],
            added_sites=[dict(site_id=s['id'],side=d) for s,d in added],
            physical_door_identity_status='UNKNOWN',connection_claim_status='PROPOSED')
        if len(removed)!=1 or len(added)!=1:group['reason_codes'].append('MULTIPLE_OLD_OR_NEW_APERTURES_IN_REFERENCE_CONTEXT')
        elif removed[0][1]==added[0][1]:group['reason_codes'].append('SAME_BOUNDARY_ACCESS_CONFIGURATION_NOT_ESTABLISHED')
        if any(s['status']!='SUPPORTED_DRAWING_APERTURE_CHANGE' for s,d in candidates):group['reason_codes'].append('LOCAL_APERTURE_CHANGE_NOT_SUPPORTED')
        groups.append(group)
    counts=Counter(s for group in groups for s in group['sites'])
    for group in groups:
        if any(counts[s]!=1 for s in group['sites']):group['reason_codes'].append('COMPETING_REFERENCE_BOUNDARY_CONTEXTS')
        if not group['reason_codes']:group['status']='SUPPORTED_DRAWING_APERTURE_RECONFIGURATION'
    audit['groups']=groups
    grouped={s for g in groups for s in g['sites']}
    audit['ungrouped_sites']=[dict(site_id=s['id'],status=s['status'],
        grouping_status='UNASSOCIATED_OBSERVED_SITE',reason='NO_UNAMBIGUOUS_REFERENCE_BOUNDARY_CONTEXT_FOR_RECONFIGURATION')
        for s in audit['sites'] if s['id'] not in grouped]
    return groups

def is_boundary_aperture_row(row):
    return 'boundary_aperture_evidence' in row or row.get('automatic_confirmation')==CONFIRMATION

def is_supported_boundary_aperture_change(row):
    proof=row.get('boundary_aperture_evidence') or {};group=proof.get('group') or {};sites=proof.get('sites',[])
    if not (row.get('automatic_confirmation')==CONFIRMATION and row.get('domain')=='STRUCTURAL'
        and row.get('entity_type')=='Opening' and row.get('judgment')=='CHANGED'
        and row.get('change_types')==[CHANGE_TYPE] and row.get('identity_status')=='SUPPORTED_AT_LOCUS'
        and not row.get('alignment_fit_parent') and row.get('physical_change_inferred') is False
        and proof.get('claim_scope')==CLAIM_SCOPE and proof.get('policy_version')==VERSION
        and group.get('status')=='SUPPORTED_DRAWING_APERTURE_RECONFIGURATION' and not group.get('reason_codes')
        and group.get('physical_door_identity_status')=='UNKNOWN'
        and group.get('target_space_identity_status')=='UNKNOWN_NOT_CREATED_FROM_REFERENCE'
        and len(group.get('removed_sites',[]))==len(group.get('added_sites',[]))==1 and len(sites)==2
        and {s.get('change') for s in sites}=={'DRAWN_GAP_CREATED','DRAWN_GAP_CLOSED'}
        and set(group.get('sites',[]))=={s.get('id') for s in sites}):return False
    for key in ['verified_target_bbox_native','ref_bbox_native','target_observed_bbox_native']:
        box=row.get(key)
        if not isinstance(box,list) or len(box)!=4 or not all(isinstance(n,(float,int)) and math.isfinite(n) for n in box) or box[0]>=box[2] or box[1]>=box[3]:return False
    for site in sites:
        reg=site.get('registration',{});visibility=site.get('face_visibility',{})
        if (site.get('status')!='SUPPORTED_DRAWING_APERTURE_CHANGE' or site.get('reason_codes') or
            reg.get('status')!='SUPPORTED_LOCAL_COORDINATE_CORRESPONDENCE' or reg.get('core_used_for_fit') is not False or
            reg.get('core_image_agreement_required') is not False or len(site.get('continuous_candidates',[]))!=1):return False
        for role in ['Ref','Target']:
            source=site.get('source_bindings',{}).get(role,{})
            frame=source.get('frame_id') or ''
            if source.get('source_role')!=role or not frame.startswith(role+':') or not source.get('source_sha256') or not source.get('image_sha256'):return False
            if not all(identity.startswith(frame+':') for identity in row.get('ref_ids' if role=='Ref' else 'target_ids',[])):return False
            rows=visibility.get(role,[])
            if len(rows)!=2 or any(v.get('unoccluded_samples',0)<18 for v in rows):return False
            values=[v.get('ink_fraction') for v in rows]
            if any(not isinstance(v,(float,int)) or not math.isfinite(v) for v in values):return False
            if role==site['gap_source_role']:
                if max(values)>.35 or not all(valid_gap_face_measurement(v) for v in rows):return False
            elif min(values)<.85:return False
    return True

def attach_boundary_aperture_comparison(result,pages,geometries,alignment,strokes,vectors,scope):
    audit=compare_sites(pages,geometries,vectors,strokes,alignment,scope.roi_mask)
    result['boundary_aperture_comparison']=audit
    if audit.get('status')!='EVALUATED':return result
    groups=group_configurations(audit,geometries[0],pages[0].zoom);sites={s['id']:s for s in audit['sites']}
    side_names=dict(NORTH='북측',SOUTH='남측',WEST='서측',EAST='동측')
    for group in groups:
        pair=[sites[k] for k in group['sites']];supported=group['status']=='SUPPORTED_DRAWING_APERTURE_RECONFIGURATION'
        ids={'Ref':[],'Target':[]};refs=[group['source_label_observation_id']];boxes={r:[] for r in ['Ref','Target']}
        for site in pair:
            for i,role in enumerate(['Ref','Target']):
                g=geometries[i];is_gap=site['gap_source_role']==role
                own_refs=site['gap']['source_refs'] if is_gap else [v for b in site['continuous_candidates'] for v in b['source_refs']]
                evidence=[g['frame_id']+':'+v for v in own_refs];refs+=evidence
                identity=g['frame_id']+':boundary-aperture:'+site['id'];ids[role].append(identity)
                box=site[role+'_bbox_native'];boxes[role].append(box)
                result['source_objects'][role].append(dict(id=identity,kind='Boundary',name=None,bbox=box,refs=evidence,
                    context_refs=[],hosts=[],spaces=[],parent=None,supported=supported,
                    native_observations=dict(claim_scope=CLAIM_SCOPE,occupancy='DRAWN_GAP' if is_gap else 'CONTINUOUS_BAND',
                        frame_id=g['frame_id'],physical_door_identity_status='UNKNOWN')))
        label=group['source_label'];description='경계 개구부 구성이 달라졌습니다.'
        if len(group['removed_sites'])==len(group['added_sites'])==1:
            old=group['removed_sites'][0];new=group['added_sites'][0];site=sites[new['site_id']]
            x,y,u,v=site['Ref_bbox_native'];axis=site['axis'];lo,hi=(x,u) if axis==0 else (y,v);n0,n1=(y,v) if axis==0 else (x,u)
            section=Section(Stroke(axis,n0,lo,hi,(),False),Stroke(axis,n1,lo,hi,(),False),(),site['id'])
            names=adjoining_names(geometries[0],section,Stroke(1-axis,(lo+hi)/2,n0,n1,(),False),pages[0].zoom)
            neighbors=[n for n in names if n!=label];group['reference_boundary_adjacent_labels']=names
            destination=(' / '.join(neighbors)+' 방향의 ') if neighbors else ''
            description=f'{label}: 도면 기준 {side_names[old["side"]]} 개구부가 메워지고 {destination}{side_names[new["side"]]} 경계에 개구부가 생긴 도면 변화입니다. 공간명·인접 표기는 Ref 기준이며 동일 문짝의 이동·제품 교체는 미확인입니다.'
            refs+=list(names.values())
            old_site=sites[old['site_id']];ox,oy,ou,ov=old_site['Ref_bbox_native'];oa=old_site['axis']
            olo,ohi=(ox,ou) if oa==0 else (oy,ov);on0,on1=(oy,ov) if oa==0 else (ox,ou)
            old_section=Section(Stroke(oa,on0,olo,ohi,(),False),Stroke(oa,on1,olo,ohi,(),False),(),old_site['id'])
            previous_names=adjoining_names(geometries[0],old_section,Stroke(1-oa,(olo+ohi)/2,on0,on1,(),False),pages[0].zoom)
            previous_neighbors=[n for n in previous_names if n!=label]
            group['reference_previous_boundary_adjacent_labels']=previous_names;refs+=list(previous_names.values())
            if previous_neighbors:
                previous=' / '.join(previous_neighbors)+' 방향의 '
                description=f'{label}: 도면 기준 {previous}{side_names[old["side"]]} 개구부가 메워지고 {destination}{side_names[new["side"]]} 경계에 개구부가 생긴 도면 변화입니다. 공간명·인접 표기는 Ref 기준이며 동일 문짝의 이동·제품 교체는 미확인입니다.'
        union={r:bounds([point for b in boxes[r] for point in polygon(b)]) for r in ['Ref','Target']}
        row=dict(correspondence_id=group['id'],entity_type='Opening',domain='STRUCTURAL',
            ref_ids=ids['Ref'],target_ids=ids['Target'],cardinality='2:2',identity_status='SUPPORTED_AT_LOCUS' if supported else 'PROPOSED',
            judgment='CHANGED' if supported else 'UNDETERMINED',state='REVIEW',change_types=[CHANGE_TYPE] if supported else [],
            boundary_aperture_evidence=dict(policy_version=VERSION,claim_scope=CLAIM_SCOPE,group=group,sites=pair),
            measured_facets=['DRAWN_APERTURE_OCCUPANCY_AT_TWO_REGISTERED_BOUNDARIES'] if supported else [],
            measurements=dict(group_id=group['id'],site_ids=group['sites']),alignment_fit_parent=False,
            verified_target_bbox_native=union['Target'] if supported else None,ref_bbox_native=union['Ref'],target_observed_bbox_native=union['Target'],
            ref_site_boxes_native=boxes['Ref'],target_site_boxes_native=boxes['Target'],
            physical_change_inferred=False,physical_installation_identity_status='UNKNOWN',
            scope_status='INDEPENDENTLY_SUPPORTED_BOUNDARY_APERTURE_CONFIGURATION' if supported else 'UNCONFIRMED',
            space_name=label+' (Ref 표기 기준)',change_description=description,
            decision_reason='각 원본의 폐곡선 벽 윤곽·양쪽 지지 구간과 반대 도면의 연속 경계, 독립 구조 좌표 검증으로 두 위치의 개구 상태 차이를 확인했습니다. 실제 문 제품 동일성과 Target 공간명·연결 관계는 별도 미확정입니다.' if supported else ', '.join(group['reason_codes']),
            evidence_refs=list(dict.fromkeys(refs)),decision_constraints=group['reason_codes'],
            identity_evidence=dict(scope=CLAIM_SCOPE,geometric_loci_supported=supported,physical_door_identity_status='UNKNOWN'),
            alternatives=['SAME_DOOR_RELOCATED','OLD_ACCESS_REMOVED_AND_DIFFERENT_ACCESS_CREATED'])
        if supported:
            row['automatic_confirmation']=CONFIRMATION
            assert is_supported_boundary_aperture_change(row)
        result['correspondences'].append(row)
    result['judgments']=dict(Counter(r['judgment'] for r in result['correspondences']))
    if result['judgments'].get('CHANGED'):result['status']='REVIEW_AVAILABLE'
    result['run_id']=fingerprint([VERSION,CONTOUR_VERSION,result['run_id']])
    return result
