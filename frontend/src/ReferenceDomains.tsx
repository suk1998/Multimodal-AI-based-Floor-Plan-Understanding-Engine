export interface Mask {
 pixel_count:number;components:number;holes:number;rects_native_xyxy:[number,number,number,number][];
}
export interface ReferenceDomains {
 frame_id:string;status:string;reason:string;layers:Partial<Record<string,Mask>>;
 open_plan?:{status:string;reason:string;labels:{name:string}[];shared_region:Mask|null};
}
const titles:Record<string,string>={U:'세대 내부 후보',W:'벽체 점유 후보',C:'제외 코어',F:'물리적 자유 영역',P:'배정된 구획',R:'미배정 영역',unknown_coverage:'포함 보류 영역',shared:'공유 구획 후보'};
const colors:Record<string,string>={U:'#0762ac',W:'#b73729',C:'#864ab1',F:'#00778e',P:'#276c3a',R:'#b87504',unknown_coverage:'#595563',shared:'#00765c'};
const reasons:Record<string,string>={
 DOMAIN_RASTER_WORK_BUDGET:'계산 한도를 넘었습니다. 하나의 도면 범위를 선택해 다시 분석하세요.',
 NO_BOUNDED_MULTI_LABEL_ENVELOPE:'여러 공간명이 들어가는 닫힌 세대 외곽을 찾지 못했습니다.',
 COMPETING_DWELLING_ENVELOPES:'둘 이상의 세대 범위를 구분하기 어렵습니다. 하나의 도면 범위를 선택하세요.',
 MISSING_OR_AMBIGUOUS_FUNCTION_LABELS:'거실과 주방/식당 라벨 두 개를 각각 한 곳에 연결할 수 없어 공유 구획을 보류했습니다.',
 SEPARATE_PARTITIONS_OR_SEEDS_OUTSIDE_RESIDUAL:'두 라벨이 같은 잔여 구획 안에 있지 않습니다.',
 OTHER_ROOM_LABEL_IN_SHARED_CELL:'같은 구획에 다른 공간명도 있어 공유 구획을 보류했습니다.',
 OPENING_OR_DOMAIN_BREAK_BETWEEN_LABELS:'개구부 후보 또는 영역 단절이 있어 공유 구획을 보류했습니다.',
 INSUFFICIENT_DIRECT_CLEARANCE:'두 라벨 사이에 넓고 연속된 통행 공간이 관찰되지 않았습니다.'
};
function layer(result:ReferenceDomains,key:string){
 return key==='shared'?result.open_plan?.shared_region:result.layers[key];
}
export function DomainReview({result,selected,onSelect}:{result:ReferenceDomains;selected:string;onSelect:(key:string)=>void}){
 const mask=layer(result,selected);
 return <section aria-label="세대 영역과 OpenPlan">
  <h3>세대 영역 · 조건부 결과</h3>
  {result.status==='HOLD'?<p>{reasons[result.reason]||'이 분석 범위에서는 세대 영역을 계산할 수 없습니다.'}</p>:<>
   <label>표시할 영역 <select aria-label="세대 영역 표시" value={selected} onChange={event=>onSelect(event.target.value)}>
    {Object.keys(titles).filter(key=>layer(result,key)).map(key=><option key={key} value={key}>{titles[key]}</option>)}
   </select></label>
   {mask&&<p>{mask.pixel_count.toLocaleString()} 원본 픽셀 · 연결 성분 {mask.components}개 · 구멍 {mask.holes}개</p>}
   <p>미배정 영역에는 통행부, 아직 복원하지 못한 방과 벽체가 포함될 수 있습니다.</p>
   <p>기능 라벨 · {result.open_plan?.labels.map(row=>row.name).join(', ')||'관찰 없음'}</p>
   <p>{result.open_plan?.status==='PROPOSED'?'두 기능 라벨 사이에 넓게 열린 경로가 관찰되어 공유 구획을 제안합니다.':reasons[result.open_plan?.reason||'']}</p>
  </>}
 </section>
}
export function DomainOverlay({result,selected}:{result:ReferenceDomains;selected:string}){
 const mask=layer(result,selected);
 if(!mask)return null;
 const path=mask.rects_native_xyxy.map(([x,y,right,bottom])=>'M'+(x-.5)+' '+(y-.5)+'h'+(right-x)+'v'+(bottom-y)+'h'+(x-right)+'z').join('');
 return <path data-domain-layer={selected} data-frame-id={result.frame_id} pointerEvents="none" fill={colors[selected]} fillOpacity={0.38} d={path}/>;
}
