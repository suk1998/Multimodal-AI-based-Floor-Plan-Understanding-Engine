"""Registered marker geometry shared by matching and scoped text decisions.

Same printed-marker locus is not physical Door/Window identity. OCR provenance,
complete type reading, original perimeter ink and reciprocal uniqueness remain
separate necessary checks before a printed change can be confirmed.
"""
import cv2
import numpy as np
from backend.structural_alignment import transform

VERSION='registered_marker_locus/1'
MINIMUM_OUTLINE_IOU=.90

def measure_marker_locus(ref_bbox,target_bbox,scope,zoom,normal_tolerance):
    result=dict(policy_version=VERSION,supported=False,
                claim_scope='REGISTERED_PRINTED_MARKER_LOCUS_NOT_INSTALLATION_IDENTITY')
    if scope.matrix is None:
        return {**result,'reason':'NO_VERIFIED_REGISTRATION'}
    boxes=[np.asarray(b,float) for b in [ref_bbox,target_bbox]]
    if any(b.shape!=(4,) or not np.isfinite(b).all() or b[0]>=b[2] or b[1]>=b[3] for b in boxes):
        return {**result,'reason':'INVALID_MARKER_NATIVE_BOUNDS'}
    polygons=[[[x,y],[u,y],[u,v],[x,v]] for x,y,u,v in boxes]
    if not scope.contains([{'paths':[polygons[0]]}],[{'paths':[polygons[1]]}]):
        return {**result,'reason':'OUTSIDE_ACCEPTED_COMPARISON_SCOPE'}
    ref_native=np.asarray(polygons[0],float);mapped_native=transform(polygons[1],scope.matrix)
    ref=ref_native.astype(np.float32);mapped=mapped_native.astype(np.float32)
    intersection,_=cv2.intersectConvexConvex(ref,mapped)
    union=cv2.contourArea(ref)+cv2.contourArea(mapped)-intersection
    iou=float(intersection/max(union,1e-6))
    distance=float(np.linalg.norm(ref_native.mean(0)-mapped_native.mean(0)))
    tolerance=min(2*zoom,normal_tolerance)
    supported=iou>=MINIMUM_OUTLINE_IOU and distance<=tolerance
    return {**result,'supported':supported,'marker_outline_iou':iou,
            'center_distance_ref_px':distance,'center_tolerance_ref_px':tolerance,
            'reason':'REGISTERED_MARKER_GEOMETRY_SUPPORTED' if supported else 'MARKER_LOCUS_OR_OUTLINE_UNSUPPORTED'}
