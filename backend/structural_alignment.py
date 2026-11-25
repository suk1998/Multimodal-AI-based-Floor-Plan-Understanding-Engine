"""Comparison preparation only. Transform direction: Target native -> Ref native.

Automatic long/thick ink is a proposal, never a semantic wall classifier.
No input raster is modified. No change detector, OCR or identity fallback.
"""
import hashlib
import json
import math

import cv2
import numpy as np

ENGINE = "phase2b-similarity-v1"


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def image_binding(image):
    return {"source_sha256": image.source_sha256, "page": image.page_number,
            "extraction_zoom": image.zoom, "width": image.width, "height": image.height,
            "image_id": f"{image.source_sha256}:{image.page_number}:{image.zoom:g}:{image.width}x{image.height}"}


def validate_roi(roi, image):
    if (type(roi.get("schema_version")) is not int or roi.get("schema_version") != 1 or roi.get("role") != "Target" or
            roi.get("coordinate_system") != "image_native_pixels" or roi.get("image") != image_binding(image)):
        raise ValueError("Target ROI의 PDF·페이지·배율·원본 크기·좌표계가 현재 도면과 다릅니다.")
    vertices=roi.get("vertices")
    if (not isinstance(vertices,list) or not 3<=len(vertices)<=512 or
            any(not isinstance(point,list) or len(point)!=2 or
                any(type(value) not in (int,float) for value in point) for point in vertices)):
        raise ValueError("ROI 좌표는 숫자 [x,y] 배열이어야 합니다. 문자열·불리언 좌표는 허용하지 않습니다.")
    p = np.asarray(vertices, dtype=float)
    if (p.ndim != 2 or p.shape[1] != 2 or not 3 <= len(p) <= 512 or not np.isfinite(p).all() or
            (p < 0).any() or (p > [image.width, image.height]).any()):
        raise ValueError("ROI는 원본 이미지 안의 유한한 좌표 3~512개가 필요합니다.")
    def cross(a,b,c):
        return float((b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0]))
    def on(a,b,c):
        return cross(a,b,c)==0 and np.all(c>=np.minimum(a,b)) and np.all(c<=np.maximum(a,b))
    for i,a in enumerate(p):
        b,c=p[(i+1)%len(p)],p[(i+2)%len(p)]
        if np.array_equal(a,b) or (cross(a,b,c)==0 and np.dot(b-a,c-b)<=0):
            raise ValueError("ROI에 중복 또는 되돌아가는 변이 있습니다.")
        for j in range(i+1,len(p)):
            if j==i+1 or (i==0 and j==len(p)-1):continue
            c,d=p[j],p[(j+1)%len(p)]
            x,y,z,w=cross(a,b,c),cross(a,b,d),cross(c,d,a),cross(c,d,b)
            if (x*y<0 and z*w<0) or on(a,b,c) or on(a,b,d) or on(c,d,a) or on(c,d,b):
                raise ValueError("ROI가 자기 교차합니다.")
    if abs(cv2.contourArea(p.astype(np.float32)))<1e-8:
        raise ValueError("ROI 면적이 0입니다.")
    return p


def polygon_mask(points, shape):
    # Subpixel polygon rasterization. Geometry itself is never rounded in JSON.
    mask=np.zeros(shape,np.uint8)
    cv2.fillPoly(mask,[np.round(np.asarray(points)*256).astype(np.int32)],255,shift=8)
    return mask


def structural_mask(gray):
    h,w=gray.shape
    ink=(gray<160).astype(np.uint8)*255
    width=max(1.1,1.4*w/2382)
    core=(cv2.distanceTransform(ink,cv2.DIST_L2,5)>=width).astype(np.uint8)*255
    length=max(15,round(35*w/2382))
    bands=cv2.morphologyEx(core,cv2.MORPH_OPEN,np.ones((1,length),np.uint8)) | cv2.morphologyEx(core,cv2.MORPH_OPEN,np.ones((length,1),np.uint8))
    radius=max(2,round(5*w/2382))
    bands=cv2.dilate(bands,np.ones((radius*2+1,radius*2+1),np.uint8))
    n,labels,stats,_=cv2.connectedComponentsWithStats(bands)
    keep=np.zeros(n,np.uint8)
    for i,(x,y,bw,bh,area) in enumerate(stats[1:],1):
        if bw<w*.8 and bh<h*.8 and x>0 and y>0 and x+bw<w and y+bh<h and area>=length*2:keep[i]=255
    return keep[labels]


def transform(points, matrix):
    return np.asarray(points,dtype=float) @ np.asarray(matrix)[:2,:2].T + np.asarray(matrix)[:2,2]


