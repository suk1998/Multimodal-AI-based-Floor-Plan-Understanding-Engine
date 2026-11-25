"""Evidence-scoped door drawing correspondence, independent of host admission.

No physical installation identity, ontology state mutation, new registration,
or GT input. Width, swing and leaf differences are measured AFTER matching.
"""
from dataclasses import dataclass,asdict
from collections import Counter
import math
import cv2
import numpy as np
from backend.opening_geometry import local_registration
from backend.wall_sections import ink_support
from backend.structural_alignment import fingerprint,transform
from integrations.fpue_ontology import understanding_contract_v0_5 as ontology

VERSION='door_drawing_correspondence/1'

@dataclass(frozen=True)
class Settings:
    minimum_ink:float=.85
    minimum_stile_overlap:float=.5
    opening_plane_angle_degrees:float=2.
    maximum_combinations:int=10000

def bounds(points):
    p=np.asarray(points,float).reshape(-1,2)
    return [*p.min(0).tolist(),*p.max(0).tolist()]

def polygon(box):
    x,y,u,v=box
    return [[x,y],[u,y],[u,v],[x,v]]

def source_doors(g,graph):
    """Reuse the preceding own-frame geometry proof; keep weak candidates too."""
    nodes={e['id']:e for e in graph.get('entities',[]) if e['kind']=='Door'}
    proofs={e['evidence_id']:e for e in g.get('evidence',[])}
    openings={o['opening_id']:o for o in g.get('architectural_assessment',{}).get('openings',[])}
    relation_rows=[*g.get('architectural_assessment',{}).get('relations',[]),
                   *g.get('reference_relations' if g['source_role']=='Ref' else 'target_relations',{}).get('relations',[])]
    out=[]
    for item in g.get('architectural_assessment',{}).get('installed_elements',[]):
        if item.get('type_candidates')!=['Door']:continue
        node=nodes.get(item['installation_id']);opening=openings.get(item['opening_id'])
        if not node or not opening:continue
        support=[s for ref in item['evidence_refs'] for s in proofs.get(ref,{}).get('supporting',[])]
        bracket=next((s for s in support if s['kind']=='GAP_BRACKETS_TRACKS'),{})
        swings=node.get('swing_support',[])
        contradicting=[c for ref in item['evidence_refs'] for c in proofs.get(ref,{}).get('contradicting',[])]
        hosting=[];installation=[]
        for relation in relation_rows:
            source=relation.get('source_id',relation.get('source',{}).get('id'))
            target=relation.get('target_id',relation.get('target',{}).get('id'))
            kind=relation.get('kind',relation.get('relation_type'))
            if target!=opening['opening_id']:continue
            record={k:relation[k] for k in ['id','relation_id','kind','relation_type','claim_status','admissibility','evidence_refs'] if k in relation}
            if kind in ['Hosting','hosts']:hosting.append(record)
            if kind in ['Installation','installedIn'] and source==item['installation_id']:installation.append(record)
        out.append(dict(id=item['installation_id'],frame_id=g['frame_id'],source_role=g['source_role'],
            bbox_native=node['bbox_native'],segment=opening['opening_segment_native'],
            caps=bracket.get('jamb_brackets_native',[]),cap_visibility=bracket.get('cap_visibility',[]),
            strong=node.get('geometry_status')=='SUPPORTED_SWING_LEAF_AND_JAMBS' and len(swings)==1,
            swing=swings[0] if len(swings)==1 else None,
            span_semantics='FRAME_STILE_INTERVAL' if item.get('installation_shape_only') else 'WALL_BRACKET_INTERVAL',
            host_status=opening['decision'],opening_id=opening['opening_id'],
            source_geometry_contradiction=opening['decision']=='CONTRADICTED' or any(c.get('kind')=='SOLID_BAND_CONTRADICTS_GAP' for c in contradicting),
            source_geometry_incomplete=any(r in ['CONTEXT_CUT_OPENING_NOT_COMPLETE','BOTH_BRACKETS_NOT_VISUALLY_SUPPORTED'] for r in opening.get('reasons',[])),
            original_opening_reason_codes=opening.get('reasons',[]),source_contradicting_evidence=contradicting,
            installation_claim_status=item['claim_status'],physical_identity_status='UNKNOWN',
            original_host_relation_claims=hosting,original_installation_relation_claims=installation,
            evidence_refs=list(dict.fromkeys([*item['evidence_refs'],*opening['evidence_refs'],*[r for s in support for r in s.get('source_observation_refs',[])]]))))
    return out

