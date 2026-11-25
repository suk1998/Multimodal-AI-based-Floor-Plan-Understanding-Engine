"""Public PDF workflow: reuse the existing API in isolated anonymous workspaces.

No accounts, authentication, fixed drawings or persistent GT. Analysis is on demand.
"""
import os,re,secrets,time,json,math
from dataclasses import dataclass, field
from pathlib import Path
from fastapi import FastAPI,HTTPException,Request
from fastapi.responses import FileResponse,JSONResponse,Response,StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware
from backend.app import DocumentStore,create_app as create_pdf_app
from backend.architectural_observations import validate_request
from backend.comparison_review import ComparisonReview, export_review
from backend.observation_jobs import ObservationJobs
from backend.upstream_lineage import public_comparison,select_records,jsonl_chunks
from phase1_pdf import PDFInputError

ROOT=Path(__file__).resolve().parents[1]
COOKIE='fpue_pdf_workspace'
TTL=2*60*60

@dataclass
class Workspace:
    api:FastAPI
    touched:float
    comparisons: dict[str, ComparisonReview] = field(default_factory=dict)

class Workspaces:
    def __init__(self,maximum=8,ttl=TTL,clock=time.monotonic):
        self.items={};self.maximum=maximum;self.ttl=ttl;self.clock=clock
    def expire(self):
        now=self.clock()
        for token,w in list(self.items.items()):
            if now-w.touched>=self.ttl:del self.items[token]
    def get(self,token):
        self.expire();w=self.items.get(token)
        if w:w.touched=self.clock()
        return w
    def create(self):
        self.expire()
        if len(self.items)>=self.maximum:raise HTTPException(503,'동시 작업공간 한도에 도달했습니다. 잠시 후 다시 시도하세요.')
        token=secrets.token_urlsafe(32)
        w=Workspace(create_pdf_app(DocumentStore(max_bytes=100*1024*1024,max_documents=8)),self.clock());self.items[token]=w
        return token,w

class ExistingPDFResponse(Response):
    def __init__(self,api):super().__init__();self.api=api
    async def __call__(self,scope,receive,send):await self.api(scope,receive,send)

