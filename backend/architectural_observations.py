"""ROI adapter for existing primitive extractors. No architectural inference."""
import hashlib,json,math,os,subprocess,tempfile,time,uuid
from collections import Counter
from dataclasses import asdict
from pathlib import Path
import cv2,numpy as np,pymupdf
from backend.source_contract import request_frame
from backend.observation_audit import CoverageAudit,normalized_text
from backend.observation_trace import TraceJournal
from poc.space_grounding_geometry import (Settings,axis_segment,fit_arc,group_walls,junction_observations,
    merge_lines,pair_boundaries,pdf_to_native_matrix,raster_observations,transform_points)

ROOT=Path(__file__).resolve().parents[1]
ALGORITHM='architectural_observation_adapter/1'
MAX_PIXELS=8_000_000

def digest(data):return hashlib.sha256(data).hexdigest()
def clip_segment(points,box):
    a,b=np.asarray(points,float);d=b-a;t0,t1=0.,1.
    for p,q in [(-d[0],a[0]-box[0]),(d[0],box[2]-a[0]),(-d[1],a[1]-box[1]),(d[1],box[3]-a[1])]:
        if abs(p)<1e-12:
            if q<0:return None
        elif p<0:t0=max(t0,q/p)
        else:t1=min(t1,q/p)
        if t0>t1:return None
    return [(a+t0*d).tolist(),(a+t1*d).tolist()]
def intersects(points,box):
    if len(points)==1:return box[0]<=points[0][0]<=box[2] and box[1]<=points[0][1]<=box[3]
    return any(clip_segment([a,b],box) is not None for a,b in zip(points,points[1:]))
def offset(points,x,y):return [[float(p[0]+x),float(p[1]+y)] for p in points]
def bbox(points):
    p=np.asarray(points);return [float(p[:,0].min()),float(p[:,1].min()),float(p[:,0].max()),float(p[:,1].max())]

def validate_request(body,page_image):
    role=body.get('source_role')
    if role not in ['Ref','Target']:raise ValueError('Ref 또는 Target 역할이 필요합니다.')
    if type(body.get('page')) is not int or body['page']!=page_image.page_number:raise ValueError('선택 페이지가 다릅니다.')
    image_id=f'{page_image.source_sha256}:{page_image.page_number}:{page_image.zoom:g}:{page_image.width}x{page_image.height}'
    frame=body.get('image',{})
    expected=request_frame(page_image,role)
    if 'pdf_frame_id' not in frame:expected.pop('pdf_frame_id')
    if any(type(frame.get(k)) is bool or frame.get(k)!=v for k,v in expected.items()):raise ValueError('PDF·이미지 Frame·크기·배율·역할이 현재 도면과 다릅니다.')
    roi=body.get('roi_bbox_native')
    whole_reference=role=='Ref' and roi is None
    if whole_reference:roi=[0,0,page_image.width,page_image.height]
    if not isinstance(roi,list) or len(roi)!=4 or any(type(v) not in [int,float] or not math.isfinite(v) for v in roi):raise ValueError('ROI는 유한한 native [xmin,ymin,xmax,ymax] 좌표여야 합니다.')
    x0,y0,x1,y1=roi
    if not (0<=x0<x1<=page_image.width and 0<=y0<y1<=page_image.height):raise ValueError('ROI가 비었거나 이미지 밖으로 벗어났습니다.')
    margin=body.get('context_margin_px',0 if whole_reference else 48)
    if type(margin) is not int or not 0<=margin<=256:raise ValueError('문맥 여백은 0~256 native px로 지정하세요.')
    context=[max(0,math.floor(x0-margin)),max(0,math.floor(y0-margin)),min(page_image.width,math.ceil(x1+margin)),min(page_image.height,math.ceil(y1+margin))]
    if (context[2]-context[0])*(context[3]-context[1])>MAX_PIXELS:raise ValueError('분석 영역은 8백만 픽셀 이하로 줄이거나 추출 배율을 낮춰 주세요.')
    return {'source_role':role,'source_sha256':page_image.source_sha256,'page':page_image.page_number,'extraction_zoom':page_image.zoom,
        'image_id':image_id,'frame_id':role+':'+image_id,'pdf_frame_id':f'pdf:{page_image.source_sha256}:page:{page_image.page_number}:pymupdf_unrotated_local','width':page_image.width,'height':page_image.height,
        'image_sha256':digest(page_image.png),'core_bbox_native':list(roi),'context_bbox_native':context,'context_margin_px':margin}

