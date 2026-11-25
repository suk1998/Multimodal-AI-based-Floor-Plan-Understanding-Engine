import {useEffect, useRef, useState} from 'react';
import type {DrawingDocument} from './api';
import type {FocusRegion} from './DrawingViewer';
import type {TargetROI} from './roi';
import {isFinalChangeKind,type FinalChangeKind} from './finalChangeEligibility';

type Role = 'Ref' | 'Target';
type Bounds = [number, number, number, number];
type Decision = 'CONFIRMED' | 'DISMISSED';
type Decisions = Record<string, Decision>;
interface Observation {id: string; kind: string; name: string | null; bbox: Bounds}
interface Finding extends FinalChangeKind {
  correspondence_id: string;
  entity_type: string;
  ref_ids: string[];
  target_ids: string[];
  identity_status: string;
  source_observations: {description: string; readings?: string[]; labels?: string[]; observed_spans_native_px?: number[]} | null;
  judgment: string;
  domain: string;
  automatic_confirmation?: string;
  alignment_fit_parent?: boolean;
  change_description?: string;
  space_name?: string | null;
  verified_target_bbox_native?: Bounds | null;
  measurements?: {span_delta_ref_px?:number|null};
  decision_reason: string;
}

// Describe only the measured facet. A shifted wall end does not establish a
// moved whole wall, changed room connection or a real-world distance in mm.
function findingDescription(row:Finding) {
  if(row.automatic_confirmation!=='VECTOR_WALL_END_WITH_INDEPENDENT_RASTER_SUPPORT')
    return row.change_description ?? row.decision_reason;
  const nearby=row.space_name?.replace(/\s*\(Ref 경계 인접\)$/, ' 인근');
  const prefix=nearby?nearby+'의 ':'';
  const length=row.measurements?.span_delta_ref_px;
  return prefix+'벽 끝선의 위치가 변경되었습니다.'+
    (typeof length==='number'&&Number.isFinite(length)&&length!==0
      ?' 확인한 벽 구간은 이전보다 '+(length<0?'짧아졌습니다.':'길어졌습니다.'):'');
}
interface Comparison {
  run_id: string;
  input_fingerprint: string;
  source_objects: Record<Role, Observation[]>;
  sources: {frame_id: string; image: TargetROI['image']; geometry_run_id?: string}[];
  target_roi: TargetROI;
  alignment_status: string;
  alignment_diagnostics?: {
    reasons?: string[];
    wall_registration?: {independent_max_normal_ref_px?: number} | null;
  };
  coverage?: {fraction:number} | null;
  correspondences: Finding[];
  judgments: Record<string, number>;
  status: string;
  review_revision?: number;
  review_decisions?: Decisions;
}
export interface SavedComparison {
  comparison: Comparison;
  documents: DrawingDocument[];
  revision: number;
  decisions: Decisions;
}
export interface ComparisonListing {run_id: string; documents: DrawingDocument[]; pages: number[]}
const meaning: Record<string, string> = {CHANGED:'변경 후보', UNCHANGED_WITHIN_SCOPE:'측정 범위 내 일치', UNDETERMINED:'판단 보류'};
const kind: Record<string, string> = {Space:'공간', Wall:'벽 구간', Door:'문', Opening:'개구부', Window:'창문', SlidingDoor:'미닫이문', WindowOrSlidingDoor:'창문 또는 미닫이문', CodeMarker:'도면 기호',DimensionText:'치수 표기'};
const identity: Record<string, string> = {SUPPORTED:'대응 근거 확보', SUPPORTED_AT_LOCUS:'해당 위치의 상태 근거 확보', PROPOSED:'동일 객체 후보', GROUP_HYPOTHESIS:'그룹 대응 후보', AMBIGUOUS:'여러 대응 가능', UNMATCHED:'대응 미확인', UNASSESSED:'미평가'};
export const reviewPath = '/api/comparison-reviews';

export async function reviewRequest(url: string, options?: RequestInit) {
  const reply = await fetch(url, options);
  if (reply.ok) return reply;
  const data = await reply.json().catch(() => ({}));
  throw Error(typeof data.detail === 'string' ? data.detail : '검토 요청을 완료하지 못했습니다.');
}