def automatic_anchors(ref, target, target_roi):
    masks=[structural_mask(ref),structural_mask(target)]
    masks[1]=cv2.bitwise_and(masks[1],polygon_mask(target_roi,target.shape))
    orb=cv2.SIFT_create(nfeatures=5000)
    (kr,dr),(kt,dt)=[orb.detectAndCompute(im,mask) for im,mask in zip([ref,target],masks)]
    if dr is None or dt is None:return [],masks
    matcher=cv2.BFMatcher(cv2.NORM_L2)
    reverse={m.queryIdx:m.trainIdx for m in matcher.match(dr,dt)}
    pairs=[]
    for group in matcher.knnMatch(dt,dr,k=2):
        if len(group)!=2:continue
        a,b=group
        if a.distance<.7*b.distance and reverse.get(a.trainIdx)==a.queryIdx:
            pairs.append((a.distance,kt[a.queryIdx].pt,kr[a.trainIdx].pt))
    # Entire spatial tiles, not a random split of neighbouring keypoints.
    tile=max(45,round(120*target.shape[1]/2382));seen=set();anchors=[]
    for distance,t,r in sorted(pairs):
        cell=(int(t[0]//tile),int(t[1]//tile))
        if cell in seen:continue
        seen.add(cell)
        anchors.append({"id":f"auto-{len(anchors)+1}","target":list(t),"ref":list(r),
                        "role":"fit" if sum(cell)%2==0 else "check","confirmed":False,
                        "reason":"긴 두꺼운 잉크 후보. 문자·치수·가구·불변 여부는 사용자 확인 필요.",
                        "descriptor_distance":float(distance),"spatial_cell":list(cell)})
        if len(anchors)>=100:break
    # Keep check patches separate from fitting patches.
    fit=[np.array(a["target"]) for a in anchors if a["role"]=="fit"]
    anchors=[a for a in anchors if a["role"]=="fit" or not fit or min(np.linalg.norm(np.array(a["target"])-p) for p in fit)>=max(18,30*target.shape[1]/2382)]
    return anchors,masks


def stats(values):
    if not len(values):return {"count":0,"rmse":None,"median":None,"p95":None,"max":None}
    v=np.asarray(values)
    return {"count":len(v),"rmse":float(np.sqrt(np.mean(v*v))),"median":float(np.median(v)),"p95":float(np.percentile(v,95)),"max":float(v.max())}


def encode(image):
    return cv2.imencode(".png",image)[1].tobytes()


def align_pages(ref_page,target_page,roi,mode="auto",anchors=None,tolerance=3.0):
    for page in [ref_page,target_page]:
        if page.width*page.height>12_000_000:raise ValueError("정렬은 이미지당 1,200만 픽셀 이하로 추출하세요.")
    polygon=validate_roi(roi,target_page)
    ref,target=[cv2.imdecode(np.frombuffer(p.png,np.uint8),cv2.IMREAD_GRAYSCALE) for p in [ref_page,target_page]]
    if mode=="auto":anchors,masks=automatic_anchors(ref,target,polygon)
    else:masks=[structural_mask(im) for im in [ref,target]]
    anchors=[dict(a) for a in anchors or []]
    result={"engine":ENGINE,"direction":"target_native_to_ref_native","coordinate_system":"image_native_pixels",
            "ref_render_sha256":hashlib.sha256(ref_page.png).hexdigest(),"target_render_sha256":hashlib.sha256(target_page.png).hexdigest(),
            "ref_image":image_binding(ref_page),"target_image":image_binding(target_page),"target_roi":roi,
            "roi_fingerprint":fingerprint(roi),"method":"SIFT_structural_candidates_similarity_RANSAC" if mode=="auto" else "user_correspondences_similarity_RANSAC",
            "status":"FAILED","matrix":None,"inverse_matrix":None,"mapping":None,"anchors":anchors,
            "reasons":[],"review":{"state":"NOT_APPROVED"},"approval_eligible":False,
            "settings":{"ref_tolerance_px":tolerance,"max_pixels_per_image":12_000_000,"minimum_fit":3,"minimum_check":3,
                        "runtime":{"opencv":cv2.__version__,"numpy":np.__version__},
                        "automatic":{"feature":"SIFT","max_features":5000,"ratio_test":.7,"ink_threshold":160,"core_radius_formula":"max(1.1,1.4*width/2382)","long_run_formula":"max(15,round(35*width/2382))","max_component_span_fraction":.8,"spatial_tile_formula":"max(45,round(120*target_width/2382))","target_holdout_gap_formula":"max(18,30*target_width/2382)"},
                        "auto_candidates_are_unconfirmed":True,"support_hull_is_not_dense_validation":True}}
    previews={}
    # Record actual selected positions, not just a global score.
    for side,im,mask in zip(["ref","target"],[ref,target],masks):
        canvas=cv2.cvtColor(im,cv2.COLOR_GRAY2BGR)
        canvas[mask>0]=(canvas[mask>0]*.6+np.array([0,140,0])*.4).astype(np.uint8)
        for a in anchors:
            point=np.asarray(a[side],dtype=float)
            if not np.isfinite(point).all() or not (0<=point[0]<im.shape[1] and 0<=point[1]<im.shape[0]):
                raise ValueError("대응점은 각 원본 이미지 안에 있어야 합니다.")
            x,y=np.round(point).astype(int);patch=mask[max(0,y-10):y+11,max(0,x-10):x+11]
            a[f"{side}_structural_support"]=bool(np.count_nonzero(patch))
            a[f"{side}_ink_support"]=bool(np.count_nonzero(im[max(0,y-5):y+6,max(0,x-5):x+6]<180)>=8)
            cv2.circle(canvas,(x,y),6,(0,120,255) if a["role"]=="fit" else (220,70,160),2)
            cv2.putText(canvas,str(a["id"]),(x+7,y-7),cv2.FONT_HERSHEY_SIMPLEX,.35,(100,0,100),1)
        previews[f"anchors_{side}"]=encode(canvas)
    fit=[a for a in anchors if a["role"]=="fit"]
    checks=[a for a in anchors if a["role"]=="check"]
    if len(fit)<3:
        result["reasons"].append("독립된 안정 구조 추정점이 3개 이상 필요합니다. 수동 대응점을 지정하세요.")
        return result,previews
    src=np.array([a["target"] for a in fit],np.float32);dst=np.array([a["ref"] for a in fit],np.float32)
    if np.linalg.matrix_rank(src-src.mean(0),tol=1e-3)<2:
        result["reasons"].append("추정점이 한 직선 또는 한 점에 집중돼 있습니다.");return result,previews
    cv2.setRNGSeed(7)
    affine,inliers=cv2.estimateAffinePartial2D(src.copy(),dst.copy(),method=cv2.RANSAC,ransacReprojThreshold=tolerance,maxIters=5000,confidence=.995,refineIters=20)
    if affine is None or not np.isfinite(affine).all():
        result["reasons"].append("변환 추정 실패. Identity를 대체 결과로 사용하지 않습니다.");return result,previews
    matrix=np.vstack([affine,[0,0,1]]);scale=float(np.hypot(affine[0,0],affine[1,0]))
    if not .05<=scale<=20 or np.count_nonzero(inliers)<3:
        result["reasons"].append("변환 배율 또는 유효 추정점이 부적절합니다.");return result,previews
    inverse=np.linalg.inv(matrix)
    result["transform_parameters"]={"scale_target_to_ref":scale,"rotation_degrees":math.degrees(math.atan2(affine[1,0],affine[0,0])),"translation_ref_px":affine[:,2].tolist()}
    for a in anchors:
        a["residual_ref_px"]=float(np.linalg.norm(transform([a["target"]],matrix)[0]-a["ref"]))
        a["residual_target_px"]=a["residual_ref_px"]/scale
        a["used_for_fit"]=a in fit and bool(inliers[fit.index(a),0])
    result["residuals"]={"fit_ref_px":stats([a["residual_ref_px"] for a in anchors if a["used_for_fit"]]),
                         "check_ref_px":stats([a["residual_ref_px"] for a in checks]),
                         "check_target_px":stats([a["residual_target_px"] for a in checks])}
    # No holdout can be an exact/sufficiently close repeat of a fitting patch.
    used=[a for a in anchors if a["used_for_fit"]]
    separated=all(min(np.linalg.norm(np.array(a["target"])-b["target"]) for b in used)>=max(12,20*target_page.zoom/2) and min(np.linalg.norm(np.array(a["ref"])-b["ref"]) for b in used)>=max(3,20*ref_page.zoom/2) for a in checks) if checks else False
    supported=all((a["ref_structural_support"] and a["target_structural_support"]) or (mode=="manual" and a.get("confirmed") and a.get("reason","").strip() and a["ref_ink_support"] and a["target_ink_support"]) for a in checks)
    check_p95=result["residuals"]["check_ref_px"]["p95"]
    if checks and check_p95>tolerance:
        result["rejected_matrix"]=matrix.tolist();result["reasons"].append("미사용 검증 구간의 잔차가 허용치를 넘었습니다. 매핑을 승인할 수 없습니다.");return result,previews
    tmask=polygon_mask(polygon,target.shape)
    valid=np.zeros(target.shape,np.uint8);xs=np.arange(target.shape[1],dtype=float)
    for y in range(target.shape[0]):
        rx=matrix[0,0]*xs+matrix[0,1]*y+matrix[0,2];ry=matrix[1,0]*xs+matrix[1,1]*y+matrix[1,2]
        valid[y]=((rx>=0)&(rx<=ref.shape[1]-1)&(ry>=0)&(ry<=ref.shape[0]-1)).astype(np.uint8)*255
    check_points=np.array([a["target"] for a in checks],np.float32)
    hull=cv2.convexHull(check_points).reshape(-1,2) if len(checks)>=3 else np.empty((0,2))
    hullmask=polygon_mask(hull,target.shape) if len(hull)>=3 else np.zeros_like(tmask)
    total=int(np.count_nonzero(tmask));inside=int(np.count_nonzero(tmask & valid));hull_inside=int(np.count_nonzero(tmask & valid & hullmask))
    tile=max(1,target_page.width/4)
    cells={ (int(a["target"][0]//tile),int(a["target"][1]//tile)) for a in checks }
    numerical=len(checks)>=3 and separated and supported and len(cells)>=2 and check_p95<=tolerance
    result["approval_eligible"]=bool(numerical)
    confirmed=all(a.get("confirmed") and a.get("reason","").strip() for a in used+checks)
    coverage=hull_inside/total if total else 0
    result["status"]="VERIFIED_SAMPLED" if numerical and confirmed and coverage>=.85 and inside==total else "PARTIAL"
    result["matrix"],result["inverse_matrix"]=matrix.tolist(),inverse.tolist()
    mapped=transform(polygon,matrix)
    result["mapping"]={"ref_vertices_unclipped":mapped.tolist(),"target_vertices":polygon.tolist(),
                       "target_roi_pixels":total,"valid_roi_pixels":inside,"outside_ref_pixels":total-inside,
                       "valid_fraction":inside/total if total else 0,"check_support_hull_target":hull.tolist(),
                       "sampled_hull_roi_fraction":coverage,"hull_is_dense_validation":False,
                       "outside_ref_vertex_indices":[i for i,p in enumerate(mapped) if not (0<=p[0]<=ref.shape[1] and 0<=p[1]<=ref.shape[0])]}
    result["verification"]={"independent_check_points":len(checks),"check_spatial_cells":len(cells),"fit_check_patches_separated":bool(separated),"check_structural_ink_supported":bool(supported),"structures_user_confirmed":confirmed,"numerical_checks_passed":bool(numerical)}
    if not numerical:result["reasons"].append("독립 검증점 수·공간 분포·구조 지지 근거가 부족합니다. 수동 검증점을 보완하세요.")
    if not confirmed:result["reasons"].append("자동 후보는 불변 구조로 확정되지 않았습니다. 근거 확인과 사용자 승인이 필요합니다.")
    if coverage<.85:result["reasons"].append("검증점의 공간 범위가 ROI 전체에 미치지 못합니다. Hull 내부도 픽셀별 검증 완료를 의미하지 않습니다.")
    if inside<total:result["reasons"].append("Ref 이미지 밖으로 매핑되는 Target ROI 영역이 있습니다.")
    warped=cv2.warpAffine(ref,affine,(target.shape[1],target.shape[0]),flags=cv2.INTER_LINEAR|cv2.WARP_INVERSE_MAP,borderValue=255)
    # Ref red, Target cyan: alignment preview only, never change labels.
    overlay=np.stack([warped,warped,target],axis=-1)
    overlay[(tmask>0)&(valid==0)]=[190,190,190]
    cv2.polylines(overlay,[np.round(polygon).astype(np.int32)],True,(0,160,0),2)
    previews["overlay"]=encode(overlay);previews["warped_ref"]=encode(warped)
    coverage_im=cv2.cvtColor(target,cv2.COLOR_GRAY2BGR);coverage_im[(tmask>0)&(valid==0)]=[120,120,120]
    coverage_im[(tmask>0)&(valid>0)&(hullmask==0)]=(coverage_im[(tmask>0)&(valid>0)&(hullmask==0)]*.65+np.array([80,180,240])*.35).astype(np.uint8)
    cv2.polylines(coverage_im,[np.round(polygon).astype(np.int32)],True,(0,150,0),2);previews["coverage"]=encode(coverage_im)
    return result,previews