def anchor_metrics(a,b,M,tolerance,cfg=Settings()):
    p=np.asarray(a['segment']);q=transform(b['segment'],M)
    da=p[1]-p[0];db=q[1]-q[0];la=float(np.linalg.norm(da));lb=float(np.linalg.norm(db))
    if min(la,lb)<1e-6:return dict(sufficient=False,reasons=['DEGENERATE_OBSERVED_INTERVAL'],anchor_error_ref_px=1e9)
    u=da/la;n=np.array([-u[1],u[0]])
    angle=math.degrees(math.acos(float(np.clip(abs(da@db/(la*lb)),-1,1))))
    normal=float(max(abs((v-p[0])@n) for v in q))
    lo,hi=sorted(float((v-p[0])@u) for v in q);overlap=max(0.,min(la,hi)-max(0.,lo))
    cap_matches=[]
    for i,ra in enumerate(a['caps']):
        ra=np.asarray(ra);direction=ra[1]-ra[0];length=np.linalg.norm(direction)
        if length<1e-6:continue
        cu=direction/length;cn=np.array([-cu[1],cu[0]])
        for j,ta in enumerate(b['caps']):
            ta=transform(ta,M);tl=np.linalg.norm(ta[1]-ta[0])
            if tl<1e-6:continue
            ca=math.degrees(math.acos(float(np.clip(abs((ta[1]-ta[0])@cu/tl),-1,1))))
            error=float(max(abs((v-ra[0])@cn) for v in ta))
            low,high=sorted(float((v-ra[0])@cu) for v in ta)
            common=max(0.,min(length,high)-max(0.,low))
            cap_matches.append(dict(ref_stile_index=i,target_stile_index=j,normal_error_ref_px=error,
                direction_difference_degrees=ca,common_stile_fraction=float(common/min(length,tl))))
    eligible=[r for r in cap_matches if r['direction_difference_degrees']<=cfg.opening_plane_angle_degrees and r['common_stile_fraction']>=cfg.minimum_stile_overlap]
    best=min((r['normal_error_ref_px'] for r in eligible),default=1e9)
    reasons=[]
    if angle>cfg.opening_plane_angle_degrees:reasons.append('OPENING_PLANE_DIRECTION_UNRESOLVED')
    if normal>tolerance:reasons.append('OPENING_NORMAL_POSITION_NOT_SUPPORTED')
    if best>tolerance:reasons.append('NO_SHARED_OBSERVED_STILE_LOCUS')
    if overlap<=2*tolerance:reasons.append('NO_POSITIVE_SHARED_OPENING_INTERIOR')
    return dict(sufficient=not reasons,reasons=reasons,opening_direction_degrees=angle,
        opening_normal_error_ref_px=normal,interior_overlap_ref_px=overlap,
        observed_stile_matches=cap_matches,anchor_error_ref_px=float(max(normal,best)),
        tolerance_ref_px=tolerance,width_equality_required=False,swing_equality_required=False,leaf_equality_required=False)

