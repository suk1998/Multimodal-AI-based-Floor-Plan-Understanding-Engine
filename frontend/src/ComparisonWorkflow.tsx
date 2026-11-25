import type {TargetROI} from "./roi";

// This is the current product capability, not a simulated automatic run.
// A future candidate-selection workflow must provide real evidence before results appear here.
export function ComparisonWorkflow({pagesReady,roi,observationState}:{pagesReady:boolean;roi:TargetROI|null;observationState?:string}){
 const observed=observationState==='COMPLETED'||observationState==='PARTIAL';
 const stale=observationState==='STALE';
 const steps=[
  {name:"PDF·페이지 선택",state:pagesReady?"완료":"선택 필요",done:pagesReady},
  {name:"비교 영역 지정",state:roi?"완료":pagesReady?"영역을 그려 주세요":"도면 준비 후",done:Boolean(roi)},
  {name:observationState?'영역 관찰 생성':'자동 정렬',state:observationState?(stale?'이전 결과 · 재분석 필요':observed?'결과 생성':observationState==='RUNNING'?'실행 중':observationState==='QUEUED'?'대기 중':observationState==='FAILED'?'실패':'실행 전'):'준비 중',done:observed},
  {name:observationState?'관찰 결과 검토':'결과 검토',state:stale?'이전 분석 결과 유지':observed?'검토 전 후보':'실행 전',done:false},
 ];
 return <section className="comparison-workflow" aria-label="비교 진행 상태">
  <ol>{steps.map((step,index)=><li key={step.name} data-comparison-step={index+1} data-complete={step.done} aria-current={index===(pagesReady&&!roi?1:0)&&!step.done?"step":undefined}>
   <span className="workflow-number" aria-hidden="true">{step.done?"✓":index+1}</span><span><strong>{step.name}</strong><small>{step.state}</small></span>
  </li>)}</ol>
  {observationState?<div className="automatic-alignment-status"><div><h2>ROI → 기초 관찰</h2><p>{!pagesReady?'PDF와 페이지를 선택해 도면을 추출하세요.':!roi?'Target에서 직사각형 ROI를 지정하세요.':'Target 아래의 영역 분석 실행을 누르면 현재 PDF의 실제 영상·벡터·문자 관찰을 생성합니다.'}</p><p>관찰은 검증된 벽·문·공간이 아닙니다. 두 도면의 독립 기하 분석 후 비교 영역 대응에서 정렬을 검증할 수 있습니다.</p></div></div>:<div className="automatic-alignment-status" aria-label="자동 정렬 준비 상태">
   <div><h2>자동 정렬 <span className="capability-badge">준비 중</span></h2>
    <p id="automatic-alignment-unavailable">도면에서 기준이 될 구조를 자동으로 찾아 정렬·검증하는 기능을 준비하고 있습니다. 현재는 두 도면과 비교 영역을 확인할 수 있습니다.</p>
    <p className="workflow-next">{!pagesReady?"PDF를 올리고 비교할 페이지를 선택해 주세요.":!roi?"오른쪽 Target 도면에서 비교할 영역을 그려 주세요.":"비교 영역을 지정했습니다. 자동 정렬 결과는 아직 제공되지 않습니다."}</p>
   </div>
   <button type="button" aria-label="자동 정렬 준비 중" aria-describedby="automatic-alignment-unavailable" disabled>자동 정렬 · 준비 중</button>
  </div>}
 </section>;
}
