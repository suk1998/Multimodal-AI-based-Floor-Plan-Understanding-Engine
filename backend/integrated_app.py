"""One-origin React/FastAPI studio. Existing analytical code remains read-only."""
import copy,hashlib,json,os,sqlite3,uuid
from collections import OrderedDict
from pathlib import Path

from fastapi import FastAPI,HTTPException,Request
from fastapi.responses import FileResponse,JSONResponse,Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from backend.review_store import ReviewStore,RevisionConflict
from backend.web_auth import Authentication,AuthConfig,COOKIE
from poc.gt_evaluation import validate_package,evaluate,package_to_csv,points_of

ROOT=Path(__file__).resolve().parents[1]
REVIEW=ROOT/'poc/results/gt_audit_page4'


class Catalog:
    def __init__(self,directory=REVIEW):
        self.directory=Path(directory);self.manifest=json.loads((self.directory/'review_manifest.json').read_text())
        raw=(self.directory/'candidate_registry.json').read_bytes()
        if hashlib.sha256(raw).hexdigest()!=self.manifest['registry_sha256']:raise ValueError('Review registry changed')
        self.predictions=json.loads(raw);self.cases=json.loads((self.directory/'review_cases.json').read_text())['cases']
        self.initial=json.loads((self.directory/'gt_annotations.json').read_text());self.initial.update(gt_annotations=[],candidate_gt_matches=[],evaluation_scopes=[],legacy_rows=[],annotation_revision='INITIAL_UNREVIEWED')
        for r in self.initial['candidate_reviews'].values():r.update(review_status='UNREVIEWED',reviewer=None,reviewed_at=None,review_notes='')
        self.config=json.loads((self.directory/'evaluation_config.json').read_text())
        self.roi=json.loads((ROOT/'poc/results/page4_inputs/Target/user_roi.json').read_text())
        target=self.manifest['bindings']['Target']
        if any(self.roi['image'][k]!=target[k] for k in ['source_sha256','page','extraction_zoom','width','height']):raise ValueError('ROI binding mismatch')
        self.spaces=[]
        data=json.loads((ROOT/'poc/results/space_grounding_page4/space_candidates.json').read_text())['drawings']
        for role,spaces in data.items():
            for s in spaces:
                if not s['name_hypotheses']:continue
                g=s['geometry'];box=g.get('bbox_xyxy') or g.get('analysis_extent_xyxy');seed=g.get('seed_native')
                if not box and seed:box=[seed[0]-100,seed[1]-100,seed[0]+100,seed[1]+100]
                self.spaces.append({'id':s['id'],'role':role,'frame_id':s['frame_id'],'name':s['name_hypotheses'][0]['text'],
                    'seed':seed,'focus_bounds':box,'polygon':g.get('polygon_native'),'status':'PROPOSED','focus_is_not_boundary':True})
        self.ocr={r:json.loads((ROOT/'poc/results/page4_ocr'/r/'observations.json').read_text()) for r in ['Ref','Target']}
        vlm=json.loads((ROOT/'poc/results/space_grounding_page4/vlm_geometry_comparison.json').read_text())['drawings']
        self.vlm={r:[{'id':c['vlm_entity']['scope_id'],'bbox':c['vlm_entity'].get('bbox_native_xyxy'),'label':c['vlm_entity'].get('label',c['vlm_entity'].get('type','후보')),
            'status':'PROPOSED','model':c['model']} for c in d['comparisons'] if c.get('vlm_entity',{}).get('bbox_native_xyxy')] for r,d in vlm.items()}

    def validate(self,package):
        if not isinstance(package,dict):raise ValueError('검토 데이터 형식이 올바르지 않습니다.')
        if package.get('dataset_kind')!='REAL_PAGE4_HUMAN_REVIEW':raise ValueError('테스트 주석은 실제 사용자 GT 저장소에 저장하지 않습니다.')
        if len(package.get('gt_annotations',[]))>5000 or len(package.get('evaluation_scopes',[]))>100:raise ValueError('검토 항목이 저장 한도를 초과했습니다.')
        for g in package.get('gt_annotations',[])+package.get('evaluation_scopes',[]):
            if len(points_of(g.get('geometry_native')))>512:raise ValueError('한 Geometry는 512점 이하로 지정하세요.')
        geometries=package.get('gt_annotations',[])+package.get('evaluation_scopes',[])
        if sum(len(points_of(g.get('geometry_native'))) for g in geometries)>25000:raise ValueError('한 검토의 전체 좌표 수가 저장 한도를 초과했습니다.')
        if sum(len(points_of(g.get('geometry_native')))**2 for g in geometries if (g.get('geometry_native') or {}).get('closed'))>2000000:raise ValueError('폐쇄 경계가 너무 복잡합니다. 검토 범위를 나누세요.')
        validate_package(package,self.manifest,self.predictions)

    def public(self):
        return {'manifest':self.manifest,'cases':self.cases,'candidates':[{k:v for k,v in p.items() if k!='original'} for p in self.predictions],
            'spaces':self.spaces,'roi':self.roi,'ocr':self.ocr,'vlm':self.vlm,'config':self.config,
            'capabilities':{'change_detection':False,'alignment_auto':False,'analysis_status':'PROPOSED'}}