def position_evidence(a,b,pages,strokes,grays,alignment):
    """Fixed transform only. Core NCC never certifies or vetoes its own change."""
    fit_parents={p.get('parent_structure_id') for p in alignment.get('pairs',[]) if p.get('usage')=='FIT'}
    heldout_parents={p.get('parent_structure_id') for p in alignment.get('pairs',[]) if p.get('usage')=='VALIDATION'}
    if fit_parents&heldout_parents:
        return dict(status='UNCONFIRMED',reason='FIT_AND_VALIDATION_PARENT_NOT_INDEPENDENT')
    M=np.asarray(alignment['matrix']);inverse=np.linalg.inv(M)
    union_target=bounds([*polygon(b['bbox_native']),*transform(polygon(a['bbox_native']),inverse).tolist()])
    union_ref=bounds(transform(polygon(union_target),M))
    for pair in alignment.get('pairs',[]):
        if pair.get('usage')!='FIT':continue
        for prefix,box,page in [('ref',union_ref,pages[0]),('target',union_target,pages[1])]:
            for name in [prefix+'_segment',prefix+'_other_face']:
                points=np.asarray(pair.get(name,[]))
                if points.shape!=(2,2):return dict(status='UNCONFIRMED',reason='FIT_INTERVAL_PROVENANCE_INCOMPLETE')
                lo=points.min(0);hi=points.max(0);guard=2*page.zoom
                if np.all(hi>=np.array(box[:2])-guard) and np.all(lo<=np.array(box[2:])+guard):
                    return dict(status='UNCONFIRMED',reason='CHANGED_LOCUS_INTERSECTS_ALIGNMENT_FIT_INTERVAL')
    r=local_registration(dict(polygon_native=polygon(a['bbox_native'])),dict(polygon_native=polygon(b['bbox_native'])),
                         pages,strokes,grays,alignment)
    if r['status']=='SUPPORTED_LOCAL_COORDINATE_CORRESPONDENCE':return r
    if r.get('reason')!='MISSING_TWO_DIRECTIONAL_EXTERNAL_WALL_WITNESSES':return r
    # All earlier reader guards (independent holdout, hull, native bounds, fit
    # exclusion) passed. Re-read the ACTUAL independent holdout faces, rather
    # than lower the wall reader's stroke/depth thresholds for this drawing.
    M=np.asarray(alignment['matrix']);proofs=[];pairs={p['id']:p for p in alignment['pairs']}
    maximum=0.
    for row in alignment['rows']:
        if row.get('usage')!='VALIDATION':continue
        pair=pairs.get(row['id'])
        if not pair or not row.get('passed'):return dict(status='UNCONFIRMED',reason='INDEPENDENT_HOLDOUT_NOT_PASSED')
        checks=[]
        for rk,tk in [('ref_segment','target_segment'),('ref_other_face','target_other_face')]:
            p=np.asarray(pair[rk]);q=transform(pair[tk],M);d=p[1]-p[0];norm=np.array([-d[1],d[0]])/np.linalg.norm(d)
            residual=float(max(abs((v-p[0])@norm) for v in q));maximum=max(maximum,residual)
            ink=[ink_support(grays[0],p),ink_support(grays[1],pair[tk])]
            if any(v is None or v<.85 for v in ink) or residual>alignment['settings']['normal_tolerance_ref_px']:
                return dict(status='UNCONFIRMED',reason='HOLDOUT_SOURCE_PIXELS_OR_RESIDUAL_FAILED',pair_id=pair['id'],residual_ref_px=residual)
            checks.append(dict(Ref=p.tolist(),Target=pair[tk],ink_support=ink,maximum_normal_residual_ref_px=residual))
        proofs.append(dict(pair_id=pair['id'],parent_structure_id=pair['parent_structure_id'],axis=pair['axis'],faces=checks,
                           source_evidence=pair.get('evidence',{})))
    if {p['axis'] for p in proofs}!={0,1} or len({p['parent_structure_id'] for p in proofs})<2:
        return dict(status='UNCONFIRMED',reason='INSUFFICIENT_INDEPENDENT_DIRECTIONAL_HOLDOUT')
    return dict(status='SUPPORTED_LOCAL_COORDINATE_CORRESPONDENCE',
        method='EXISTING_DIRECTIONAL_HOLDOUT_HULL_AND_REREAD_INDEPENDENT_SOURCE_FACES',
        matrix_target_to_ref=M.tolist(),core_used_for_fit=False,core_image_agreement_required=False,
        existing_hull_guard_passed=True,fit_exclusion_guard_passed=True,holdout_faces=proofs,
        global_holdout_maximum_normal_residual_ref_px=maximum,
        local_reader_limitation=r['reason'],alignment_status_unchanged=True)

def roi_contains(a,b,M,roi_mask):
    p=np.r_[polygon(b['bbox_native']),transform(polygon(a['bbox_native']),np.linalg.inv(M))]
    x,y=np.floor(p.min(0)).astype(int);u,v=np.ceil(p.max(0)).astype(int)+1
    return 0<=x<u<=roi_mask.shape[1] and 0<=y<v<=roi_mask.shape[0] and bool(np.all(roi_mask[y:v,x:u]))

def relation_facts(g,door):
    """Read existing states literally. HOLD/PROPOSED is not a contradiction."""
    spaces={r['region_id']:r for r in g.get('space_closure',{}).get('regions',[]) if 'region_id' in r}
    known={e['evidence_id'] for e in g.get('evidence',[])}|{o['observation_id'] for o in g.get('observations',[])}
    verified=[];unresolved=[];supported=[]
    key='reference_relations' if g['source_role']=='Ref' else 'target_relations'
    source_relations=g.get(key,{})
    for group in ['door_reviews','code_reviews']:
        known.update(r['id'] for r in source_relations.get(group,[]) if r.get('id','').startswith(g['frame_id']+':'))
    known.update(r['id'] for r in source_relations.get('code_reobservations',{}).get('observations',[]) if r.get('id','').startswith(g['frame_id']+':'))
    for relation in g.get(key,{}).get('relations',[]):
        if relation.get('kind')!='Connection' or relation.get('via_entity_id')!=door['id']:continue
        if relation.get('claim_status')!='VERIFIED':
            supported.append(dict(id=relation.get('id'),claim_status=relation.get('claim_status')));continue
        ids=[relation.get('source',{}).get('id'),relation.get('target',{}).get('id')]
        refs=relation.get('evidence_refs',[])
        try:ontology.validate_relation_signature('Connection',relation.get('source',{}).get('type'),relation.get('target',{}).get('type'))
        except ValueError:
            unresolved.append(dict(id=relation.get('id'),reason='INVALID_VERIFIED_RELATION_SIGNATURE'));continue
        if (not refs or not set(refs)<=known or any(not r.startswith(g['frame_id']+':') for r in refs)
            or relation.get('source_role',g['source_role'])!=g['source_role'] or relation.get('frame_id',g['frame_id'])!=g['frame_id']
            or any(i not in spaces or spaces[i].get('claim_status')!='VERIFIED' for i in ids)
            or any(not i.startswith(g['frame_id']+':') for i in ids)):
            unresolved.append(dict(id=relation.get('id'),reason='UNRESOLVED_VERIFIED_RELATION_PROVENANCE'));continue
        verified.append(dict(id=relation['id'],space_ids=ids,evidence_refs=refs))
    return dict(verified=verified,unresolved_verified=unresolved,supported_or_partial=supported,
                physical_host_status=door['host_status'])

