"""Separate line-verification workflow; legacy /api/alignments is unchanged."""
from collections import OrderedDict
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
from typing import Literal
from uuid import uuid4

from fastapi import HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

from backend.alignment_api import PageSpec
from backend.structural_anchor_alignment import Settings, align_structural_anchors
from phase1_pdf import PDFInputError


class LineSettings(BaseModel):
    normal_tolerance_ref_px: float = Field(default=3, ge=.1, le=30, allow_inf_nan=False)
    angle_tolerance_degrees: float = Field(default=1, ge=.01, le=10, allow_inf_nan=False)
    support_radius_native_px: float = Field(default=2, ge=0, le=8, allow_inf_nan=False)
    ink_threshold: int = Field(default=160, ge=1, le=254, strict=True)
    minimum_visible_support: float = Field(default=.70, ge=.1, le=1, allow_inf_nan=False)
    minimum_axis_span_fraction: float = Field(default=.15, ge=.01, le=1, allow_inf_nan=False)
    minimum_normal_eigen_ratio: float = Field(default=.05, ge=.001, le=1, allow_inf_nan=False)
    maximum_condition_number: float = Field(default=10000, ge=1, le=1000000, allow_inf_nan=False)
    maximum_rotation_degrees: float = Field(default=15, ge=.1, le=80, allow_inf_nan=False)
    minimum_rotation_concentration: float = Field(default=.90, ge=.5, le=1, allow_inf_nan=False)


class ContextUpdate(BaseModel):
    version: int = Field(ge=1, strict=True)
    input_token: str = Field(min_length=1, max_length=80)


class LineRequest(ContextUpdate):
    context_id: str = Field(min_length=1, max_length=80)
    ref: PageSpec
    target: PageSpec
    target_roi: dict
    anchor_file: dict
    settings: LineSettings = Field(default_factory=LineSettings)
    execution_context: Literal['USER_WORKFLOW', 'TEST_ONLY'] = 'USER_WORKFLOW'


class LineReview(ContextUpdate):
    decision: Literal['approve', 'reject']
    reason: str = Field(min_length=3, max_length=2000)
    sampled_line_scope_confirmed: bool = False