def existing_ocr(crop_png):
    interpreter=ROOT/'poc/.venv/bin/python'
    if not interpreter.is_file():return {'status':'FAILED','reason':'EXISTING_OCR_ENVIRONMENT_UNAVAILABLE','regions':[]}
    required=[ROOT/'poc/models'/kind/'inference.pdiparams' for kind in ['det','rec']]
    if not all(p.is_file() for p in required):return {'status':'FAILED','reason':'EXISTING_LOCAL_OCR_WEIGHTS_UNAVAILABLE','regions':[]}
    with tempfile.TemporaryDirectory(prefix='fpue-observation-ocr-') as directory:
        folder=Path(directory);image=folder/'context.png';output=folder/'result.json';image.write_bytes(crop_png)
        env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1','PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK':'True','HF_HUB_OFFLINE':'1','TRANSFORMERS_OFFLINE':'1','CUDA_VISIBLE_DEVICES':''}
        try:
            result=subprocess.run([str(interpreter),'-B',str(ROOT/'backend/observation_ocr.py'),'--image',str(image),'--output',str(output)],cwd=ROOT,env=env,capture_output=True,timeout=100)
            if not output.is_file():return {'status':'FAILED','reason':'EXISTING_OCR_PIPELINE_FAILED','exit_code':result.returncode,'regions':[]}
            from backend.observation_ocr import recover
            return recover(output,digest(crop_png),'EXISTING_OCR_PIPELINE_FAILED' if result.returncode else None)
        except subprocess.TimeoutExpired:
            if output.is_file():
                try:
                    from backend.observation_ocr import recover
                    return recover(output,digest(crop_png),'OCR_TIMEOUT_100_SECONDS')
                except (ValueError,KeyError,json.JSONDecodeError):pass
            return {'status':'FAILED','reason':'OCR_TIMEOUT_100_SECONDS','regions':[]}
        except Exception:return {'status':'FAILED','reason':'OCR_RESULT_INVALID','regions':[]}

