import {DomainOverlay,type Mask} from './ReferenceDomains';
import type {FocusRegion} from './DrawingViewer';
import type {Vertex} from './roi';

type Bounds=FocusRegion['bounds'];
interface FunctionalReview {
 reason:string;
 before?:{reason:string};
 after?:{status:string;reason:string;shared_region?:Mask|null;labels?:{name:string}[]}|null;
 competing_interpretation?:{status:string;reason:string};
 observations:{id:string;normalized:string;bbox_native:Bounds}[];
}
interface DoorReview {
 id:string;window_native:Bounds;segment_native:Vertex[];
 sides:{space_name:string|null;probes:({point_native:Vertex}|null)[]}[];
 after:{status:string;reason:string};
}
interface CodeReview {
 id:string;code:string;bbox_native:Bounds;window_native:Bounds;
}
export interface ReferenceRelations {
 frame_id:string;status:string;functional_review?:FunctionalReview;
 door_reviews:DoorReview[];code_reviews:CodeReview[];summary?:Record<string,number>;
}
const reasons:Record<string,string>={
 OPENING_OR_DOMAIN_BREAK_BETWEEN_LABELS:'라벨 사이의 연속 공간 근거가 부족합니다.',
 OTHER_ROOM_LABEL_IN_SHARED_CELL:'같은 구획에 다른 공간명도 들어 있습니다.',
 MISSING_OR_AMBIGUOUS_FUNCTION_LABELS:'두 기능의 공간명이나 위치가 부족합니다.',
 SEPARATE_PARTITIONS_OR_SEEDS_OUTSIDE_RESIDUAL:'두 라벨이 같은 미배정 구획 안에 있지 않습니다.',
 INSUFFICIENT_DIRECT_CLEARANCE:'두 라벨을 잇는 넓고 직접적인 경로가 확인되지 않았습니다.',
 HOST_OR_OPENING_UNRESOLVED:'벽과 개구부의 관계 보류',
 TWO_NAMED_SIDES_NOT_GROUNDED:'양쪽 공간 식별 보류',
 SWING_OR_CROSSING_UNRESOLVED:'문 여닫이 또는 통과 경로 보류',
 TWO_SPACES_AND_CLEAR_CROSSING:'양쪽 공간과 통과 경로 후보 지지'
};
const pointString=(points:Vertex[])=>points.map(p=>p.join(',')).join(' ');
const stroke=(color:string)=>({stroke:color,strokeWidth:2,fill:'none',vectorEffect:'non-scaling-stroke' as const});

export function RelationReview({result,onFocus}:{result:ReferenceRelations;onFocus:(f:FocusRegion)=>void}){
 const functional=result.functional_review;
 const focus=(key:string,bounds:Bounds)=>onFocus({key:key+':'+Date.now(),bounds});
 return <section aria-label="관계 Grounding">
  <h3>관계와 국소 재관찰</h3>
  {result.status==='HOLD'?<p>공간 복원 근거가 부족해 관계 검토를 보류했습니다.</p>:<>
   <p>출입 연결 지지 {result.summary?.Connection??0}건 · 창호 기호 {result.code_reviews.length}개 · 벽면과 부모 벽 후보 {result.summary?.BoundaryOfElement??0}건</p>
   {functional?.observations.map(o=><button key={o.id} onClick={()=>focus(o.id,[o.bbox_native[0]-45,o.bbox_native[1]-45,o.bbox_native[2]+45,o.bbox_native[3]+45])}>{o.normalized} 재관찰 위치</button>)}
   {functional?.after&&<p data-functional-review={functional.after.reason}>{functional.after.status==='PROPOSED'?'거실과 주방/식당의 공유 구획 후보를 청록색으로 표시합니다.':reasons[functional.after.reason]||'공유 구획 판정 보류'}</p>}
   {functional?.after?.status==='PROPOSED'&&functional.competing_interpretation?.status==='HOLD'&&
    <p>불확실한 개구부 후보까지 경계로 넣으면 공유 구획 판정이 보류됩니다.</p>}
   {result.door_reviews.map((r,i)=><div key={r.id}><button onClick={()=>focus(r.id,r.window_native)}>
    출입구 {i+1} · {r.sides.map(s=>s.space_name||'미식별').join(' ↔ ')}
   </button> <span>{reasons[r.after.reason]}</span></div>)}
   {result.code_reviews.length>0&&<details><summary>창호 기호 찾아보기</summary>
    <p>원 안 글자와 번호를 읽었습니다. 실제 창호에 대한 지시 관계는 보류 중입니다.</p>
    <div className="reference-space-list">{result.code_reviews.map(r=><button key={r.id} onClick={()=>focus(r.id,r.window_native)}>{r.code}</button>)}</div>
   </details>}
  </>}
 </section>
}

export function RelationOverlay({result}:{result:ReferenceRelations}){
 const inferred=result.functional_review?.after;
 return <g data-reference-relations={result.frame_id} pointerEvents="none">
  {inferred?.shared_region&&<DomainOverlay selected="shared" result={{frame_id:result.frame_id,status:inferred.status,reason:inferred.reason,layers:{},open_plan:{...inferred,labels:inferred.labels??[],shared_region:inferred.shared_region}}}/>}
  {result.functional_review?.observations.map(o=><rect key={o.id} x={o.bbox_native[0]} y={o.bbox_native[1]}
   width={o.bbox_native[2]-o.bbox_native[0]} height={o.bbox_native[3]-o.bbox_native[1]} {...stroke('#007758')}/>)}
  {result.door_reviews.map(r=><g key={r.id} data-review-state={r.after.status}><title>{reasons[r.after.reason]}</title>
   <polyline points={pointString(r.segment_native)} {...stroke(r.after.status==='SUPPORTED'?'#007758':'#b05307')} strokeDasharray="6 3"/>
   {r.sides.flatMap(s=>s.probes).map((probe,i)=>probe&&<circle key={i} cx={probe.point_native[0]} cy={probe.point_native[1]} r={3} fill="#b05307"/>)}
  </g>)}
  {result.code_reviews.map(r=><ellipse key={r.id} cx={(r.bbox_native[0]+r.bbox_native[2])/2} cy={(r.bbox_native[1]+r.bbox_native[3])/2}
   rx={(r.bbox_native[2]-r.bbox_native[0])/2+2} ry={(r.bbox_native[3]-r.bbox_native[1])/2+2} {...stroke('#6647a4')}><title>{r.code}</title></ellipse>)}
 </g>;
}