def register_structural_anchor_routes(app, documents):
    contexts, runs = OrderedDict(), OrderedDict()
    app.state.structural_alignments = runs
    app.state.structural_alignment_contexts = contexts

    def invalidate(entry, why):
        r = entry['result']
        if r['validity'] == 'INVALIDATED': return
        r['previous_status'] = r['status']; r['status'] = 'DEFERRED'; r['validity'] = 'INVALIDATED'
        r['review_history'].append(dict(r['review']))
        r['review'] = {'state': 'INVALIDATED', 'reason': why, 'scope': 'sampled_line_constraints_only'}
        r['matrix'] = r['inverse_matrix'] = r['provisional_mapping'] = None
        r['approval_eligible'] = False; r['reasons'].append(why)
        entry['previews'] = {}; r['preview_modes'] = []

    def check_context(context_id, version, token):
        current = contexts.get(context_id)
        if current is None or current != (version, token):
            raise HTTPException(409, 'Anchor·ROI·설정 문맥이 변경되거나 만료됐습니다. 다시 검증하세요.')

    def get(run_id):
        if run_id not in runs: raise HTTPException(410, '구간 검증 결과가 만료됐습니다. 다시 실행하세요.')
        entry = runs[run_id]
        for document_id in entry['document_ids']: documents.get(document_id)
        if contexts.get(entry['context_id']) != (entry['version'], entry['input_token']):
            invalidate(entry, 'Anchor·ROI·설정 변경으로 검증과 승인을 무효화했습니다.')
        return entry

    @app.put('/api/structural-alignment-contexts/{context_id}')
    async def update(context_id: str, body: ContextUpdate):
        if not 1 <= len(context_id) <= 80: raise HTTPException(422, '문맥 ID가 잘못됐습니다.')
        old = contexts.get(context_id)
        if old and (body.version < old[0] or (body.version == old[0] and body.input_token != old[1])):
            raise HTTPException(409, '이전 문맥 갱신은 적용하지 않습니다.')
        contexts[context_id] = (body.version, body.input_token); contexts.move_to_end(context_id)
        while len(contexts) > 64: contexts.popitem(last=False)
        for e in runs.values():
            if contexts.get(e['context_id']) != (e['version'], e['input_token']): invalidate(e, '입력 문맥이 변경되거나 만료됐습니다.')
        return {'version': body.version, 'input_token': body.input_token}

    @app.post('/api/structural-alignments', status_code=201)
    async def run(body: LineRequest):
        check_context(body.context_id, body.version, body.input_token)
        if len(json.dumps(body.anchor_file, ensure_ascii=False).encode()) > 2*1024*1024:
            raise HTTPException(413, 'Anchor JSON은 2MiB 이하여야 합니다.')
        try:
            pages = [documents.page(documents.get(s.document_id), s.page, s.zoom) for s in [body.ref, body.target]]
            result, previews = align_structural_anchors(*pages, body.target_roi, body.anchor_file, Settings(**body.settings.model_dump()))
        except (ValueError, PDFInputError, TypeError, KeyError) as exc:
            raise HTTPException(422, str(exc)) from exc
        check_context(body.context_id, body.version, body.input_token)
        # Test annotations never silently become production approval records.
        annotated_test = any('TEST ONLY' in str(p.get('rationale', '')).upper() or 'TEST_ONLY' in str(p.get('rationale', '')).upper() for p in body.anchor_file.get('pairs', []))
        result['execution_context'] = 'TEST_ONLY' if annotated_test or body.anchor_file.get('test_only') is True else body.execution_context
        result['run_id'] = uuid4().hex; result['context_id'] = body.context_id; result['context_version'] = body.version
        result['created_at'] = datetime.now(timezone.utc).isoformat(); result['review_history'] = []
        result['preview_modes'] = list(previews)
        result['ref_render_sha256'] = hashlib.sha256(pages[0].png).hexdigest()
        result['target_render_sha256'] = hashlib.sha256(pages[1].png).hexdigest()
        size = sum(map(len, previews.values()))
        if size > 32*1024*1024: raise HTTPException(413, '구간 검증 미리보기가 너무 큽니다.')
        while runs and (len(runs) >= 6 or sum(len(p) for e in runs.values() for p in e['previews'].values())+size > 32*1024*1024): runs.popitem(last=False)
        runs[result['run_id']] = {'result': result, 'previews': previews, 'document_ids': [body.ref.document_id, body.target.document_id],
                                  'context_id': body.context_id, 'version': body.version, 'input_token': body.input_token}
        return result

    @app.get('/api/structural-alignments/{run_id}')
    async def metadata(run_id: str): return get(run_id)['result']

    @app.get('/api/structural-alignments/{run_id}/preview/{mode}')
    async def preview(run_id: str, mode: str):
        e = get(run_id); png = e['previews'].get(mode)
        if png is None: raise HTTPException(404, '현재 입력 문맥의 미리보기가 없습니다.')
        return Response(png, media_type='image/png', headers={'Cache-Control': 'no-store'})

    @app.post('/api/structural-alignments/{run_id}/review')
    async def review(run_id: str, body: LineReview):
        e = get(run_id); r = e['result']; check_context(e['context_id'], body.version, body.input_token)
        if r['validity'] != 'CURRENT' or body.version != e['version'] or body.input_token != e['input_token']:
            raise HTTPException(409, '이전 검증의 승인은 무효입니다.')
        if len(body.reason.strip()) < 3: raise HTTPException(422, '검토 근거를 입력하세요.')
        if body.decision == 'approve':
            if r['status'] != 'VERIFIED_SAMPLED' or not r['approval_eligible'] or not body.sampled_line_scope_confirmed:
                raise HTTPException(409, '독립 구간 검증과 표본 범위 확인이 충족되지 않았습니다.')
            state = 'TEST_APPROVED_SAMPLED' if r['execution_context'] == 'TEST_ONLY' else 'APPROVED_SAMPLED'
        else: state = 'REJECTED'
        r['review'] = {'state': state, 'reason': body.reason.strip(), 'scope': 'sampled_line_constraints_only',
                       'at': datetime.now(timezone.utc).isoformat(), 'roi_mapping_confirmed': False}
        r['review_history'].append(dict(r['review'])); r['review_history'] = r['review_history'][-32:]
        return r