def analyze(pdf_bytes,image_png,binding,ocr_runner=existing_ocr):
    started=time.perf_counter();core=binding['core_bbox_native'];context=binding['context_bbox_native'];x0,y0,x1,y1=context
    image=cv2.imdecode(np.frombuffer(image_png,np.uint8),cv2.IMREAD_COLOR)
    if image is None or image.shape[:2]!=(binding['height'],binding['width']) or digest(image_png)!=binding['image_sha256']:raise ValueError('Raster frame mismatch')
    if digest(pdf_bytes)!=binding['source_sha256']:raise ValueError('Source PDF mismatch')
    crop=image[y0:y1,x0:x1].copy();crop_png=cv2.imencode('.png',crop)[1].tobytes();crop_sha=digest(crop_png);analysis_id=binding.get('analysis_run_id') or uuid.uuid4().hex
    core_window=[math.floor(core[0]),math.floor(core[1]),math.ceil(core[2]),math.ceil(core[3])]
    cx0,cy0,cx1,cy1=core_window;core_sha=digest(cv2.imencode('.png',image[cy0:cy1,cx0:cx1])[1].tobytes())
    ocr=ocr_runner(crop_png);regions=ocr.get('regions',[]);settings=Settings()
    height=float(np.median([bbox(o['polygon_native'])[3]-bbox(o['polygon_native'])[1] for o in regions])) if regions else max(8,binding['height']/100)
    raster=raster_observations(cv2.cvtColor(crop,cv2.COLOR_BGR2GRAY),[o['polygon_native'] for o in ocr.get('detection_regions',regions)],height,settings)
    observations=[];limited=Counter();counts=Counter();components={};vector_lines=[];audit=CoverageAudit(binding)
    trace=TraceJournal(binding,analysis_id,'OBSERVATION_ATTEMPT')
    trace.record(None,'RasterMaskDiagnostic','MEASURED','EXISTING_PIXEL_MASK_AUDIT_NOT_OBJECT_DELETION',
                 bounds=context,pixel_audit=raster.get('morphology_audit',{}))
    caps={'PdfText':600,'TextDetection':500,'PdfLine':4000,'PdfPath':800,'PdfRectangle':800,'PdfCurve':1000,'RasterLine':3000,'RasterContour':400,'Endpoint':2000,'ParallelBoundaries':800,'BandContact':500,'TextRegion':500}
    def add(kind,points,source,details=None,closed=False,confidence=None):
        if not points:
            trace.observation_attempt(kind,[],source,'EMPTY_GEOMETRY');return None
        bounds=bbox(points)
        context_hit=intersects(points+[points[0]] if closed else points,context)
        if closed:context_hit=not(bounds[2]<x0 or bounds[0]>x1 or bounds[3]<y0 or bounds[1]>y1)
        if not context_hit:
            trace.observation_attempt(kind,points,source,'OUTSIDE_CONTEXT');return None
        counts[kind]+=1
        if counts[kind]>caps[kind]:
            limited[kind]+=1;audit.record(kind,bounds,False)
            trace.observation_attempt(kind,points,source,'STORAGE_CAP',confidence=confidence);return None
        core_hit=intersects(points+[points[0]] if closed else points,core)
        if closed:
            b=bbox(points);core_hit=not (b[2]<core[0] or b[0]>core[2] or b[3]<core[1] or b[1]>core[3])
        outside_core=any(not(core[0]<=p[0]<=core[2] and core[1]<=p[1]<=core[3]) for p in points)
        context_extended=any(not(x0<=p[0]<=x1 and y0<=p[1]<=y1) for p in points)
        context_contact=any(min(abs(p[0]-x0),abs(p[0]-x1),abs(p[1]-y0),abs(p[1]-y1))<=2 for p in points)
        o={'observation_id':f'{binding["frame_id"]}:run:{analysis_id}:obs:{len(observations):06d}','source_role':binding['source_role'],'frame_id':binding['frame_id'],'observation_type':kind,
            'geometry_native':{'coordinate_system':'image_native_pixels','type':'point' if len(points)==1 else 'polyline','points_native':points,'trace_closed':closed},
            'source_evidence':{'source_sha256':binding['source_sha256'],'image_sha256':binding['image_sha256'],'page':binding['page'],'input_crop_sha256':crop_sha,'observation_origin_run_id':analysis_id,**source},
            'confidence':confidence,'evidence_status':'OBSERVED_UNREVIEWED','claim_status':'PROPOSED','verification_status':'PROPOSED',
            'core_roi_intersection':bool(core_hit),'core_intersection_semantics':'primitive bounding extent overlap' if closed else 'segment or point intersection','boundary_truncated':bool((core_hit and outside_core) or context_contact or context_extended),
            'boundary_details':{'crosses_core_boundary':bool(core_hit and outside_core),'context_boundary_contact':bool(context_contact),'context_geometry_extends_beyond_window':context_extended,'endpoint_semantics':'OBSERVATION_ENDPOINT_NOT_PHYSICAL_WALL_END'},
            'measurements':details or {}}
        if len(points)==2:o['core_intersection_geometry_native']=clip_segment(points,core)
        audit.record(kind,bounds,True,o['boundary_truncated']);observations.append(o)
        trace.observation_attempt(kind,points,source,'RETAINED',o['observation_id'],confidence);return o
    for d in ocr.get('detection_regions',[]):
        add('TextDetection',offset(d['polygon_native'],x0,y0),{'kind':'OCR_DETECTION','algorithm':'existing PP-OCRv5 detection',
            'raw_detection_id':d['id'],'recognition_status':d['recognition_status'],'failure_reason':d.get('failure_reason'),
            'detection_score':d.get('detection_score'),'text_region_is_not_space':True},closed=True)
    for o in regions:
        native_to_recognition=None;recognition_to_native=None
        if o.get('native_to_recognition_crop') is not None and o.get('recognition_crop_to_native') is not None:
            native_to_recognition=(np.asarray(o['native_to_recognition_crop'])@np.array([[1,0,-x0],[0,1,-y0],[0,0,1]])).tolist()
            recognition_to_native=(np.array([[1,0,x0],[0,1,y0],[0,0,1]])@np.asarray(o['recognition_crop_to_native'])).tolist()
        add('TextRegion',offset(o['polygon_native'],x0,y0),{'kind':'OCR','algorithm':'poc.run.ocr existing inference with optional progress hooks','models':ocr.get('models'),
            'normalized_text':o.get('normalized_text',normalized_text(o['text'])),'raw_recognition':o.get('raw_recognition'),
            'crop_transform_status':o.get('crop_transform_status','NOT_RECORDED'),'recognition_crop_size':o.get('recognition_crop_size'),'recognition_crop_sha256':o.get('recognition_crop_sha256'),
            'native_to_recognition_crop':native_to_recognition,'recognition_crop_to_native':recognition_to_native,
            'crop_to_native':[[1,0,x0],[0,1,y0],[0,0,1]],'context_to_recognition_crop':o.get('native_to_recognition_crop'),
            'recognition_crop_to_context':o.get('recognition_crop_to_native'),
            'raw_detection_id':o['id'],'raw_crop_polygon':o['polygon_native'],'text':o['text'],'recognition_score':o['recognition_score'],
            'crop_native_to_rectified':o['native_to_rectified_crop'],'crop_rotated_ccw_90':o['crop_rotated_ccw_90'],'semantics':'Text extent is not a room boundary'},
            {'text':o['text'],'score_semantics':'uncalibrated recognition confidence, not correctness'},True,float(o['recognition_score']))
    components['OCR']={k:v for k,v in ocr.items() if k not in ['regions','raw_detection']};components['OCR']['regions']=len(regions)
    components['OCR']['raw_detection']=ocr.get('raw_detection',{})
    frame={}
    try:
        with pymupdf.open(stream=pdf_bytes,filetype='pdf') as document:
            page=document[binding['page']-1];pix=page.get_pixmap(matrix=pymupdf.Matrix(binding['extraction_zoom'],binding['extraction_zoom']),colorspace=pymupdf.csRGB,alpha=False)
            if digest(pix.tobytes('png'))!=binding['image_sha256']:raise ValueError('Vector/raster rerender mismatch')
            matrix=pdf_to_native_matrix(page,binding['extraction_zoom'],(pix.x,pix.y));inverse=np.linalg.inv(matrix);u=page.transformation_matrix
            user_to_local=[[u.a,u.c,u.e],[u.b,u.d,u.f],[0,0,1]]
            frame={'pdf_local_to_native':matrix,'native_to_pdf_local':inverse.tolist(),'pdf_user_to_local':user_to_local,
                'pdf_user_to_native':(np.asarray(matrix)@np.asarray(user_to_local)).tolist(),'native_to_pdf_user':np.linalg.inv(np.asarray(matrix)@np.asarray(user_to_local)).tolist(),'pdf_local_frame_id':binding['pdf_frame_id'],'native_frame_id':binding['frame_id'],'rotation':page.rotation,'cropbox':list(page.cropbox),'mediabox':list(page.mediabox),
                'pixmap_origin':[pix.x,pix.y],'rerender_image_sha256_matches':True,'geometry_validation_is_not_architectural_verification':True}
            raw=page.get_drawings()
            for pi,path in enumerate(raw):
                path_id=f'pdf_path:{pi}';items=path['items'];style={'pdf_coordinate_system':'pymupdf_unrotated_local','path_id':path_id,'pdf_path_index':pi,'path_type':path['type'],'dash':path.get('dashes'),'stroke_width_pdf':path.get('width'),'layer':path.get('layer','')}
                r=path['rect'];bounds=transform_points([[r.x0,r.y0],[r.x1,r.y0],[r.x1,r.y1],[r.x0,r.y1]],matrix)
                if not intersects(bounds+[bounds[0]],context) and not (r.width==0 or r.height==0):
                    b=bbox(bounds)
                    if b[0]>x1 or b[2]<x0 or b[1]>y1 or b[3]<y0:continue
                payload=[]
                for ii,item in enumerate(items):
                    kind=item[0];local=[];edges=[]
                    if kind=='l':local=[list(item[1]),list(item[2])];edges=[local]
                    elif kind=='re':
                        q=item[1];local=[list(q.tl),list(q.tr),list(q.br),list(q.bl)];edges=[[local[j],local[(j+1)%4]] for j in range(4)]
                        add('PdfRectangle',transform_points(local,matrix),{'kind':'PDF_VECTOR','algorithm':'PyMuPDF rectangle primitive','source_primitive_id':f'{path_id}:rectangle:{ii}','pdf_points_local':local,**style},closed=True)
                    elif kind=='qu':
                        q=item[1];local=[list(q.ul),list(q.ur),list(q.lr),list(q.ll)];edges=[[local[j],local[(j+1)%4]] for j in range(4)]
                        add('PdfRectangle',transform_points(local,matrix),{'kind':'PDF_VECTOR','algorithm':'PyMuPDF quad primitive','pdf_points_local':local,**style},closed=True)
                    elif kind=='c':
                        local=[list(p) for p in item[1:]];control=transform_points(local,matrix);curve=fit_arc(control)
                        add('PdfCurve',curve['polyline'],{'kind':'PDF_VECTOR','algorithm':'existing fit_arc sampled Bezier; not guaranteed circular','source_primitive_id':f'{path_id}:curve:{ii}','pdf_control_points_local':local,'control_points_native':control,**style},curve)
                    payload.append({'item_index':ii,'type':kind,'points_pdf_local':local})
                    for ei,edge in enumerate(edges):
                        full=transform_points(edge,matrix);clipped=clip_segment(full,context)
                        if clipped is None:continue
                        s={**style,'id':f'{path_id}:{ii}:{ei}','points':offset(clipped,-x0,-y0),'stroke_px':float(path.get('width') or 0)*float(np.linalg.norm(np.asarray(matrix)[:2,0]))}
                        vector_lines.append(s);orientation=axis_segment(full,settings.axial_angle_degrees)
                        add('PdfLine',clipped,{'kind':'PDF_VECTOR','algorithm':'existing PDF item edge extraction','source_primitive_id':s['id'],'pdf_points_local':edge,'native_unclipped_points':full,'context_clipped':clipped!=full,**style},
                            {'stroke_width_px':s['stroke_px'],'stroke_width_is_not_wall_thickness':True,'orientation':orientation[0] if orientation else 'DIAGONAL'})
                add('PdfPath',bounds,{'kind':'PDF_VECTOR','algorithm':'PyMuPDF path primitive bounds','pdf_items':payload,**style},{'geometry_semantics':'path extent, not a space or wall polygon'},True)
            spans=0
            for block in page.get_text('dict',flags=pymupdf.TEXTFLAGS_DICT & ~pymupdf.TEXT_PRESERVE_IMAGES)['blocks']:
                for line in block.get('lines',[]):
                    for span in line.get('spans',[]):
                        text=span.get('text','');rect=pymupdf.Rect(span['bbox'])
                        if not text.strip():continue
                        o=add('PdfText',transform_points([list(rect.tl),list(rect.tr),list(rect.br),list(rect.bl)],matrix),
                            {'kind':'PDF_TEXT','algorithm':'PyMuPDF text span','text':text,'normalized_text':normalized_text(text),
                            'font':span.get('font'),'pdf_text_size':span.get('size'),'pdf_bbox_local':list(rect),
                            'pdf_text_is_not_verified_OCR_or_space':True},closed=True)
                        if o:spans+=1
            components['PDF_TEXT']={'status':'PARTIAL' if limited.get('PdfText') else 'COMPLETED' if spans else 'UNAVAILABLE','stored_spans':spans}
            components['PDF_VECTOR']={'status':'COMPLETED' if raw else 'UNAVAILABLE','path_count':len(raw),'reason':None if raw else 'NO_EXTRACTABLE_VECTOR_PATHS_RASTER_FALLBACK_USED'}
    except Exception as error:components['PDF_VECTOR']={'status':'FAILED','reason':'PDF_VECTOR_EXTRACTION_FAILED','failure_type':type(error).__name__}
    for s in raster['segments']:
        pts=offset(s['points'],x0,y0);orientation=axis_segment(s['points'],settings.axial_angle_degrees)
        o=add('RasterLine',pts,{'kind':'RASTER','algorithm':'existing OpenCV LSD_REFINE_STD','source_primitive_id':s['id'],'crop_points':s['points']},
            {'detector_support_width_px':s['stroke_px'],'support_width_is_not_wall_thickness':True,'orientation':orientation[0] if orientation else 'DIAGONAL'})
        if o:
            for i,p in enumerate(pts):add('Endpoint',[p],{'kind':'RASTER_DERIVED','algorithm':'LSD segment endpoint','line_observation_id':o['observation_id'],'endpoint_index':i})
    # Existing OpenCV contour tracing, without arc/space classification or closure.
    contours,_=cv2.findContours(raster['visible_ink'],cv2.RETR_LIST,cv2.CHAIN_APPROX_SIMPLE)
    for i,c in enumerate(contours):
        p=c.reshape(-1,2)
        if len(p)<6:continue
        if len(p)>512:p=p[::math.ceil(len(p)/512)]
        add('RasterContour',offset(p.tolist(),x0,y0),{'kind':'RASTER','algorithm':'OpenCV visible-ink contour trace','source_contour_index':i,'is_analytic_arc':False,'is_space_boundary':False},{'trace_semantics':'stroke contour; curves, glyphs and hatches are not classified'},True)
    pair_audit={};all_pairs=[]
    for source,segments in [('pdf',vector_lines),('raster',raster['segments'])]:
        lines,rejections=merge_lines(segments,raster,height,settings,source);pairs,rejected=pair_boundaries(lines,raster,height,settings);all_pairs.extend(pairs)
        pair_audit[source]={'merged_count':len(lines),'rejections':rejections['rejected_counts'],'pair_rejections':rejected['rejected_counts']}
        line_evidence={line['id']:line for line in lines}
        for i,p in enumerate(pairs):
            poly=offset(p['polygon'],x0,y0);pair_observation=add('ParallelBoundaries',poly,{'kind':source.upper()+'_DERIVED','algorithm':'existing merge_lines + pair_boundaries','source_boundary_ids':p['boundary_ids'],'boundary_source_evidence':[line_evidence[k] for k in p['boundary_ids']],'boundaries_native':[offset(b,x0,y0) for b in p['boundaries']]},
                {'separation_px':p['thickness_px'],'separation_is_not_verified_wall_thickness':True,'boundary_stroke_px':p['boundary_stroke_px'],'raster_support':p['boundary_support']},True)
            p['_observation_id']=pair_observation['observation_id'] if pair_observation else None
    # Band grouping is used only to report geometric contacts, not Wall entities.
    groups=group_walls(all_pairs,binding['source_role'],height)
    group_evidence={g['id']:[p['_observation_id'] for p in g['pieces'] if p.get('_observation_id')] for g in groups}
    for j in junction_observations(groups,binding['source_role']):
        add('BandContact',offset([j['point']],x0,y0),{'kind':'DERIVED','algorithm':'existing junction_observations','parallel_observation_ids':[p for identity in j['wall_ids'] for p in group_evidence[identity]],'semantics':'paired-band proximity/intersection, not a verified architectural joint'})
    components['RASTER']={'status':'PARTIAL' if limited else 'COMPLETED','raw_lsd_count':len(raster['segments']),'contour_count':len(contours),'morphology_audit':raster['morphology_audit'],'pair_audit':pair_audit,
        'curve_semantics':'Raster contours are raw stroke traces. Circular arc identity is not verified.'}
    if any(k.startswith('Pdf') for k in limited) and components['PDF_VECTOR']['status']=='COMPLETED':components['PDF_VECTOR']['status']='PARTIAL'
    if limited.get('TextRegion') and components['OCR']['status']=='COMPLETED':components['OCR']['status']='PARTIAL'
    statuses=[components['PDF_VECTOR']['status'],components['OCR']['status']]
    return {'schema_version':'architectural_observation_bundle/1','run_id':analysis_id,'bundle_kind':'OBSERVATION_ONLY','source_role':binding['source_role'],'frame_id':binding['frame_id'],'source_binding':binding,
        'algorithm':ALGORITHM,'reused_geometry_settings':asdict(settings),'status':'PARTIAL' if any(v in ['FAILED','PARTIAL'] for v in statuses) or limited else 'COMPLETED',
        'analysis_scope':{'core_roi':{'bbox_native':core,'coordinate_system':'image_native_pixels','raster_window_native':core_window,'raster_crop_sha256':core_sha},'context':{'bbox_native':context,'requested_margin_px':binding['context_margin_px'],'kind':'CONTEXT_NOT_CORE_ROI'},
            'crop_to_native':[[1,0,x0],[0,1,y0],[0,0,1]],'native_to_crop':[[1,0,-x0],[0,1,-y0],[0,0,1]],'input_crop_sha256':crop_sha,'crop_size':[x1-x0,y1-y0]},
        'frame_registry':frame,'components':components,'observations':observations,'entities':[],'claims':[],
        'producer_traces':[trace.finish()],
        'coverage':{'spatial_audit':audit.export(),'observation_counts':dict(Counter(o['observation_type'] for o in observations)),'core_intersecting_count':sum(o['core_roi_intersection'] for o in observations),'omitted_due_to_limits':dict(limited),'caps_per_type':caps,
            'junction_scope':'H/V parallel-band contacts only; arbitrary diagonal intersections not inferred'},
        'policy':{'architectural_objects_verified':False,'text_box_is_not_space':True,'ROI_cut_is_not_physical_endpoint':True,'Ref_mapping_performed':False,'VLM_run':False,'change_detection_run':False},
        'runtime_seconds':time.perf_counter()-started}