function download(data: Blob, filename: string) {
  const url = URL.createObjectURL(data);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function useObjectComparison(body: string, fingerprint: string | undefined, restored: SavedComparison | null) {
  const [fresh, setFresh] = useState<Comparison | null>(null);
  const [selected, setSelected] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [baseline, setBaseline] = useState({revision:0, decisions:{} as Decisions});
  const [draft, setDraft] = useState<Decisions>({});
  const request = useRef<AbortController | null>(null);

  const source = restored?.comparison ?? (fresh?.input_fingerprint === fingerprint ? fresh : null);
  useEffect(() => {
    setFresh(null);
    setSelected('');
    setBusy(false);
    setError('');
    return () => {request.current?.abort(); request.current = null;};
  }, [body, fingerprint, restored]);

  useEffect(() => {
    const decisions = restored?.decisions ?? source?.review_decisions ?? {};
    setBaseline({revision:restored?.revision ?? source?.review_revision ?? 0, decisions});
    setDraft(decisions);
  }, [source, restored]);

  const dirty = Object.keys(baseline.decisions).length !== Object.keys(draft).length
    || Object.entries(draft).some(([id, state]) => baseline.decisions[id] !== state);
  useEffect(() => {
    if (!source || !dirty) return;
    const warn = (event: BeforeUnloadEvent) => event.preventDefault();
    window.addEventListener('beforeunload', warn);
    return () => window.removeEventListener('beforeunload', warn);
  }, [source, dirty]);
  const row = source?.correspondences.find(r => r.correspondence_id === selected);
  const confirmed = source?.correspondences.filter(r =>
    ['VERIFIED_ROI','PARTIAL'].includes(source.alignment_status) &&
    r.judgment === 'CHANGED' && !r.alignment_fit_parent &&
    isFinalChangeKind(r) && r.verified_target_bbox_native &&
    (baseline.decisions[r.correspondence_id] ?? (r.automatic_confirmation ? 'CONFIRMED' : 'REVIEW')) === 'CONFIRMED') ?? [];
  const confirmedIds = new Set(confirmed.map(r=>r.correspondence_id));
  const lookup = {
    Ref: new Map(source?.source_objects.Ref.map(o => [o.id, o])),
    Target: new Map(source?.source_objects.Target.map(o => [o.id, o]))
  };
  function locations(f: Finding, side: Role): Bounds[] {
    const ids = side === 'Ref' ? f.ref_ids : f.target_ids;
    const boxes = ids.flatMap(id => {
      const object = lookup[side].get(id);
      return object ? [object.bbox] : [];
    });
    if (side === 'Target' && f.verified_target_bbox_native) return [f.verified_target_bbox_native];
    return boxes;
  }
  function focus(side: Role): FocusRegion | undefined {
    if(source&&selected==='__all__'){
      const image=source.sources[side==='Ref'?0:1].image;
      return {key:source.run_id+':all',bounds:[0,0,image.width,image.height]};
    }
    const boxes = row && locations(row, side);
    if (!boxes?.length) return;
    const image = source!.sources[side === 'Ref' ? 0 : 1].image;
    const context = 38 * image.extraction_zoom;
    return {key:source!.run_id + ':' + selected, bounds:[
      Math.max(0, Math.min(...boxes.map(b=>b[0])) - context),
      Math.max(0, Math.min(...boxes.map(b=>b[1])) - context),
      Math.min(image.width, Math.max(...boxes.map(b=>b[2])) + context),
      Math.min(image.height, Math.max(...boxes.map(b=>b[3])) + context)
    ]};
  }
  function overlay(side: Role) {
    return <>
      {row && !confirmedIds.has(row.correspondence_id) && <g data-comparison-location={side} pointerEvents="none">{locations(row, side).map(([x,y,u,v], i) =>
        <rect key={i} x={x-2} y={y-2} width={u-x+4} height={v-y+4} stroke="#a04f0c" strokeWidth="2"
          strokeDasharray="5 3" vectorEffect="non-scaling-stroke" fill="none"/>)}</g>}
      {confirmed.length>0 && <g data-confirmed-changes={side} pointerEvents="none">{confirmed.map((r,index) => {
        const boxes=locations(r,side);
        if(!boxes.length)return null; // Missing counterpart is not a fabricated location.
        const color=side==='Ref'?'#1765c1':'#c03938';
        return <g key={r.correspondence_id} data-change-number={index+1} data-change-id={r.correspondence_id} fill={color}>
          {boxes.map(([x,y,u,v],i)=><g key={i}>
            <rect x={x-4} y={y-4} width={u-x+8} height={v-y+8} stroke={color}
              strokeWidth={r.correspondence_id===selected?'4':'2'} vectorEffect="non-scaling-stroke" fill="none"/>
            <text x={x} y={Math.max(16,y-8)} style={{fontSize:'calc(13px / var(--viewport-scale,1))'}}
              stroke="white" strokeWidth="3" paintOrder="stroke" strokeLinejoin="round" vectorEffect="non-scaling-stroke">{index+1}</text>
          </g>)}
        </g>;
      })}</g>}
    </>;
  }

  async function perform(action: (signal: AbortSignal) => Promise<void>) {
    if (request.current) return;
    const controller = new AbortController();
    request.current = controller;
    setBusy(true);
    setError('');
    try { await action(controller.signal); }
    catch (e) { if (!controller.signal.aborted) setError(e instanceof Error ? e.message : '요청 실패'); }
    finally { if (request.current === controller) {request.current = null; setBusy(false);} }
  }
  function run() {
    return perform(async signal => {
      const reply = await reviewRequest('/api/structural-comparisons', {
        method:'POST', headers:{'Content-Type':'application/json'}, body, signal
      });
      const data: Comparison = await reply.json();
      if (data.input_fingerprint !== fingerprint) throw Error('이전 도면 또는 비교 범위의 결과입니다.');
      if (!signal.aborted) {setFresh(data); setSelected('');}
    });
  }
  function save() {
    if (!source) return;
    return perform(async signal => {
      const reply = await reviewRequest(reviewPath + '/' + source.run_id, {
        method:'PUT', signal, headers:{'Content-Type':'application/json'},
        body:JSON.stringify({revision:baseline.revision, decisions:draft})
      });
      const data: SavedComparison = await reply.json();
      if (!signal.aborted) {
        setBaseline({revision:data.revision, decisions:data.decisions});
        setDraft(data.decisions);
      }
    });
  }
  function exportFiles(format: 'csv'|'png') {
    if (!source || dirty || !baseline.revision) return;
    return perform(async signal => {
      const reply = await reviewRequest(reviewPath + '/' + source.run_id + '/export?revision=' + baseline.revision + '&format=' + format, {signal});
      const blob = await reply.blob();
      if (!signal.aborted) download(blob, {csv:'change_report.csv',png:'target_changes.png'}[format]);
    });
  }

  const panel = (fingerprint || restored) && <section aria-label="객체 대응 및 변경 판단">
    <h3>객체 대응과 변경 판단</h3>
    {!restored && !source && <button disabled={busy} onClick={()=>void run()}>{busy?'비교 중…':'객체 대응·변경 분석'}</button>}
    {error && <p role="alert">{error}</p>}
    {source && <div data-comparison-status={source.status}>
      <p role="status">{source.status === 'UNDETERMINED' ? '현재 근거로 실제 변경 여부를 판단할 수 없습니다.' : '원본 위치에서 변경 후보를 검토하세요.'}</p>
      <p>{Object.entries(source.judgments).map(([status,n]) => meaning[status]+' '+n+'건').join(' · ')}</p>
      <label>검토 사례 <select value={selected} onChange={e=>setSelected(e.target.value)}><option value="">사례 선택</option>
        {source.correspondences.map(r => {
          const object = lookup.Target.get(r.target_ids[0]) ?? lookup.Ref.get(r.ref_ids[0]);
          const review = draft[r.correspondence_id] ?? (r.automatic_confirmation ? 'CONFIRMED' : 'REVIEW');
          return <option key={r.correspondence_id} value={r.correspondence_id}>
            {object?.name || kind[r.entity_type]} · {meaning[r.judgment]}{review === 'CONFIRMED' ? baseline.decisions[r.correspondence_id] === 'CONFIRMED' ? ' · 확정 저장됨' : ' · 확정 선택' : review === 'DISMISSED' ? ' · 제외' : ''}
          </option>;
        })}
      </select></label>
      {row && <>
        <p>{identity[row.identity_status]} · {row.decision_reason}</p>
        <p><a href="#fpue-drawings">도면에서 선택 위치 보기</a></p>
        {row.source_observations && <p>
          {(row.source_observations.readings || row.source_observations.labels || row.source_observations.observed_spans_native_px?.map(v=>v.toFixed(2)+' px'))?.join(' → ')} · {row.source_observations.description}
        </p>}
        <label>검토 결정 <select value={draft[selected] ?? (row.automatic_confirmation ? 'CONFIRMED' : 'REVIEW')} disabled={busy} onChange={e=>{
          const decision = e.target.value as Decision | 'REVIEW';
          setDraft(previous => {
            const next = {...previous};
            if (decision === 'REVIEW') delete next[selected];
            else next[selected] = decision;
            return next;
          });
        }}>
          <option value="REVIEW">{row.automatic_confirmation ? '자동 판정 적용' : '보류'}</option>
          {row.judgment === 'CHANGED' && row.verified_target_bbox_native && <option value="CONFIRMED">변경 확정</option>}
          <option value="DISMISSED">결과에서 제외</option>
        </select></label>
        <details><summary>측정 범위와 근거</summary><pre>{JSON.stringify(row,null,2)}</pre></details>
      </>}
      <p role="status" data-review-revision={baseline.revision}>
        {dirty ? '저장하지 않은 검토 결정이 있습니다.' : baseline.revision ? '검토 저장됨' : '검토 초안'}
      </p>
      <button disabled={busy || (!dirty && baseline.revision>0)} onClick={()=>void save()}>검토 저장</button>{' '}
      <details><summary>비교 데이터</summary>
        <button onClick={()=>download(new Blob([JSON.stringify(source,null,2)],{type:'application/json'}),'comparison.json')}>비교 판단 JSON 저장</button>
      </details>
    </div>}
  </section>;
  const summary = source && <section className="observation-panel" aria-label="비교 분석 결과">
    <h2>변경 결과</h2>
    <p role="status" data-final-comparison={source.alignment_status}>
      {confirmed.length ? '변경 사항 '+confirmed.length+'건을 찾았습니다.' :
        source.status === 'UNDETERMINED' || source.alignment_status !== 'VERIFIED_ROI'
          ? '두 도면의 변경을 확정하지 못했습니다. 위치 대응이나 비교 근거가 부족합니다.'
          : '비교한 범위에서 변경을 발견하지 못했습니다.'}
    </p>
    {(source.alignment_status!=='VERIFIED_ROI'||source.status==='UNDETERMINED')&&<p className="hint">일부 구역은 판단할 근거가 부족합니다. 표시되지 않은 곳이 모두 변경 없는 곳이라는 뜻은 아닙니다.</p>}
    {!!confirmed.length&&<><p className="change-legend"><span className="before">Reference · 파랑: 변경 전</span> / <span className="after">Target · 빨강: 변경 후</span> · 같은 번호는 하나의 변경입니다.</p>
      <button onClick={()=>setSelected('__all__')}>전체 변경 보기</button></>}
    {!!confirmed.length && <ul className="change-list">{confirmed.map((r,index) => <li key={r.correspondence_id}>
      <button aria-pressed={selected===r.correspondence_id} onClick={()=>{
        setSelected(selected===r.correspondence_id?'__all__':r.correspondence_id);
        document.getElementById('fpue-drawings')?.scrollIntoView({behavior:'smooth',block:'start'});
      }}><span className="change-number">{index+1}</span>
        {findingDescription(r)}
      </button>
    </li>)}</ul>}
    {error && <p role="alert">{error}</p>}
    <button disabled={busy || dirty || !baseline.revision} onClick={()=>void exportFiles('csv')}>변경 CSV 다운로드</button>{' '}
    <button disabled={busy || dirty || !baseline.revision} onClick={()=>void exportFiles('png')}>Target PNG 다운로드</button>
  </section>;
  return {panel, overlay, focus, summary};
}