def relation_check(a,b,geometries,comparison_rows):
    facts=[relation_facts(g,d) for g,d in zip(geometries,[a,b])]
    if any(f['unresolved_verified'] for f in facts):
        return dict(status='REVIEW',reason='VERIFIED_RELATION_REFERENCE_INTEGRITY_UNRESOLVED',sources=facts)
    mapping={};reverse={}
    for row in comparison_rows:
        if row['entity_type']=='Space' and row['identity_status']=='SUPPORTED' and len(row['ref_ids'])==len(row['target_ids'])==1:
            t,r=row['target_ids'][0],row['ref_ids'][0]
            mapping.setdefault(t,set()).add(r);reverse.setdefault(r,set()).add(t)
    conflicts=[]
    for ref in facts[0]['verified']:
        for target in facts[1]['verified']:
            if not all(len(mapping.get(t,set()))==1 for t in target['space_ids']):continue
            mapped={next(iter(mapping[t])) for t in target['space_ids']}
            if not all(len(reverse.get(r,set()))==1 for r in mapped):continue
            if mapped!=set(ref['space_ids']):
                conflicts.append(dict(ref_relation_id=ref['id'],target_relation_id=target['id'],
                    ref_space_ids=ref['space_ids'],target_space_ids=target['space_ids'],mapped_target_space_ids=sorted(mapped),
                    interpretation='CONNECTION_CHANGED_OR_DIFFERENT_OBJECT_NOT_AUTOMATICALLY_RESOLVED'))
    return dict(status='CONFLICT' if conflicts else 'HOLD' if not all(f['verified'] for f in facts) else 'CONSISTENT_OR_PARTIAL_MAPPING',
                reason='VERIFIED_CONNECTION_CONTRADICTION' if conflicts else 'NO_VERIFIED_CONTRADICTION',conflicts=conflicts,sources=facts)

def leaf_profiles(door,vector,gray,zoom,exclusions=()):
    if vector is None or not door['swing']:return []
    swing=door['swing'];center=np.asarray(swing['hinge_candidate_native']);radius=swing['swing_radius_px'];found=[]
    leaves=[l for l in swing['leaf_candidates'] if l['visible_fraction']>=.8]
    for leaf in leaves:
        p=np.asarray(leaf['segment_native']);axis=int(abs(p[1,1]-p[0,1])>abs(p[1,0]-p[0,0]));normal=1-axis
        for line in vector['lines']:
            if line['axis']!=axis or abs(line['normal']-center[normal])>max(3*zoom,radius*.15):continue
            lo,hi=sorted(p[:,axis]);span=max(0,min(hi,line['hi'])-max(lo,line['lo']))
            if span<radius*.6 or line['hi']-line['lo']>radius*1.3:continue
            if any(k in line['source_evidence'].get('layer','').upper() for k in ['DIM','FUR','TOI','A-TE','CEN']):continue
            box=bounds(line['points_native'])
            if any(not(box[2]<e[0] or box[0]>e[2] or box[3]<e[1] or box[1]>e[3]) for e in exclusions):continue
            ink=ink_support(gray,line['points_native'])
            if ink is not None and ink>=.85:
                found.append(dict(source_ref=line['id'],points_native=line['points_native'],ink_support=ink,
                                  source_evidence=line['source_evidence']))
    candidates=list({f['source_ref']:f for f in found}.values());closed=[];tolerance=max(.5*zoom,.5)
    # A nearby parallel wall face is not a leaf side. Require TWO observed
    # sides joined by both transverse end caps. PDF path grouping/fragment
    # length alone is not evidence of leaf ownership.
    for i,a in enumerate(candidates):
        pa=np.asarray(a['points_native']);axis=int(abs(np.diff(pa,axis=0)[0,1])>abs(np.diff(pa,axis=0)[0,0]));normal=1-axis
        for b in candidates[i+1:]:
            pb=np.asarray(b['points_native']);other=int(abs(np.diff(pb,axis=0)[0,1])>abs(np.diff(pb,axis=0)[0,0]))
            if other!=axis:continue
            ns=sorted([float(pa[:,normal].mean()),float(pb[:,normal].mean())])
            if ns[1]-ns[0]<.25*zoom:continue
            lo=max(pa[:,axis].min(),pb[:,axis].min());hi=min(pa[:,axis].max(),pb[:,axis].max())
            if hi-lo<radius*.75:continue
            caps=[]
            for end in [lo,hi]:
                pool=[s for s in vector['lines'] if s['axis']!=axis and abs(s['normal']-end)<=tolerance
                    and s['lo']<=ns[0]+tolerance and s['hi']>=ns[1]-tolerance
                    and s['hi']-s['lo']<=ns[1]-ns[0]+2*tolerance
                    and (ink_support(gray,s['points_native']) or 0)>=.85]
                if pool:caps.append(pool[0])
            if len(caps)!=2:continue
            outline='leaf:'+fingerprint(sorted([a['source_ref'],b['source_ref']]))[:16]
            closed.extend([dict(s,outline_id=outline,end_cap_source_refs=[c['id'] for c in caps],
                end_caps_native=[c['points_native'] for c in caps]) for s in [a,b]])
    return closed