async def body_json(request,limit=16*1024*1024):
    data=bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data)>limit:raise HTTPException(413,'저장할 검토 데이터가 너무 큽니다.')
    try:
        value=json.loads(data,parse_constant=lambda _:(_ for _ in ()).throw(ValueError('non-finite JSON')))
        if not isinstance(value,dict):raise ValueError('JSON object required')
        return value
    except (ValueError,UnicodeDecodeError):raise HTTPException(422,'올바른 검토 데이터가 아닙니다.')


def create_app(*,data_dir=None,auth_config=None,catalog=None,jwks_client=None,static_dir=None,instance_kind=None):
    folder=Path(data_dir or os.getenv('FPUE_DATA_DIR',str(ROOT/'var/fpue')))
    config=auth_config or AuthConfig(mode=os.getenv('FPUE_AUTH_MODE','local'),user_file=folder/'local_auth.json',
        public_origin=os.getenv('FPUE_PUBLIC_ORIGIN','http://127.0.0.1:8020'),access_issuer=os.getenv('FPUE_ACCESS_ISSUER',''),access_audience=os.getenv('FPUE_ACCESS_AUDIENCE',''),development_origin=os.getenv('FPUE_DEV_ORIGIN') or None)
    kind=instance_kind or os.getenv('FPUE_INSTANCE_KIND','USER')
    if kind not in ['USER','TEST_ONLY']:raise ValueError('Invalid instance kind')
    store=ReviewStore(folder/'reviews.sqlite3');auth=Authentication(config,store,jwks_client);cat=catalog or Catalog()
    app=FastAPI(title='FPUE Studio',docs_url=None,redoc_url=None,openapi_url=None);app.state.reviews=store;app.state.catalog=cat
    from urllib.parse import urlsplit
    app.add_middleware(TrustedHostMiddleware,allowed_hosts=['localhost','127.0.0.1','testserver',urlsplit(config.public_origin).hostname])

    @app.middleware('http')
    async def authenticated(request,call_next):
        try:
            if request.method not in ['GET','HEAD','OPTIONS']:auth.check_origin(request)
            identity=auth.identify(request);request.state.identity=identity
            protected=request.url.path.startswith(('/api/','/advanced/')) and request.url.path not in ['/api/health','/api/auth/config','/api/auth/login']
            if protected and not identity:return JSONResponse({'detail':'로그인이 필요합니다.'},status_code=401)
            response=await call_next(request)
        except HTTPException as exc:return JSONResponse({'detail':exc.detail},status_code=exc.status_code)
        response.headers['X-Content-Type-Options']='nosniff';response.headers['Referrer-Policy']='same-origin'
        response.headers['Content-Security-Policy']="default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; script-src 'self'; frame-src 'self'; frame-ancestors 'self'; connect-src 'self'; object-src 'none'; base-uri 'self'"
        if protected:response.headers['Cache-Control']='no-store'
        return response

    @app.get('/api/health')
    async def health():return {'status':'ok','scope':'fpue_studio'}

    @app.get('/api/auth/config')
    async def auth_mode():return {'mode':config.mode}

    @app.post('/api/auth/login')
    async def login(request:Request):
        data=await body_json(request,4096);username=data.get('username','');password=data.get('password','')
        if not isinstance(username,str) or not isinstance(password,str) or len(username)>100 or len(password)>256:raise HTTPException(422,'로그인 입력을 확인하세요.')
        token,identity=auth.login(username,password,request.client.host if request.client else 'unknown');response=JSONResponse({**identity,'instance_kind':kind})
        response.set_cookie(COOKIE,token,max_age=28800,httponly=True,samesite='strict',secure=False,path='/');response.headers['Cache-Control']='no-store';return response

    @app.get('/api/session')
    async def session(request:Request):return {**request.state.identity,'instance_kind':kind}

    @app.post('/api/auth/logout')
    async def logout(request:Request):
        auth.store.revoke_session(request.cookies.get(COOKIE));response=JSONResponse({'logged_out':True});response.delete_cookie(COOKIE,path='/');return response

    @app.get('/api/fpue/catalog')
    async def catalog_data():return cat.public()

    @app.get('/api/fpue/images/{role}')
    async def image(role:str):
        if role not in ['Ref','Target']:raise HTTPException(404)
        return FileResponse(cat.directory/role/'original.png',media_type='image/png')

    @app.get('/api/fpue/candidate')
    async def candidate(key:str):
        item=next((p for p in cat.predictions if p['candidate_key']==key),None)
        if not item:raise HTTPException(404)
        return item

    def current(owner):
        return store.load(owner,cat.manifest['dataset_id']) or {'revision':0,'latest_revision':0,'package':copy.deepcopy(cat.initial),'saved_at':None}

    @app.get('/api/fpue/review')
    async def load(request:Request):return current(request.state.identity['id'])

    @app.put('/api/fpue/review')
    async def save(request:Request):
        body=await body_json(request)
        try:
            base=body['base_revision'];save_id=body['save_id'];package=body['package']
            if type(base) is not int or base<0:raise ValueError('검토 버전이 올바르지 않습니다.')
            uuid.UUID(save_id);cat.validate(package)
            if kind=='USER' and package.get('review_instance_kind')=='TEST_ONLY':raise ValueError('자동 검증 주석은 사용자 저장소로 가져올 수 없습니다.')
            if kind=='TEST_ONLY':package['review_instance_kind']='TEST_ONLY'
            return store.save(request.state.identity['id'],cat.manifest['dataset_id'],base,save_id,package)
        except RevisionConflict as exc:raise HTTPException(409,{'code':'REVISION_CONFLICT','latest_revision':exc.latest,'message':'다른 창에서 저장한 검토가 있습니다. 내 수정은 유지했습니다. 서버의 최신 검토를 확인하세요.'})
        except (ValueError,TypeError,KeyError,AttributeError,IndexError) as exc:raise HTTPException(422,str(exc))
        except (sqlite3.Error,OSError):raise HTTPException(503,'서버에 저장하지 못했습니다. 내 수정은 유지했습니다. 잠시 후 다시 저장하세요.')

    @app.get('/api/fpue/history')
    async def history(request:Request):return store.history(request.state.identity['id'],cat.manifest['dataset_id'])

    @app.get('/api/fpue/history/{revision}')
    async def old_revision(revision:int,request:Request):
        record=store.load(request.state.identity['id'],cat.manifest['dataset_id'],revision)
        if not record:raise HTTPException(404)
        return record

    @app.post('/api/fpue/evaluate')
    async def evaluate_saved(request:Request):
        body=await body_json(request);record=current(request.state.identity['id'])
        if record['revision']!=body.get('revision'):raise HTTPException(409,'검토가 변경됐습니다. 최신 저장본을 다시 확인하세요.')
        settings=body.get('config',cat.config)
        if not isinstance(settings,dict):raise HTTPException(422,'평가 설정을 확인하세요.')
        step=settings.get('sampling_step_px')
        if type(step) not in [float,int] or not .25<=step<=10:raise HTTPException(422,'웹 평가의 샘플 간격은 0.25~10px로 지정하세요.')
        if sum(g.get('review_status')=='CONFIRMED' for g in record['package']['gt_annotations'])>150 or len(record['package']['evaluation_scopes'])>8:raise HTTPException(413,'큰 검토는 기존 PoC 2a CLI에서 평가하세요. 웹 평가는 확정 객체 150개·범위 8개까지 지원합니다.')
        try:result,audit=evaluate(record['package'],cat.manifest,cat.predictions,settings)
        except (ValueError,TypeError,KeyError) as exc:raise HTTPException(422,str(exc))
        return {'revision':record['revision'],'result':result,'audit':audit}

    @app.get('/api/fpue/export')
    async def export(request:Request,format:str='json'):
        package=current(request.state.identity['id'])['package']
        if format=='csv':return Response(package_to_csv(package),media_type='text/csv',headers={'Content-Disposition':'attachment; filename=fpue_gt_review.csv'})
        return JSONResponse(package,headers={'Content-Disposition':'attachment; filename=fpue_gt_review.json'})

    @app.get('/advanced/{path:path}')
    async def advanced(path:str,request:Request):
        if path=='review_data.js':
            packet=current(request.state.identity['id'])['package'];result,audit=evaluate(packet,cat.manifest,cat.predictions,cat.config)
            payload={'manifest':cat.manifest,'predictions':cat.predictions,'cases':cat.cases,'initial':packet,'initial_evaluation':result,'initial_audit':audit}
            return Response('window.FPUE_GT_DATA='+json.dumps(payload,ensure_ascii=False,allow_nan=False).replace('<',chr(92)+'u003c')+';',media_type='application/javascript')
        allowed={'review.html','gt_review.js','gt_review.css','GT_REVIEW_GUIDE.md','review_cases.json'}|{f'{role}/{name}' for role in ['Ref','Target'] for name in ['original.png','poc1_overlay.png','poc2_overlay.png']}|{'legacy/gt_review_template.csv','legacy/gt_review_template.json'}
        if path not in allowed:raise HTTPException(404)
        return FileResponse(cat.directory/path)

    # Reuse the existing PDF/alignment API in an isolated app per authenticated
    # owner. This preserves old routes while separating document/run contexts.
    legacy_apps=OrderedDict()
    class DelegatedResponse(Response):
        def __init__(self,delegate):super().__init__();self.delegate=delegate
        async def __call__(self,scope,receive,send):await self.delegate(scope,receive,send)
    @app.api_route('/api/{legacy_path:path}',methods=['GET','POST','PUT','DELETE'])
    async def legacy(request:Request):
        from backend.app import create_app as existing_app
        owner=request.state.identity['id']
        if owner not in legacy_apps:
            if len(legacy_apps)>=8:legacy_apps.popitem(last=False)
            legacy_apps[owner]=existing_app()
        legacy_apps.move_to_end(owner);return DelegatedResponse(legacy_apps[owner])

    dist=Path(static_dir or ROOT/'frontend/dist')
    if (dist/'assets').is_dir():app.mount('/assets',StaticFiles(directory=dist/'assets'),name='assets')
    @app.get('/{path:path}')
    async def frontend(path:str):
        if path.startswith(('api/','advanced/')) or '.' in path:raise HTTPException(404)
        if not (dist/'index.html').is_file():raise HTTPException(503,'프런트엔드 배포 빌드를 먼저 생성하세요.')
        return FileResponse(dist/'index.html',headers={'Cache-Control':'no-cache'})
    return app
