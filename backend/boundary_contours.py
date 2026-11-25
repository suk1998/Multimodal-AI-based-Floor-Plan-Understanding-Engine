"""Positive segmented contour evidence, including observed orthogonal terminals.

No semantic wall classification or inter-drawing information is used here.
Faces and both transverse caps must exist; no missing edge is synthesized.
"""
import re
import numpy as np
from backend.structural_alignment import fingerprint
from backend.wall_sections import ink_support

VERSION='segmented_boundary_contours/1'

def segment(axis,normal,lo,hi):
    return [[lo,normal],[hi,normal]] if axis==0 else [[normal,lo],[normal,hi]]

def eligible_lines(vector,zoom,cfg):
    return [l for l in vector['lines']
        if l['source_evidence'].get('stroke_width_native',0)>=cfg.minimum_stroke_zoom*zoom
        and (not l['source_evidence'].get('layer') or
             re.fullmatch(r'A-WAL[0-9]*',l['source_evidence']['layer'].split('|')[-1],re.I))]

def segmented_bands(vector,gray,zoom,role,cfg):
    """Read closed local contours whose four edges may span different Paths."""
    eps=.05*zoom;lines=eligible_lines(vector,zoom,cfg);out=[]
    axes={axis:sorted([l for l in lines if l['axis']==axis],key=lambda l:l['normal']) for axis in (0,1)}
    for axis in (0,1):
        aa=axes[axis];cross=axes[1-axis]
        for i,a in enumerate(aa):
            for c in aa[i+1:]:
                depth=c['normal']-a['normal']
                if depth>cfg.maximum_depth_zoom*zoom:break
                if depth<cfg.minimum_depth_zoom*zoom:continue
                if abs(c['source_evidence']['stroke_width_native']-a['source_evidence']['stroke_width_native'])>eps:continue
                lo=max(a['lo'],c['lo']);hi=min(a['hi'],c['hi'])
                if hi-lo<.5*depth:continue
                caps=[]
                for end in (lo,hi):
                    candidates=[l for l in cross if abs(l['normal']-end)<=eps and
                        l['lo']<=a['normal']+eps and l['hi']>=c['normal']-eps]
                    if not candidates:break
                    caps.append(candidates)
                if len(caps)!=2:continue
                points=[segment(axis,a['normal'],lo,hi),segment(axis,c['normal'],lo,hi),
                    segment(1-axis,lo,a['normal'],c['normal']),segment(1-axis,hi,a['normal'],c['normal'])]
                visibility=[ink_support(gray,p) for p in points]
                if any(v is None or v<cfg.minimum_ink for v in visibility):continue
                source=[a,c,*caps[0],*caps[1]];refs=list(dict.fromkeys(l['id'] for l in source))
                out.append(dict(id=role+':segmented-band:'+fingerprint([axis,refs])[:16],source_role=role,
                    axis=axis,lo=lo,hi=hi,n0=a['normal'],n1=c['normal'],source_refs=refs,
                    path_ids=list(dict.fromkeys(l['source_evidence']['path_id'] for l in source)),
                    source_evidence=[l['source_evidence'] for l in source],contour_ink_support=visibility,
                    band_source_kind='OBSERVED_FACES_AND_TRANSVERSE_CAPS',
                    cap_source_refs=[[l['id'] for l in cap] for cap in caps],
                    claim_scope='OBSERVED_CLOSED_BAND_CONTOUR_NOT_VERIFIED_WALL'))
    return out

def corner_gaps(bands,zoom,cfg,existing):
    """One axial band plus a positive orthogonal band can terminate an aperture.

    Both ends are witnessed by closed contours. An intervening band or nearer
    transverse band prevents jumping across another actual boundary.
    """
    out=[];eps=cfg.normal_tolerance_zoom*zoom
    for a in bands:
        candidates=[]
        for c in bands:
            if a['axis']==c['axis'] or c['lo']>a['n0']+.05*zoom or c['hi']<a['n1']-.05*zoom:continue
            intervals=[]
            if c['n0']>a['hi']+.2*zoom:intervals.append((a['hi'],c['n0']))
            if c['n1']<a['lo']-.2*zoom:intervals.append((c['n1'],a['lo']))
            for lo,hi in intervals:
                length=hi-lo;depth=a['n1']-a['n0']
                if not cfg.minimum_gap_zoom*zoom<=length<=cfg.maximum_gap_zoom*zoom or length<1.5*depth:continue
                candidates.append((lo,hi,c))
        for lo,hi,c in candidates:
            if any(o['axis']==a['axis'] and max(abs(o['n0']-a['n0']),abs(o['n1']-a['n1']))<=eps
                and min(o['hi'],hi)-max(o['lo'],lo)>.2*zoom for o in bands):continue
            if any(o['axis']!=a['axis'] and o['lo']<=a['n0']+.05*zoom and o['hi']>=a['n1']-.05*zoom
                and min(o['n1'],hi)-max(o['n0'],lo)>.2*zoom for o in bands):continue
            gap=dict(axis=a['axis'],lo=lo,hi=hi,n0=a['n0'],n1=a['n1'],flanks=[a,c],
                source_refs=list(dict.fromkeys(a['source_refs']+c['source_refs'])),flank_topology='ORTHOGONAL_TERMINATION')
            points=np.asarray([*segment(a['axis'],a['n0'],lo,hi),*segment(a['axis'],a['n1'],lo,hi)])
            gap['bbox_native']=[*points.min(0).tolist(),*points.max(0).tolist()]
            gap['id']=a['source_role']+':corner-gap:'+fingerprint([a['id'],c['id'],lo,hi])[:16]
            if not any(np.allclose(g['bbox_native'],gap['bbox_native'],atol=.05*zoom,rtol=0) for g in existing+out):out.append(gap)
    return out

def thinner_stroke_correction(gray,samples,strip_pixels,strip_ink,central_ink,axis,normal,vector,expected_width):
    """Resolve only positive adjacent thin-stroke contamination of a gap test.

    A white subpixel center alone is insufficient. Every dark strip pixel must
    be accounted for by a distinct observed, substantially thinner parallel
    stroke. Raw strip readings survive. No text, line or pixel is deleted.
    """
    if not vector or not expected_width or central_ink>.35:return None
    candidates=[l for l in vector['lines'] if l['axis']==axis and
        0<l['source_evidence'].get('stroke_width_native',0)<=.5*expected_width and
        .5*expected_width+.25<abs(l['normal']-normal)<=2.]
    if not candidates or not any(strip_ink):return None
    used={}
    for pixels in strip_pixels:
        for point in pixels:
            if gray[point[1],point[0]]>=160:continue
            explaining=[l for l in candidates if l['lo']<=point[axis]<=l['hi'] and
                abs(point[1-axis]-l['normal'])<=.5*l['source_evidence']['stroke_width_native']+.75]
            if not explaining:return None
            for l in explaining:used[l['id']]=l
    if not used:return None
    return dict(method='OBSERVED_ADJACENT_THINNER_STROKE_AND_SUBPIXEL_CENTER',
        expected_face_stroke_native=expected_width,all_dark_strip_pixels_explained=True,
        central_ink_fraction=central_ink,
        source_lines=[dict(id=l['id'],normal_native=l['normal'],
            offset_from_face_native=abs(l['normal']-normal),source_evidence=l['source_evidence']) for l in used.values()])
