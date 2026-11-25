import {DomainOverlay,type Mask} from './ReferenceDomains';
import type {FocusRegion} from './DrawingViewer';
import type {Vertex} from './roi';

interface ReviewCase {
 case_id:string;name:string;kind:string;bounds_native:FocusRegion['bounds'];
 analysis_state:string;review_state:string;finding:string;inferred_run_count?:number;
 polygon_native?:{outer_rings:Vertex[][];holes:Vertex[][]}|null;
 boundary_runs?:{points_native:Vertex[];owner:number}[];
 shared_region?:Mask|null;segment_native?:Vertex[];bbox_native?:FocusRegion['bounds'];
}
export interface ReferenceModel {
 frame_id:string;status:string;review_cases:ReviewCase[];
 reference_audit:{status:string;unresolved_relation_refs:unknown[]};
 gate:{status:string;criteria:{id:string;label:string;candidate_evidence_available:boolean;review_status:string}[]};
}
const findings:Record<string,string>={
 CONDITIONAL_POLYGON_WITH_INFERRED_SEGMENTS:'경계 일부를 연결해 복원',
 CONDITIONAL_POLYGON:'관찰 경계에서 복원',
 PARTIAL_OR_UNKNOWN_BOUNDARY:'경계 복원 미완료',
 HOST_OR_OPENING_UNRESOLVED:'벽과 출입구 관계 미해결',
 TWO_NAMED_SIDES_NOT_GROUNDED:'출입구 양쪽 공간 미해결',
 CODE_INSIDE_MARKER_WITHOUT_TRACED_INSTANCE_REFERENCE:'기호를 읽음 · 실제 창호 지시 미해결',
 DISTINCT_LABELS_IN_ONE_PARTITION_WITH_BROAD_CLEAR_PATH:'공유 구획 후보',
 OTHER_ROOM_LABEL_IN_SHARED_CELL:'여러 공간명이 같은 구획에 남음'
};
export function ModelReview({result,selected,onSelect,onFocus,show,onShow,role="Ref"}:{role?:'Ref'|'Target';result:ReferenceModel;selected:string;onSelect:(id:string)=>void;onFocus:(f:FocusRegion)=>void;show:boolean;onShow:(value:boolean)=>void}){
 const row=result.review_cases.find(c=>c.case_id===selected);
 return <section aria-label="Reference 이해 통합 검토">
  <h3>{role} 이해 검토</h3>
  <p>원본에서 확인할 공간과 관계를 선택하세요. 현재 후보의 독립 검토가 필요합니다.</p>
  <label>검토할 위치 <select aria-label="Reference 이해 사례" value={row?.case_id||''} onChange={event=>{
   const next=result.review_cases.find(c=>c.case_id===event.target.value);
   if(next){onSelect(next.case_id);onFocus({key:next.case_id+Date.now(),bounds:next.bounds_native})}
  }}><option value="">위치 선택</option>{result.review_cases.map((c,i)=><option key={c.case_id} value={c.case_id}>{i+1}. {c.name}</option>)}</select></label>
  <label><input type="checkbox" aria-label="통합 모델 후보 표시" checked={show} onChange={e=>onShow(e.target.checked)}/>후보 표시</label>
  {row&&<p data-model-finding={row.finding}>{findings[row.finding]||'원본 근거와 해석 확인 필요'} · {row.analysis_state==='PROPOSED'?'미검증 후보':row.analysis_state==='SUPPORTED'?'근거 지지':'판단 보류'}</p>}
  <details><summary>확인 범위와 남은 조건</summary>
   <p>통합 참조 검사 · {result.reference_audit.status==='RESOLVED'?'근거 연결됨':'미해결 참조 있음'}</p>
   {result.gate.criteria.map(c=><p key={c.id}>{c.label} · {c.candidate_evidence_available?'후보 근거 있음 · 독립 검토 대기':'추가 근거 필요'}</p>)}
  </details>
 </section>
}
export function ModelOverlay({result,selected}:{result:ReferenceModel;selected:string}){
 const row=result.review_cases.find(c=>c.case_id===selected);
 return <g data-reference-model={result.frame_id} data-selected-case={row?.case_id} pointerEvents="none">
  {row?.shared_region&&<DomainOverlay selected="shared" result={{frame_id:result.frame_id,status:"PARTIAL",reason:row.finding,layers:{},open_plan:{status:"PROPOSED",reason:row.finding,labels:[],shared_region:row.shared_region}}}/>}
  {row?.polygon_native&&<path d={[...row.polygon_native.outer_rings,...row.polygon_native.holes].map(r=>'M'+r.map(p=>p.join(',')).join(' L')+'Z').join(' ')} fill="#087c6933" fillRule="evenodd"/>}
  {row?.boundary_runs?.map((b,i)=><polyline key={i} points={b.points_native.map(p=>p.join(',')).join(' ')} fill="none" stroke={b.owner<0?'#b55a08':'#087c69'} strokeDasharray={b.owner<0?'6 3':undefined} strokeWidth={2} vectorEffect="non-scaling-stroke"/>)}
  {row?.segment_native&&<polyline points={row.segment_native.map(p=>p.join(',')).join(' ')} fill="none" stroke="#b55a08" strokeWidth={3} vectorEffect="non-scaling-stroke"/>}
  {row?.bbox_native&&<rect x={row.bbox_native[0]} y={row.bbox_native[1]} width={row.bbox_native[2]-row.bbox_native[0]} height={row.bbox_native[3]-row.bbox_native[1]} fill="none" stroke="#694aa3" strokeWidth={2} vectorEffect="non-scaling-stroke"/>}
 </g>
}
