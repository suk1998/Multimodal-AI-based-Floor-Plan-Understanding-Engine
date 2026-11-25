"""Small in-memory alignment workflow attached to the existing PDF API."""
from collections import OrderedDict
from typing import Literal
from uuid import uuid4

from fastapi import HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

from backend.structural_alignment import align_pages
from phase1_pdf import PDFInputError


class PageSpec(BaseModel):
    document_id: str
    page: int = Field(ge=1)
    zoom: float = Field(gt=0,le=4,allow_inf_nan=False)


class Anchor(BaseModel):
    id: str = Field(min_length=1,max_length=40)
    target: tuple[float,float]
    ref: tuple[float,float]
    role: Literal['fit','check']
    confirmed: bool = False
    reason: str = Field(default='',max_length=1000)


class AlignmentRequest(BaseModel):
    ref: PageSpec
    target: PageSpec
    target_roi: dict
    mode: Literal['auto','manual'] = 'auto'
    anchors: list[Anchor] = Field(default_factory=list,max_length=128)
    tolerance_ref_px: float = Field(default=3,ge=.25,le=20,allow_inf_nan=False)


class ReviewRequest(BaseModel):
    decision: Literal['approve','reject']
    stable_structures_confirmed: bool = False
    reason: str = Field(min_length=3,max_length=2000)
    scope: Literal['sampled_support_only'] = 'sampled_support_only'


def register_alignment_routes(app,documents):
    runs=OrderedDict()
    app.state.alignments=runs

    def get(run_id):
        if run_id not in runs:raise HTTPException(410,'임시 정렬 결과가 만료됐습니다. 다시 실행하세요.')
        entry=runs[run_id]
        # Removed/replaced PDFs cannot silently preserve a live approval.
        for doc_id in entry['document_ids']:documents.get(doc_id)
        return entry

    @app.post('/api/alignments',status_code=201)
    async def run(body: AlignmentRequest):
        if len({a.id for a in body.anchors})!=len(body.anchors):raise HTTPException(422,'대응점 ID가 중복됩니다.')
        try:
            pages=[documents.page(documents.get(spec.document_id),spec.page,spec.zoom) for spec in [body.ref,body.target]]
            result,previews=align_pages(*pages,body.target_roi,body.mode,[a.model_dump() for a in body.anchors],body.tolerance_ref_px)
        except (ValueError,PDFInputError) as exc:raise HTTPException(422,str(exc)) from exc
        run_id=uuid4().hex;result['run_id']=run_id
        result['preview_modes']=list(previews)
        entry={'result':result,'previews':previews,'document_ids':[body.ref.document_id,body.target.document_id]}
        size=sum(len(p) for p in previews.values())
        if size>32*1024*1024:raise HTTPException(413,'정렬 미리보기가 너무 큽니다. 추출 배율을 낮추세요.')
        while runs and (len(runs)>=6 or sum(len(p) for e in runs.values() for p in e['previews'].values())+size>32*1024*1024):runs.popitem(last=False)
        runs[run_id]=entry
        return result

    @app.get('/api/alignments/{run_id}')
    async def metadata(run_id:str):return get(run_id)['result']

    @app.get('/api/alignments/{run_id}/preview/{mode}')
    async def preview(run_id:str,mode:str):
        png=get(run_id)['previews'].get(mode)
        if png is None:raise HTTPException(404,'이 결과에는 요청한 정렬 미리보기가 없습니다.')
        return Response(png,media_type='image/png',headers={'Cache-Control':'no-store'})

    @app.post('/api/alignments/{run_id}/review')
    async def review(run_id:str,body:ReviewRequest):
        result=get(run_id)['result']
        if not body.reason.strip():raise HTTPException(422,'검토 근거를 기록하세요.')
        if body.decision=='approve':
            if not result['approval_eligible'] or not body.stable_structures_confirmed:
                raise HTTPException(409,'독립 검증과 안정 구조 확인이 부족합니다. 대응점을 보완해 다시 실행하세요.')
            state='APPROVED_SAMPLED' if result['status']=='VERIFIED_SAMPLED' else 'APPROVED_PARTIAL'
        else:state='REJECTED'
        result['review']={'state':state,'reason':body.reason.strip(),'scope':body.scope,
                          'stable_structures_confirmed':body.stable_structures_confirmed,
                          'warning':'승인 범위는 기록된 표본 구조 지지 범위입니다. 전체 ROI의 조밀 검증이나 변경 탐지 완료가 아닙니다.'}
        result.setdefault('review_history',[]).append(dict(result['review']))
        return result