def create_app(*,static_dir=None,workspaces=None,observation_jobs=None):
    dist=Path(static_dir or ROOT/'frontend/dist');spaces=workspaces or Workspaces()
    app=FastAPI(title='FPUE PDF Comparison',docs_url=None,redoc_url=None,openapi_url=None);app.state.workspaces=spaces
    jobs=observation_jobs or ObservationJobs();app.state.observation_jobs=jobs
    hosts=['127.0.0.1','localhost','testserver','*.trycloudflare.com']
    custom=os.getenv('FPUE_APPROVED_PUBLIC_HOST','')
    if custom:
        if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9.-]*',custom):raise ValueError('Invalid approved hostname')
        hosts.append(custom)
    app.add_middleware(TrustedHostMiddleware,allowed_hosts=hosts)

    @app.middleware('http')
    async def boundaries(request:Request,call_next):
        response=None
        if request.method not in ['GET','HEAD']:
            expected=f'{request.url.scheme}://{request.url.netloc}'
            if request.headers.get('origin')!=expected:response=JSONResponse({'detail':'이 도면 비교 화면에서 시작한 요청만 허용합니다. 페이지를 새로 열어 주세요.'},status_code=403)
        if response is None and request.method=='POST':
            length=request.headers.get('content-length')
            if length and length.isdecimal() and int(length)>51*1024*1024:response=JSONResponse({'detail':'PDF는 50MiB 이하로 업로드하세요.'},status_code=413)
        if response is None:response=await call_next(request)
        token=request.cookies.get(COOKIE)
        if request.url.path.startswith('/api/documents') and response.status_code<400 and token in spaces.items:
            response.set_cookie(COOKIE,token,max_age=spaces.ttl,httponly=True,secure=request.url.scheme=='https',samesite='strict',path='/')
        response.headers.update({'X-Content-Type-Options':'nosniff','Referrer-Policy':'no-referrer',
            'Content-Security-Policy':"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' blob: data:; connect-src 'self'; frame-src 'none'; frame-ancestors 'none'; object-src 'none'; base-uri 'self'",
            'Cross-Origin-Resource-Policy':'same-origin','Permissions-Policy':'camera=(), microphone=(), geolocation=()'})
        if request.url.path.startswith('/api/'):response.headers['Cache-Control']='no-store'
        return response

    @app.get('/api/health')
    async def health():
        return {'status':'ok','mode':'public_pdf_workspace','server_GT_storage':False,'fixed_dataset':False,
            'capabilities':{'pdf_upload':True,'page_selection':True,'pdf_rendering':True,'manual_target_roi':True,'architectural_observations':True,'raster_observations':True,'pdf_vector_observations':True,'OCR':(ROOT/'poc/.venv/bin/python').is_file(),'VLM':False,'Geometry':True,'architectural_geometry':True,'spatial_understanding':True,'reference_understanding':True,'target_understanding':True,'alignment':True,'scoped_spatial_alignment':True,'structural_comparison_review':True,'change_detection':True,'confirmed_change_export':True}}

    @app.get('/api/workspace')
    async def workspace(request:Request):
        token=request.cookies.get(COOKIE);w=spaces.get(token)
        if w is None:token,w=spaces.create()
        response=JSONResponse({'status':'ready','storage':'temporary_memory','idle_expiry_seconds':spaces.ttl})
        response.set_cookie(COOKIE,token,max_age=spaces.ttl,httponly=True,secure=request.url.scheme=='https',samesite='strict',path='/')
        return response

    @app.post('/api/observations',status_code=202)
    async def observe(request:Request):
        w=spaces.get(request.cookies.get(COOKIE))
        if w is None:raise HTTPException(409,'작업공간이 만료됐습니다. PDF를 다시 업로드하세요.')
        data=bytearray()
        async for chunk in request.stream():
            data.extend(chunk)
            if len(data)>65536:raise HTTPException(413,'관찰 요청이 너무 큽니다.')
        try:
            body=json.loads(data)
            if not isinstance(body,dict):raise ValueError('관찰 요청 형식을 확인하세요.')
            identity=body.get('document_id')
            if not isinstance(identity,str) or not re.fullmatch(r'[a-f0-9]{32}',identity):raise ValueError('업로드한 PDF를 선택하세요.')
            page=body.get('page');zoom=body.get('extraction_zoom')
            if type(page) is not int or page<1 or type(zoom) not in [int,float] or not math.isfinite(zoom) or not 0<zoom<=4:raise ValueError('페이지 또는 추출 배율을 확인하세요.')
            if not isinstance(body.get('image'),dict):raise ValueError('원본 이미지 Frame이 필요합니다.')
            document=w.api.state.documents.get(identity);image=w.api.state.documents.page(document,page,zoom)
            binding=validate_request(body,image);binding['document_id']=identity
        except (ValueError,TypeError,KeyError,PDFInputError):raise HTTPException(422,'ROI 좌표 또는 PDF·페이지·이미지 Frame·배율이 올바르지 않습니다.')
        return jobs.create(w,document,image,binding)

    @app.get('/api/observations/{identity}')
    async def observation_result(identity:str,request:Request):
        w=spaces.get(request.cookies.get(COOKIE))
        if w is None:raise HTTPException(404)
        job=jobs.get(w,identity)
        if job.get('job_kind')!='observation':raise HTTPException(404)
        return job

    @app.post('/api/reference-understandings',status_code=202)
    async def reference_understanding(request:Request):
        w=spaces.get(request.cookies.get(COOKIE))
        if w is None:raise HTTPException(409,'작업공간이 만료됐습니다. PDF를 다시 업로드하세요.')
        data=bytearray()
        async for chunk in request.stream():
            data.extend(chunk)
            if len(data)>65536:raise HTTPException(413,'관찰 요청이 너무 큽니다.')
        try:
            body=json.loads(data)
            if not isinstance(body,dict):raise ValueError('관찰 요청 형식을 확인하세요.')
            identity=body.get('document_id')
            if not isinstance(identity,str) or not re.fullmatch(r'[a-f0-9]{32}',identity):raise ValueError('업로드한 PDF를 선택하세요.')
            page=body.get('page');zoom=body.get('extraction_zoom')
            if type(page) is not int or page<1 or type(zoom) not in [int,float] or not math.isfinite(zoom) or not 0<zoom<=4:raise ValueError('페이지 또는 추출 배율을 확인하세요.')
            if not isinstance(body.get('image'),dict):raise ValueError('원본 이미지 Frame이 필요합니다.')
            document=w.api.state.documents.get(identity);image=w.api.state.documents.page(document,page,zoom)
            if body.get('source_role')!='Ref' or body.get('roi_bbox_native') is not None:raise ValueError('Ref whole-page source required')
            binding=validate_request(body,image);binding['document_id']=identity
            selection=body.get('drawing_region_selection')
            if selection is not None:
                if not isinstance(selection,dict) or not isinstance(selection.get('run_id'),str) or not isinstance(selection.get('region_id'),str):raise ValueError('DrawingRegion selection required')
                parent=jobs.get(w,selection['run_id'])
                if parent['job_kind']!='reference' or parent['status'] not in ['COMPLETED','PARTIAL']:raise ValueError('Completed Ref layout required')
                old=parent['result']['source_binding']
                if any(old.get(k)!=binding.get(k) for k in ['document_id','image_id','image_sha256','source_sha256','source_role','page','extraction_zoom','width','height','frame_id']):raise ValueError('DrawingRegion belongs to another source')
                regions=parent['result'].get('drawing_regions',{}).get('candidates',[])
                chosen=next((r for r in regions if r['region_id']==selection['region_id']),None)
                if chosen is None:raise ValueError('Unknown DrawingRegion')
                binding['drawing_region_bbox_native']=chosen['bbox_native']
                binding['drawing_region_selection']={'run_id':parent['run_id'],'region_id':chosen['region_id'],'selection_status':'USER_SELECTED_SCOPE_NOT_VERIFIED_GEOMETRY'}
        except (ValueError,TypeError,KeyError,PDFInputError):raise HTTPException(422,'Ref PDF·페이지·이미지 Frame·배율 또는 분석 범위가 올바르지 않습니다.')
        return jobs.create(w,document,image,binding,kind='reference')

    @app.get('/api/reference-understandings/{identity}')
    async def reference_result(identity:str,request:Request, summary:bool=False):
        w=spaces.get(request.cookies.get(COOKIE))
        if w is None:raise HTTPException(404)
        job=jobs.get(w,identity)
        if job.get('job_kind')!='reference':raise HTTPException(404)
        if summary:
            view={k:v for k,v in job.items() if k!='result'}
            # Processing completion is not verified room geometry. The small
            # product summary uses actual OCR-linked labels without sending the
            # full observation/geometry evidence to the browser on every poll.
            view['observed_space_names']=list(dict.fromkeys(
                case['label']['name'] for case in (job.get('result') or {}).get('cases',[])
                if isinstance(case.get('label',{}).get('name'),str) and case['label']['name']))
            return view
        return job

    @app.post('/api/target-understandings',status_code=202)
    async def target_understanding(request:Request):
        w=spaces.get(request.cookies.get(COOKIE))
        if w is None:raise HTTPException(409,'작업공간이 만료됐습니다. PDF를 다시 업로드하세요.')
        data=bytearray()
        async for chunk in request.stream():
            data.extend(chunk)
            if len(data)>65536:raise HTTPException(413,'관찰 요청이 너무 큽니다.')
        try:
            body=json.loads(data)
            if not isinstance(body,dict):raise ValueError('관찰 요청 형식을 확인하세요.')
            identity=body.get('document_id')
            if not isinstance(identity,str) or not re.fullmatch(r'[a-f0-9]{32}',identity):raise ValueError('업로드한 PDF를 선택하세요.')
            page=body.get('page');zoom=body.get('extraction_zoom')
            if type(page) is not int or page<1 or type(zoom) not in [int,float] or not math.isfinite(zoom) or not 0<zoom<=4:raise ValueError('페이지 또는 추출 배율을 확인하세요.')
            if not isinstance(body.get('image'),dict):raise ValueError('원본 이미지 Frame이 필요합니다.')
            document=w.api.state.documents.get(identity);image=w.api.state.documents.page(document,page,zoom)
            if body.get('source_role')!='Target' or body.get('roi_bbox_native') is not None:raise ValueError('Target page context required')
            if any(key in body for key in ('reference_run_id','observation_run_id','geometry_run_id','reference_model','observations')):
                raise ValueError('Own uploaded Target input required')
            target_body={**body,'roi_bbox_native':[0,0,image.width,image.height],'context_margin_px':0}
            binding=validate_request(target_body,image);binding['document_id']=identity
            selection=body.get('drawing_region_selection')
            if selection is not None:
                if not isinstance(selection,dict) or not isinstance(selection.get('run_id'),str) or not isinstance(selection.get('region_id'),str):raise ValueError('DrawingRegion selection required')
                parent=jobs.get(w,selection['run_id'])
                if parent['job_kind']!='target' or parent['status'] not in ['COMPLETED','PARTIAL']:raise ValueError('Completed Target layout required')
                old=parent['result']['source_binding']
                if any(old.get(k)!=binding.get(k) for k in ['document_id','image_id','image_sha256','source_sha256','source_role','page','extraction_zoom','width','height','frame_id']):raise ValueError('DrawingRegion belongs to another source')
                regions=parent['result'].get('drawing_regions',{}).get('candidates',[])
                chosen=next((r for r in regions if r['region_id']==selection['region_id']),None)
                if chosen is None:raise ValueError('Unknown DrawingRegion')
                binding['drawing_region_bbox_native']=chosen['bbox_native']
                binding['drawing_region_selection']={'run_id':parent['run_id'],'region_id':chosen['region_id'],'selection_status':'USER_SELECTED_SCOPE_NOT_VERIFIED_GEOMETRY'}
        except (ValueError,TypeError,KeyError,PDFInputError):raise HTTPException(422,'Target PDF·페이지·이미지 Frame·배율 또는 분석 범위가 올바르지 않습니다.')
        return jobs.create(w,document,image,binding,kind='target')

    @app.get('/api/target-understandings/{identity}')
    async def target_result(identity:str,request:Request, summary:bool=False):
        w=spaces.get(request.cookies.get(COOKIE))
        if w is None:raise HTTPException(404)
        job=jobs.get(w,identity)
        if job.get('job_kind')!='target':raise HTTPException(404)
        return {k:v for k,v in job.items() if k!='result'} if summary else job

    @app.post('/api/geometries',status_code=202)
    async def geometry(request:Request):
        w=spaces.get(request.cookies.get(COOKIE))
        if w is None:raise HTTPException(409,'작업공간이 만료됐습니다. PDF를 다시 업로드하세요.')
        data=bytearray()
        async for chunk in request.stream():
            data.extend(chunk)
            if len(data)>65536:raise HTTPException(413,'기하 요청이 너무 큽니다.')
        try:
            body=json.loads(data)
            if not isinstance(body,dict):raise ValueError('Observation required')
            reference = 'reference_run_id' in body
            target = 'target_run_id' in body
            if reference and target:raise ValueError('One source pipeline required')
            independent = reference or target
            field = 'reference_run_id' if reference else 'target_run_id' if target else 'observation_run_id'
            if not isinstance(body.get(field),str) or (independent and (
                    body.get('source_role') != ('Ref' if reference else 'Target') or body.get('roi_bbox_native') is not None
                    or 'observation_run_id' in body)):
                raise ValueError('Invalid Ref source')
            parent = jobs.get(w,body[field])
            expected_kind = 'reference' if reference else 'target' if target else 'observation'
            if parent.get('job_kind')!=expected_kind or parent['status'] not in ['COMPLETED','PARTIAL'] or not parent['result']:
                raise HTTPException(409,'관찰 생성이 완료된 뒤 실행하세요.')
            observations = parent['result']['observation_bundle'] if independent else parent['result']
            identity=body.get('document_id')
            if identity!=parent['document_id']:raise ValueError('Different uploaded document')
            page=body.get('page');zoom=body.get('extraction_zoom')
            if type(page) is not int or type(zoom) not in [int,float] or not math.isfinite(zoom) or not 0<zoom<=4 or not isinstance(body.get('image'),dict):raise ValueError('Invalid frame')
            document=w.api.state.documents.get(identity);image=w.api.state.documents.page(document,page,zoom)
            geometry_body={**body,'roi_bbox_native':[0,0,image.width,image.height],'context_margin_px':0} if target else body
            binding=validate_request(geometry_body,image);binding['document_id']=identity
            from backend.architectural_geometry import validate_parent
            if independent:
                # Only the completed, owned observation can supply the analysis window.
                saved = observations['source_binding']
                for key in ('core_bbox_native','context_bbox_native','context_margin_px','drawing_region_selection','drawing_region_bbox_native'):
                    if key in saved:binding[key]=saved[key]
            validate_parent(binding,observations)
        except (ValueError,TypeError,KeyError,PDFInputError):raise HTTPException(422,'현재 PDF·ROI·Frame과 완료된 관찰 입력이 일치하지 않습니다.')
        return jobs.create(w,document,image,binding,parent=observations)

    @app.get('/api/geometries/{identity}')
    async def geometry_result(identity:str,request:Request, summary:bool=False):
        w=spaces.get(request.cookies.get(COOKIE))
        if w is None:raise HTTPException(404)
        job=jobs.get(w,identity)
        if job.get('job_kind')!='geometry':raise HTTPException(404)
        return {k:v for k,v in job.items() if k!='result'} if summary else job

    # Pair only finished geometry revisions already owned by this browser. Do not
    # accept caller-supplied evidence, transforms, or paths.
    from asyncio import Lock
    from starlette.concurrency import run_in_threadpool
    alignment_slot = Lock()

    def resolve_comparison(request, run_id):
        w = spaces.get(request.cookies.get(COOKIE))
        review = w.comparisons.get(run_id) if w else None
        if review is None:
            raise HTTPException(404,'이 작업공간에서 저장한 비교를 찾을 수 없습니다.')
        store = w.api.state.documents
        for document in review.documents:
            store.get(document['id'])
        return store, review

    @app.get('/api/comparison-reviews')
    async def list_comparison_reviews(request:Request):
        w = spaces.get(request.cookies.get(COOKIE))
        if not w:
            return []
        return [dict(run_id=run_id, documents=review.documents,
                     pages=[s['image']['page'] for s in review.comparison['sources']])
                for run_id, review in reversed(w.comparisons.items())
                if review.revision and all(d['id'] in w.api.state.documents.documents for d in review.documents)]

    @app.get('/api/comparison-reviews/{run_id}')
    async def get_comparison_review(run_id:str, request:Request):
        _, review = resolve_comparison(request, run_id)
        return review.view()

    @app.get('/api/comparison-reviews/{run_id}/upstream-trace')
    async def comparison_upstream_trace(run_id:str,request:Request,format:str='json',subject_id:str|None=None,
                                        role:str|None=None,stage:str|None=None,offset:int=0,limit:int=200):
        _,review=resolve_comparison(request,run_id)
        if role not in (None,'Ref','Target') or not 0<=offset<=1_000_000 or not 1<=limit<=1000 or (subject_id and len(subject_id)>1024) or (stage and len(stage)>80):
            raise HTTPException(422,'추적 범위·페이지 크기를 확인하세요.')
        summary=public_comparison(review.comparison).get('upstream_lineage')
        if summary is None:raise HTTPException(404,'이전 실행에는 상류 추적 기록이 없습니다.')
        if format=='jsonl':
            return StreamingResponse(jsonl_chunks(review.comparison,subject_id=subject_id,role=role,stage=stage),media_type='application/x-ndjson',
                headers={'Content-Disposition':'attachment; filename="fpue_upstream_trace.jsonl"','Cache-Control':'no-store'})
        if format!='json':raise HTTPException(422,'지원하는 추적 형식을 확인하세요.')
        from starlette.concurrency import run_in_threadpool
        selected=await run_in_threadpool(select_records,review.comparison,subject_id=subject_id,role=role,stage=stage,offset=offset,limit=limit)
        return {**selected,'summary':summary,'review_revision':review.revision,'user_review_decisions':review.decisions}

    @app.put('/api/comparison-reviews/{run_id}')
    async def save_comparison_review(run_id:str, request:Request):
        data = bytearray()
        async for chunk in request.stream():
            data.extend(chunk)
            if len(data) > 1024*1024:
                raise HTTPException(413,'검토 요청이 너무 큽니다.')
        _, review = resolve_comparison(request, run_id)
        try:
            body = json.loads(data)
            if not isinstance(body, dict) or set(body) != {'revision', 'decisions'} or type(body['revision']) is not int:
                raise ValueError('검토 저장 형식을 확인하세요.')
            if body['revision'] != review.revision:
                raise HTTPException(409,'다른 화면에서 검토를 저장했습니다. 저장본을 다시 여세요.')
            return review.save(body['decisions'])
        except (ValueError, TypeError) as exc:
            raise HTTPException(422,'검토 상태와 비교 검증 범위를 확인하세요.') from exc

    @app.get('/api/comparison-reviews/{run_id}/export')
    async def download_comparison_review(run_id:str, revision:int, request:Request, format:str='zip'):
        store, review = resolve_comparison(request, run_id)
        if not review.revision or revision != review.revision:
            raise HTTPException(409,'현재 검토를 저장한 뒤 다시 내보내세요.')
        image = review.comparison['sources'][1]['image']
        target = store.page(store.get(review.documents[1]['id']),image['page'],image['extraction_zoom'])
        outputs = {'zip':('application/zip','fpue_comparison.zip'),
                   'csv':('text/csv','change_report.csv'),'png':('image/png','target_changes.png')}
        if format not in outputs:
            raise HTTPException(422,'내보낼 파일 형식을 확인하세요.')
        mime, filename = outputs[format]
        return Response(export_review(review,target,format),media_type=mime,
                        headers={'Content-Disposition':f'attachment; filename="{filename}"'})

    @app.post('/api/structural-comparisons')
    @app.post('/api/spatial-alignments')
    async def spatial_alignment(request:Request):
        from backend.spatial_alignment import verify
        w = spaces.get(request.cookies.get(COOKIE))
        if w is None:
            raise HTTPException(409,'작업공간이 만료됐습니다. PDF를 다시 업로드하세요.')
        data = bytearray()
        async for chunk in request.stream():
            data.extend(chunk)
            if len(data)>65536:
                raise HTTPException(413,'정렬 요청이 너무 큽니다.')
        if alignment_slot.locked():
            raise HTTPException(429,'정렬 검증이 진행 중입니다. 잠시 후 다시 실행하세요.')
        async with alignment_slot:
            try:
                body = json.loads(data)
                if not isinstance(body,dict) or set(body)!={'Ref','Target','target_roi'} or not isinstance(body['target_roi'],dict):
                    raise ValueError('두 이해 결과와 Target ROI가 필요합니다.')
                pages, geometries, documents, vectors = [], [], [], []
                dimension_vectors=[]
                store = w.api.state.documents
                for role in ('Ref','Target'):
                    source = body[role]
                    if not isinstance(source,dict) or not isinstance(source.get('geometry_run_id'),str):
                        raise ValueError('현재 도면의 기하 실행을 선택하세요.')
                    job = jobs.get(w,source['geometry_run_id'])
                    binding = job['binding']
                    if (job['job_kind']!='geometry' or job['status'] not in ('COMPLETED','PARTIAL')
                            or job['document_id']!=source.get('document_id') or binding['source_role']!=role
                            or binding['frame_id']!=source.get('frame_id') or not job['result']
                            or job['result']['run_id']!=job['run_id']):
                        raise ValueError('선택한 도면과 기하 실행이 일치하지 않습니다.')
                    document = store.get(job['document_id'])
                    documents.append(document.metadata())
                    pages.append(store.page(document,binding['page'],binding['extraction_zoom']))
                    geometries.append(job['result'])
                    from backend.wall_sections import read_strokes
                    import pymupdf
                    try:
                        vectors.append(read_strokes(document.data,pages[-1]))
                    except pymupdf.FileDataError:
                        vectors.append([])
                    if request.url.path=='/api/structural-comparisons':
                        from backend.dimension_comparison import read_dimension_geometry
                        try:
                            dimension_vectors.append(await run_in_threadpool(read_dimension_geometry,document.data,pages[-1]))
                        except pymupdf.FileDataError:
                            dimension_vectors.append(None)
                if request.url.path=='/api/structural-comparisons':
                    from backend.structural_comparison import compare
                    result = await run_in_threadpool(compare,pages,geometries,body['target_roi'],vectors,
                        dimension_vectors=dimension_vectors,annotation_decisions=True,opening_geometry_decisions=True)
                    run_id = result['run_id']
                    review = w.comparisons.get(run_id)
                    if review is None:
                        if len(w.comparisons) >= 6:
                            removable = next((key for key, value in w.comparisons.items() if not value.revision
                                              or any(d['id'] not in store.documents for d in value.documents)), None)
                            if removable is None:
                                raise HTTPException(409,'작업공간에 비교 검토 6개가 저장되어 있습니다. 결과를 내려받고 새 브라우저 작업공간을 사용하세요.')
                            del w.comparisons[removable]
                        review = w.comparisons[run_id] = ComparisonReview(result, documents)
                    if not review.revision and (request.query_params.get('workflow')=='auto' or any(
                            row.get('automatic_confirmation') for row in result['correspondences'])):
                        review.save({})
                    return {**public_comparison(review.comparison), 'review_revision':review.revision,
                            'review_decisions':review.decisions}
                return await run_in_threadpool(verify,pages,geometries,body['target_roi'],vectors)
            except (ValueError,TypeError,KeyError) as exc:
                raise HTTPException(422,'현재 도면·독립 이해 결과·ROI의 원본 좌표를 확인하세요.') from exc

    @app.post('/api/spatials',status_code=202)
    async def spatial(request:Request):
        w=spaces.get(request.cookies.get(COOKIE))
        if w is None:raise HTTPException(409,'작업공간이 만료됐습니다. PDF를 다시 업로드하세요.')
        data=bytearray()
        async for chunk in request.stream():
            data.extend(chunk)
            if len(data)>65536:raise HTTPException(413,'공간 요청이 너무 큽니다.')
        try:
            body=json.loads(data)
            if not isinstance(body,dict) or not isinstance(body.get('geometry_run_id'),str):raise ValueError('Geometry required')
            parent=jobs.get(w,body['geometry_run_id'])
            if parent.get('job_kind')!='geometry' or parent['status'] not in ['COMPLETED','PARTIAL'] or not parent['result']:raise HTTPException(409,'Geometry 분석이 완료된 뒤 실행하세요.')
            identity=body.get('document_id')
            if identity!=parent['document_id']:raise ValueError('Different document')
            if body.get('observation_run_id')!=parent['result']['parent_observation_run_id']:raise ValueError('Different observation revision')
            page=body.get('page');zoom=body.get('extraction_zoom')
            if type(page) is not int or type(zoom) not in [int,float] or not math.isfinite(zoom) or not 0<zoom<=4 or not isinstance(body.get('image'),dict):raise ValueError('Invalid frame')
            document=w.api.state.documents.get(identity);image=w.api.state.documents.page(document,page,zoom)
            binding=validate_request(body,image);binding['document_id']=identity
            from backend.architectural_spatial import validate_geometry_parent
            validate_geometry_parent(binding,parent['result'])
        except (ValueError,TypeError,KeyError,PDFInputError):raise HTTPException(422,'현재 PDF·ROI·Frame과 완료된 Geometry 입력이 일치하지 않습니다.')
        return jobs.create(w,document,image,binding,parent=parent['result'],kind='spatial')

    @app.get('/api/spatials/{identity}')
    async def spatial_result(identity:str,request:Request):
        w=spaces.get(request.cookies.get(COOKIE))
        if w is None:raise HTTPException(404)
        job=jobs.get(w,identity)
        if job.get('job_kind')!='spatial':raise HTTPException(404)
        return job

    @app.api_route('/api/{path:path}',methods=['GET','POST','DELETE','PUT','PATCH'])
    async def pdf(request:Request,path:str):
        allowed=(request.method in ['GET','POST'] and path=='documents') or (request.method in ['GET','DELETE'] and re.fullmatch(r'documents/[a-f0-9]{32}',path)) or (request.method=='GET' and re.fullmatch(r'documents/[a-f0-9]{32}/pages/-?[0-9]+/image',path))
        if not allowed:raise HTTPException(404)
        w=spaces.get(request.cookies.get(COOKIE))
        if w is None:raise HTTPException(409 if request.method=='POST' else 404,'작업공간이 없거나 만료됐습니다. 페이지를 새로 열고 PDF를 다시 업로드하세요.')
        if request.method == 'DELETE' and request.query_params.get('preserve_review') == '1':
            document_id = path.split('/')[1]
            if any(review.revision and any(d['id']==document_id for d in review.documents)
                   for review in w.comparisons.values()):
                return Response(status_code=204)
        return ExistingPDFResponse(w.api)

    if (dist/'assets').is_dir():app.mount('/assets',StaticFiles(directory=dist/'assets'),name='assets')
    @app.get('/{path:path}')
    async def page(path:str):
        if path not in ['','compare']:raise HTTPException(404)
        if not (dist/'index.html').is_file():raise HTTPException(503,'프런트엔드 빌드를 먼저 생성하세요.')
        return FileResponse(dist/'index.html',headers={'Cache-Control':'no-store'})
    return app