def hausdorff(a,b):
    a=np.asarray(a);b=np.asarray(b)
    distance=np.linalg.norm(a[:,None,:]-b[None,:,:],axis=2)
    return float(max(distance.min(0).max(),distance.min(1).max()))

def measure_facets(a,b,M,tolerance,profiles):
    scale=float(np.linalg.norm(M[:2,0]));ra=a['swing'];ta=b['swing'];rows=[]
    length=lambda s:float(np.linalg.norm(np.diff(s,axis=0)))
    same=a['span_semantics']==b['span_semantics']
    delta=length(transform(b['segment'],M))-length(a['segment'])
    rows.append(dict(facet='OBSERVED_FRAME_INTERVAL',status='SUPPORTED' if same else 'HOLD',changed=abs(delta)>tolerance if same else None,
                     delta_ref_px=delta,source_semantics=[a['span_semantics'],b['span_semantics']],physical_door_width_inferred=False))
    def fit_error(s):return float(np.sqrt(np.mean((np.linalg.norm(np.asarray(s['swing_polyline_native'])-s['hinge_candidate_native'],axis=1)-s['swing_radius_px'])**2)))
    swing_tolerance=max(tolerance,fit_error(ra)+fit_error(ta)*scale)
    rows.append(dict(facet='OBSERVED_SWING_RADIUS',status='SUPPORTED',delta_ref_px=ta['swing_radius_px']*scale-ra['swing_radius_px'],
                     uncertainty_ref_px=swing_tolerance,changed=abs(ta['swing_radius_px']*scale-ra['swing_radius_px'])>swing_tolerance))
    rc=np.asarray(ra['hinge_candidate_native']);tc=transform([ta['hinge_candidate_native']],M)[0]
    rm=np.asarray(ra['swing_polyline_native'])[len(ra['swing_polyline_native'])//2]-rc
    tm=transform([ta['swing_polyline_native'][len(ta['swing_polyline_native'])//2]],M)[0]-tc
    angle=math.degrees(math.acos(float(np.clip(rm@tm/(np.linalg.norm(rm)*np.linalg.norm(tm)),-1,1))))
    angle_tolerance=math.degrees(math.atan2(swing_tolerance,min(ra['swing_radius_px'],ta['swing_radius_px']*scale)))
    rows.append(dict(facet='OBSERVED_SWING_DIRECTION',status='SUPPORTED',difference_degrees=angle,
                     uncertainty_degrees=angle_tolerance,changed=angle>angle_tolerance))
    for i,key in enumerate(['Ref','Target']):rows.append(dict(facet=key+'_LEAF_PROFILE_COVERAGE',status='SUPPORTED' if len(profiles[i])>=2 else 'HOLD',source_line_count=len(profiles[i])))
    unambiguous=all(len(p)>=2 and len({f.get('outline_id') for f in p})==1 for p in profiles)
    if unambiguous:
        def samples(items):return np.concatenate([np.asarray(f['points_native'])[0]+np.linspace(0,1,31)[:,None]*np.diff(f['points_native'],axis=0)[0] for f in items])
        distance=hausdorff(samples(profiles[0]),transform(samples(profiles[1]),M))
        stroke_uncertainty=float(sum(float(max(f['source_evidence'].get('stroke_width_native',0) for f in p))*s*.5 for p,s in zip(profiles,[1.,scale])))
        leaf_tolerance=float(tolerance+stroke_uncertainty)
        rows.append(dict(facet='OBSERVED_LEAF_PROFILE',status='SUPPORTED',maximum_profile_distance_ref_px=distance,changed=bool(distance>leaf_tolerance),
                         uncertainty_ref_px=leaf_tolerance,
                         scope='OBSERVED_LONG_LEAF_SIDES_NOT_UNOBSERVED_FULL_CONTOUR'))
    else:rows.append(dict(facet='OBSERVED_LEAF_PROFILE',status='HOLD',changed=None,reason='COMPLETE_UNAMBIGUOUS_LEAF_SIDES_AND_CAPS_NOT_AVAILABLE_IN_BOTH_SOURCES'))
    return rows

def compare_doors(pages,geometries,graphs,alignment,strokes,vectors,roi_mask,comparison_rows,cfg=Settings()):
    sources=[source_doors(g,graphs[r]) for g,r in zip(geometries,['Ref','Target'])]
    out=dict(schema_version=VERSION,settings=asdict(cfg),source_counts=[len(s) for s in sources],candidates=[],
             source_diagnostics=[dict(source_role=r,status=graphs[r].get('status'),reason=graphs[r].get('reason')) for r in ['Ref','Target']],
             ontology_version=ontology.VERSION,ontology_contract_sha256=ontology.contract_digest(),
             physical_identity_inferred=False,ontology_states_modified=False)
    if not all(sources):return dict(out,status='DEFERRED',reason='MISSING_DOOR_OBSERVATIONS_IN_ONE_OR_BOTH_SOURCES')
    if len(sources[0])*len(sources[1])>cfg.maximum_combinations:return dict(out,status='DEFERRED',reason='CANDIDATE_SEARCH_BUDGET_EXCEEDED')
    if alignment.get('status') not in ['PARTIAL','VERIFIED_ROI'] or alignment.get('matrix') is None:
        return dict(out,status='DEFERRED',reason='NO_SUPPORTED_FIXED_TRANSFORM')
    M=np.asarray(alignment['matrix'])
    if M.shape!=(3,3) or not np.isfinite(M).all() or not np.allclose(M[2],[0,0,1]):
        return dict(out,status='DEFERRED',reason='INVALID_FIXED_TRANSFORM')
    scale=np.linalg.norm(M[:2,0])
    if max(abs(M[0,1]),abs(M[1,0]),abs(M[0,0]-M[1,1]))>1e-6 or scale<=0:
        return dict(out,status='DEFERRED',reason='NONAXIAL_OR_ANISOTROPIC_MAPPING_UNSUPPORTED')
    grays=[cv2.imdecode(np.frombuffer(p.png,np.uint8),0) for p in pages]
    own_vectors=[];exclusions=[]
    for page,g,graph,v in zip(pages,geometries,[graphs['Ref'],graphs['Target']],vectors or [None,None]):
        valid=(v is not None and v.get('source_sha256')==page.source_sha256 and v.get('page')==page.page_number
            and v.get('extraction_zoom')==page.zoom and v.get('image_size')==[page.width,page.height])
        own_vectors.append(v if valid else None)
        exclusions.append([bounds(o['geometry_native']['points_native']) for o in g.get('observations',[]) if o['observation_type']=='TextRegion']+
                          [e['bbox_native'] for e in graph.get('entities',[]) if e['kind']=='Symbol'])
    for side,items in enumerate(sources):
        for item in items:
            item['source_stile_ink_support']=[ink_support(grays[side],c) for c in item['caps']]
            if len(item['source_stile_ink_support'])!=2 or any(v is None or v<cfg.minimum_ink for v in item['source_stile_ink_support']):
                item['strong']=False
    residual=alignment.get('wall_registration',{}).get('independent_max_normal_ref_px')
    if residual is None or not math.isfinite(residual):return dict(out,status='DEFERRED',reason='MISSING_INDEPENDENT_RESIDUAL')
    tolerance=max(.5*pages[0].zoom,2*residual+pages[0].zoom*.5)
    all_edges=[]
    for a in sources[0]:
        for b in sources[1]:
            distance=float(np.linalg.norm(np.mean(a['segment'],0)-transform([np.mean(b['segment'],0)],M)[0]))
            geometry=anchor_metrics(a,b,M,tolerance,cfg)
            # A large width change can move the interval centroid far away
            # while its observed hinge-side stile remains at the same locus.
            if distance>max(100*pages[0].zoom,np.linalg.norm(np.diff(a['segment'],axis=0))*1.5) and not geometry['sufficient']:continue
            reasons=list(geometry['reasons'])
            if not a['strong'] or not b['strong']:reasons.append('INSUFFICIENT_INDEPENDENT_SOURCE_DOOR_GEOMETRY')
            if a['source_geometry_contradiction'] or b['source_geometry_contradiction']:reasons.append('SOURCE_GEOMETRY_CONTRADICTION')
            if a['source_geometry_incomplete'] or b['source_geometry_incomplete']:reasons.append('SOURCE_GEOMETRY_TRUNCATED_OR_UNSUPPORTED')
            if not roi_contains(a,b,M,roi_mask):reasons.append('DOOR_CORE_OUTSIDE_USER_ROI')
            registration=dict(status='NOT_EVALUATED_AFTER_ABSOLUTE_FAILURE')
            if not reasons:
                if strokes is None or not all(strokes):registration=dict(status='UNCONFIRMED',reason='SOURCE_WALL_WITNESSES_UNAVAILABLE')
                else:registration=position_evidence(a,b,pages,strokes,grays,alignment)
                if registration['status']!='SUPPORTED_LOCAL_COORDINATE_CORRESPONDENCE':reasons.append(registration.get('reason','COORDINATE_SUPPORT_MISSING'))
            relation=relation_check(a,b,geometries,comparison_rows)
            geometric_support=not reasons
            if relation['status'] in ['CONFLICT','REVIEW']:reasons.append(relation['reason'])
            edge=dict(id='DG'+fingerprint([VERSION,a['id'],b['id']])[:16],Ref=a,Target=b,geometry_evidence=geometry,
                claim_scope='OBSERVED_DOOR_DRAWING_GEOMETRY',
                registration=registration,relation_assessment=relation,absolute_support=not reasons,
                absolute_geometry_support=geometric_support,reason_codes=reasons,
                tolerance_ref_px=tolerance,measured_facets=[],status='PROPOSED')
            all_edges.append(edge)
    for edge in all_edges:
        if not edge['absolute_support']:continue
        upper=edge['geometry_evidence']['anchor_error_ref_px']+tolerance
        competitors=[]
        for other in all_edges:
            if other is edge or not (other['Ref']['id']==edge['Ref']['id'] or other['Target']['id']==edge['Target']['id']):continue
            g=other['geometry_evidence']
            if g.get('interior_overlap_ref_px',0)<=0:continue
            lower=max(0,g['anchor_error_ref_px']-tolerance)
            if lower<=upper+.5*pages[0].zoom:
                competitors.append(dict(candidate_id=other['id'],anchor_error_lower_ref_px=lower,
                    competitor_absolute_support=other['absolute_support'],competitor_reasons=other['reason_codes']))
        reciprocal=all(not any(other is not edge and other[key]['id']==edge[key]['id'] and
            other['geometry_evidence']['anchor_error_ref_px']<edge['geometry_evidence']['anchor_error_ref_px'] for other in all_edges)
            for key in ['Ref','Target'])
        edge['competitor_assessment']=dict(status='DISTINGUISHABLE' if not competitors and reciprocal else 'AMBIGUOUS',
            best_error_upper_ref_px=upper,separation_guard_ref_px=.5*pages[0].zoom,competing_candidates=competitors,
            reciprocal_best=reciprocal,mutual_best_match_is_not_sufficient=True)
        if competitors:edge['reason_codes'].append('COMPETING_CANDIDATE_UNCERTAINTY_INTERVALS_OVERLAP');continue
        if not reciprocal:edge['reason_codes'].append('NOT_RECIPROCALLY_PREFERRED');continue
        edge['status']='SUPPORTED_GEOMETRIC_CORRESPONDENCE'
        profiles=[leaf_profiles(d,v,g,p.zoom,e) for d,v,g,p,e in zip([edge['Ref'],edge['Target']],own_vectors,grays,pages,exclusions)]
        edge['leaf_profiles']={r:p for r,p in zip(['Ref','Target'],profiles)}
        edge['measured_facets']=measure_facets(edge['Ref'],edge['Target'],M,tolerance,profiles)
    out.update(status='EVALUATED',candidates=all_edges,fixed_matrix_target_to_ref=M.tolist(),
               source_geometry_support_separate_from_physical_host=True)
    return out

def attach_door_correspondence(result,pages,geometries,alignment,strokes,vectors,scope):
    from backend import door_change_policy as policy
    graphs=result.get('element_grounding',{}).get('sources',{})
    if set(graphs)!={'Ref','Target'}:return result
    audit=compare_doors(pages,geometries,graphs,alignment,strokes,vectors,scope.roi_mask,result['correspondences'])
    result['door_geometric_correspondence']=audit
    legacy_rows=list(result['correspondences'])
    for edge in audit.get('candidates',[]):
        a,b=edge['Ref'],edge['Target'];supported=edge['status']=='SUPPORTED_GEOMETRIC_CORRESPONDENCE'
        changes=[f['facet'] for f in edge['measured_facets'] if f.get('status')=='SUPPORTED' and f.get('changed')]
        facts=edge['measured_facets'];partial=any(f['status']=='HOLD' for f in facts)
        judgment='CHANGED' if supported and changes else 'UNCHANGED_WITHIN_SCOPE' if supported else 'UNDETERMINED'
        reason=('원본 문 형상과 공통 문틀 위치, 독립 구조 좌표 검증 및 경쟁 후보 구분으로 도면상의 대응을 지지합니다. 실제 설치·호스트 관계와 제품 동일성은 별도 미확정입니다.'
                if supported else '기하 대응 보류: '+', '.join(edge['reason_codes']))
        row=dict(correspondence_id=edge['id'],entity_type='Door',domain='STRUCTURAL',ref_ids=[a['id']],target_ids=[b['id']],
            cardinality='1:1',identity_status='SUPPORTED' if supported else 'PROPOSED',state='REVIEW',judgment=judgment,
            identity_evidence=dict(scope='REGISTERED_DOOR_DRAWING_LOCUS_NOT_PHYSICAL_PRODUCT_IDENTITY',
                absolute_geometry_supported=edge['absolute_geometry_support'],competitor_assessment=edge.get('competitor_assessment'),
                physical_identity_status='UNKNOWN'),door_geometry_evidence=edge,
            geometric_correspondence_status=edge['status'],physical_installation_identity_status='UNKNOWN',
            source_installation_entity_claim_status={'Ref':a['installation_claim_status'],'Target':b['installation_claim_status']},
            source_opening_admissibility={'Ref':a['host_status'],'Target':b['host_status']},
            source_host_relation_claims={'Ref':a['original_host_relation_claims'],'Target':b['original_host_relation_claims']},
            source_installation_relation_claims={'Ref':a['original_installation_relation_claims'],'Target':b['original_installation_relation_claims']},
            physical_change_inferred=False,
            decision_reason=reason,change_description=('문 도면 관찰 차이: '+', '.join(changes)) if changes else
                '측정한 문 도면 관찰 항목에서 차이를 확인하지 못했습니다. 미측정 부분과 제품 동일성은 미확인입니다.' if supported else reason,
            measured_facets=[f['facet'] for f in facts if f['status']=='SUPPORTED'],change_types=changes,
            measurements=dict(facets=facts,unmeasured_facets_remain_unknown=partial,matching_used_width_swing_leaf_equality=False),
            ref_bbox_native=a['bbox_native'],target_observed_bbox_native=b['bbox_native'],
            verified_target_bbox_native=bounds([*polygon(b['bbox_native']),*transform(polygon(a['bbox_native']),np.linalg.inv(alignment['matrix'])).tolist()]) if supported else None,alignment_fit_parent=False,
            scope_status='EXTERNALLY_SUPPORTED_DOOR_DRAWING_LOCUS' if supported else 'UNCONFIRMED',
            space_name=None,evidence_refs=list(dict.fromkeys(a['evidence_refs']+b['evidence_refs'])),
            alternatives=[] if supported else ['CONNECTION_RELATION_CHANGED','DIFFERENT_OBJECT','CONTRADICTORY_INPUT_FACTS']
                if edge['relation_assessment']['status']=='CONFLICT' else ['DIFFERENT_OBJECT','MODIFIED_OR_MOVED_OBJECT','INSUFFICIENT_OBSERVATION'])
        for facts in edge['relation_assessment']['sources']:
            for fact in facts['verified']:
                row['evidence_refs'].extend([fact['id'],*fact['evidence_refs']])
        row['evidence_refs']=list(dict.fromkeys(row['evidence_refs']))
        if supported and changes:
            row['automatic_confirmation']=policy.CONFIRMATION
            if not policy.is_supported_door_change(row):
                row.pop('automatic_confirmation')
                row['automatic_output_hold_reason']='DOOR_DRAWING_OUTPUT_CONTRACT_NOT_SATISFIED'
            else:
                labels={'OBSERVED_FRAME_INTERVAL':'관측 문틀 간격','OBSERVED_SWING_RADIUS':'관측 스윙 반경',
                        'OBSERVED_SWING_DIRECTION':'관측 스윙 방향','OBSERVED_LEAF_PROFILE':'관측 문짝 선분 형상'}
                row['change_description']='문 도면의 '+', '.join(labels[c] for c in changes)+'이 달라졌습니다. 실제 제품·설치·연결 공간의 변경은 별도 미확정입니다.'
        related=[]
        for old in legacy_rows:
            if old['entity_type']!='Door' or not (set(old['ref_ids'])&{a['id']} or set(old['target_ids'])&{b['id']}):continue
            related.append(old['correspondence_id'])
            if supported and old['judgment']=='UNDETERMINED':
                old.setdefault('geometric_reassociation_ids',[]).append(row['correspondence_id'])
                old['output_disposition']='REASSOCIATED_GEOMETRY_EVIDENCE'
        row['related_legacy_correspondence_ids']=related
        result['correspondences'].append(row)
    result['judgments']=dict(Counter(r['judgment'] for r in result['correspondences']))
    result['run_id']=fingerprint([VERSION,policy.VERSION,result['run_id']])
    return result
