import {useEffect,useRef,useState,type ReactNode} from 'react';
import {omittedInCell,validDrawingRegion} from './observationAudit';
import {DrawingRegionPicker} from './DrawingRegionPicker';
import type {ObservationAudit,DrawingRegion,DrawingRegionSelection} from './observationAudit';
import type {NativeImage} from './api';
import {ModelReview,ModelOverlay} from './ReferenceModel';
import {RelationReview,RelationOverlay} from './ReferenceRelations';
import {DomainReview,DomainOverlay} from './ReferenceDomains';
import {PartitionReview,PartitionOverlay} from './SpaceClosure';
import {useGeometricUnderstanding} from './useGeometricUnderstanding';
import {DrawingViewer} from './DrawingViewer';
import type {FocusRegion,Inspection} from './DrawingViewer';
import {referenceInput,referenceMatches,referenceBoundarySegments,referenceFocus} from './referenceUnderstanding';
import type {ReferenceBundle,ReferenceRun} from './referenceUnderstanding';
const phaseLabels:Record<string,string>={NOT_RUN:'실행 전',QUEUED:'순서 대기',RUNNING:'관찰 실행 중',PARTIAL:'검토 전 후보',COMPLETED:'실행 완료 · 검토 필요',FAILED:'실패'};
async function request<T>(url:string,init:RequestInit):Promise<T>{
 const response=await fetch(url,init);if(!response.ok){const data=await response.json().catch(()=>({}));throw Error(data.detail||'Ref 이해 요청이 실패했습니다.')}return response.json();
}
export function ReferenceUnderstandingViewer({image,technicalDetails=false,compact=false,role='Ref',renderDrawing,onGeometryRun,alignmentOverlay,comparisonFocus}:{onGeometryRun?:(id:string|null)=>void;alignmentOverlay?:ReactNode;comparisonFocus?:FocusRegion;compact?:boolean;image:NativeImage;technicalDetails?:boolean;role?:'Ref'|'Target';renderDrawing?:(view:{tools:ReactNode;overlay:ReactNode;inspection:Inspection|undefined;focusRegion:FocusRegion|undefined})=>ReactNode}){
 const endpoint=role==='Ref'?'/api/reference-understandings':'/api/target-understandings';
 const [bundle,setBundle]=useState<ReferenceBundle|null>(null),[phase,setPhase]=useState('NOT_RUN'),[error,setError]=useState('');
 const [show,setShow]=useState(true),[text,setText]=useState(false),[focus,setFocus]=useState<FocusRegion>();
 const [scopeFocus,setScopeFocus]=useState<FocusRegion>();
 useEffect(()=>{setScopeFocus(undefined)},[comparisonFocus?.key]);
 const [selection,setSelection]=useState<DrawingRegionSelection>(),[showCoverage,setShowCoverage]=useState(false),[showLayout,setShowLayout]=useState(false);
 const generation=useRef(0),controller=useRef<AbortController|null>(null),running=useRef(false);
 const sourceKey=image.document.id+':'+image.id;
 useEffect(()=>{setBundle(null);setPhase('NOT_RUN');setError('');setFocus(undefined);setScopeFocus(undefined);setSelection(undefined);return()=>{generation.current++;controller.current?.abort();running.current=false}},[sourceKey]);
 async function run(){
  if(running.current)return;running.current=true;const own=++generation.current,abort=new AbortController();controller.current=abort;setPhase('QUEUED');setError('');
  try{
   let job=await request<ReferenceRun>(endpoint,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(referenceInput(image,selection,role)),signal:abort.signal});
   const deadline=Date.now()+400000;
   while(['QUEUED','RUNNING'].includes(job.status)){
    if(own!==generation.current||abort.signal.aborted)return;setPhase(job.status);if(Date.now()>deadline)throw Error('실행 대기 시간이 초과됐습니다.');
    await new Promise(resolve=>setTimeout(resolve,900));job=await request<ReferenceRun>(endpoint+'/'+job.run_id,{signal:abort.signal});
   }
   if(own!==generation.current||abort.signal.aborted)return;
   if(job.status==='FAILED'||!job.result)throw Error(job.failure?.reason||role+' 이해 작업이 실패했습니다.');
   if(!referenceMatches(job.result,image,role))throw Error(role+' 원본 Frame이 다른 결과를 거부했습니다.');setBundle(job.result);setPhase(job.status);
  }catch(error){if(own===generation.current&&!abort.signal.aborted){setError(error instanceof Error?error.message:role+' 실행 실패');setPhase('FAILED')}}finally{if(own===generation.current)running.current=false;}
 }
 const current=bundle&&referenceMatches(bundle,image,role)?bundle:null;
 function save(){if(!current)return;const url=URL.createObjectURL(new Blob([JSON.stringify(current,null,2)],{type:'application/json'})),a=document.createElement('a');a.href=url;a.download=`${role.toLowerCase()}_page_${image.page}_understanding.json`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000)}
 const audit=current as (ReferenceBundle&ObservationAudit)|null;
 const regions=audit?.drawing_regions?.candidates.filter(r=>validDrawingRegion(r,image,role))||[];
 function selectRegion(region?:DrawingRegion){
  if(!current)return;
  setSelection(region?{run_id:current.run_id,region_id:region.region_id}:undefined);
  setScopeFocus({key:'analysis-scope:'+Date.now(),bounds:region?.bbox_native??[0,0,image.width,image.height]});
 }
 function focusCase(next:FocusRegion){
  setScopeFocus(undefined);
  setFocus(next);
 }
 const selected=regions.find(r=>r.region_id===selection?.region_id);
 const scopeChanged=Boolean(current&&JSON.stringify(current.coverage.drawing_region_selection?{run_id:(current.coverage.drawing_region_selection as DrawingRegionSelection).run_id,region_id:(current.coverage.drawing_region_selection as DrawingRegionSelection).region_id}:null)!==JSON.stringify(selection||null));
 const geometry=useGeometricUnderstanding(image,null,null,{bundle:current,role,scopeChanged:scopeChanged||['RUNNING','QUEUED'].includes(phase)});
 const [view,setView]=useState('geometry'),[domainLayer,setDomainLayer]=useState('R');
 const partition=geometry.stale?undefined:geometry.bundle?.space_closure;
 const domains=geometry.stale?undefined:(role==='Ref'?geometry.bundle?.reference_domains:geometry.bundle?.target_domains);
 const activeLayer=domainLayer==='shared'&&!domains?.open_plan?.shared_region?'R':domainLayer;
 const domainView=Boolean(domains&&view==='domains');
 const partitionView=Boolean(partition&&view==='partition');
 const relations=geometry.stale?undefined:(role==='Ref'?geometry.bundle?.reference_relations:geometry.bundle?.target_relations);
 const relationView=Boolean(relations&&view==='relations');
 const model=geometry.stale?undefined:(role==='Ref'?geometry.bundle?.reference_model:geometry.bundle?.target_model);
 const currentRun=model&&geometry.readyForComparison&&phase!=='FAILED'?geometry.bundle!.run_id:null;
 useEffect(()=>{onGeometryRun?.(currentRun);return ()=>onGeometryRun?.(null)},[currentRun,onGeometryRun]);
 const [modelCase,setModelCase]=useState(''),[showModel,setShowModel]=useState(true);
 const modelView=Boolean(model&&view==='model');
 const spaceView=domainView||partitionView||relationView||modelView;
 const tools=<section className="observation-panel" aria-label={role==='Ref'?'Reference Understanding':'Target Understanding'}>
  <h3>{role} 이해</h3><div className="observation-actions"><button onClick={()=>void run()} disabled={['QUEUED','RUNNING'].includes(phase)}>{role==='Ref'?'Reference 이해 실행':'Target 이해 실행'}</button><span role="status" data-reference-state={phase}>{phaseLabels[phase]}</span></div>
  <p>{role}의 페이지 또는 선택한 도면 범위에서 자체 영상·문자를 관찰합니다.</p>
  {error&&<p role="alert" className="error">{error}</p>}
  {current&&<>
   <DrawingRegionPicker image={image} regions={regions} selected={selection?.region_id}
    busy={['QUEUED','RUNNING'].includes(phase)} onSelect={selectRegion} onClose={()=>setScopeFocus(undefined)}/>
   {scopeChanged&&<p role="status">분석 범위 선택이 변경됐습니다. 기존 결과는 원래 위치에 보존됩니다. {role} 이해 실행을 눌러 선택 범위를 새로 관찰하세요.</p>}

   {!partition&&!geometry.stale&&<p>{role} 건축 기하 분석을 실행하면 공간 복원을 검토할 수 있습니다.</p>}
   {partition&&<label>검토할 결과 <select aria-label={role+" 복원 결과 보기"} value={spaceView?view:'geometry'} onChange={e=>{setView(e.target.value);setDomainLayer('R')}}>
    <option value="geometry">기하 후보와 초기 경계</option><option value="partition">4단계 공간 복원</option>{domains&&<option value="domains">5단계 세대 영역과 OpenPlan</option>}{relations&&<option value="relations">6단계 관계와 재관찰</option>}{model&&<option value="model">{role} 이해 통합 검토</option>}</select></label>}
   {modelView&&model&&<ModelReview role={role} result={model} selected={modelCase} onSelect={setModelCase} onFocus={focusCase} show={showModel} onShow={setShowModel}/>}
   {relationView&&relations&&<RelationReview result={relations} onFocus={focusCase}/>}
   {domainView&&domains&&<DomainReview result={domains} selected={activeLayer} onSelect={setDomainLayer}/>}
   {partitionView&&partition&&<PartitionReview result={partition} onFocus={focusCase}/>}
   {!spaceView&&<><p>공간명 후보 {current.cases.length}개 · 조건부 영역 {current.summary.reobserved_region_proposals}개 · 미해결 {current.summary.unresolved_after}개. 전체 공간의 정확성은 아직 검증되지 않았습니다.</p>
   <div className="observation-layers"><label><input type="checkbox" checked={show} onChange={e=>setShow(e.target.checked)}/>공간 경계 후보</label><label><input type="checkbox" checked={text} onChange={e=>setText(e.target.checked)}/>공간명 OCR</label></div>
   <div className="reference-space-list">{current.cases.map(c=><button key={c.id} onClick={()=>focusCase({key:c.id+':'+Date.now(),bounds:referenceFocus(c,image)})}>{c.label.name}{c.after.status==='REGION_PROPOSED'?' · 후보':' · 미해결'}</button>)}</div>
   <p>청록색은 관찰 지지 구간, 주황색 점선은 추론 폐합 구간입니다. 모두 미검증 후보이며 물리적 벽을 확정하지 않습니다. OCR 박스는 공간 경계가 아닙니다.</p></>}
   {Object.entries(current.components).map(([name,c])=><p key={name} data-reference-component={name}>{name}: {c.status}{c.reason?' · '+c.reason:''}</p>)}
   <details><summary>고급 근거·내보내기</summary>
    <div className="observation-layers"><label><input aria-label={role+" 관찰 누락 위치"} type="checkbox" checked={showCoverage} onChange={e=>setShowCoverage(e.target.checked)}/>관찰 상한 누락 위치</label><label><input aria-label={role+" DrawingRegion 후보 표시"} type="checkbox" checked={showLayout} onChange={e=>setShowLayout(e.target.checked)}/>도면 범위 후보</label></div>
    {audit?.components.OCR.stages&&<p data-ocr-stage-status>OCR 검출: {audit.components.OCR.stages.detection.status} · 인식: {audit.components.OCR.stages.recognition.status}</p>}
    {audit?.reobservation_audit&&<p data-reobservation-ledger>재관찰 실행 {audit.reobservation_audit.tiles.length}회 · 미실행 {audit.reobservation_audit.skipped.length}건. 실행 완료는 질문 해결이나 독립 검증을 뜻하지 않습니다.</p>}
    <p>색칠된 격자는 저장 상한으로 생략된 관찰의 bbox 위치입니다. 실제 벽의 누락률이나 빈 공간을 뜻하지 않습니다. 범위 후보도 실제 공간 경계가 아닙니다.</p>
<button onClick={save}>{role} 이해 JSON 저장</button><p>후보·재관찰·원본 좌표·이전 가설·온톨로지 계약 검사를 함께 저장합니다. 현재 모델의 검증 상태와 근거를 함께 저장합니다.</p></details>
  </>}
 </section>;
 const overlay=current&&<g className="reference-understanding" data-frame-id={current.frame_id} data-claim-status="PROPOSED">
  {showCoverage&&audit?.observation_bundle.coverage.spatial_audit?.cells.filter(c=>omittedInCell(c)>0).map(c=><rect key={'coverage:'+c.cell_id} data-coverage-cell={c.cell_id} x={c.bbox_native[0]} y={c.bbox_native[1]} width={c.bbox_native[2]-c.bbox_native[0]} height={c.bbox_native[3]-c.bbox_native[1]} fill="#df6800" opacity={Math.min(.42,.08+Math.log1p(omittedInCell(c))/25)}><title>{'상한 생략 bbox 관찰 '+omittedInCell(c)+'건 · 정답률 아님'}</title></rect>)}
  {regions.filter(r=>showLayout||selected?.region_id===r.region_id).map(r=><rect key={r.region_id} data-drawing-region={r.region_id} x={r.bbox_native[0]} y={r.bbox_native[1]} width={r.bbox_native[2]-r.bbox_native[0]} height={r.bbox_native[3]-r.bbox_native[1]} fill="none" stroke={selected?.region_id===r.region_id?'#a300c0':'#786ca8'} strokeDasharray="10 5" strokeWidth={2} vectorEffect="non-scaling-stroke"><title>{r.kind+' · PROPOSED · 분석 범위 후보'}</title></rect>)}
  {current.cases.map(c=><g key={c.id} data-reference-case={c.label.name}><title>{c.label.name+' · '+c.after.status+' · PROPOSED'}</title>
   {!spaceView&&show&&referenceBoundarySegments(c).map((segment,i)=><polyline key={i} data-inferred-closure={segment.inferred} points={segment.points.map(p=>p.join(',')).join(' ')} fill="none" stroke={segment.inferred?'#c17308':'#058b80'} strokeDasharray={segment.inferred?'7 4':undefined} strokeWidth={2} vectorEffect="non-scaling-stroke"/>)}
   {!spaceView&&text&&<polygon points={c.label.observation.polygon_native.map(p=>p.join(',')).join(' ')} fill="none" stroke="#d53d49" strokeWidth={1.5} vectorEffect="non-scaling-stroke"/>}
  </g>)}
 </g>;
 const drawing = {
  tools:<>{tools}{current&&(spaceView?<button onClick={geometry.save}>{modelView?role+' 통합 모델 JSON 저장':'공간 복원·기하 JSON 저장'}</button>:geometry.tools)}</>,
  overlay:<>{overlay}{modelView&&model?(showModel?<ModelOverlay result={model} selected={modelCase}/>:null):relationView&&relations?<RelationOverlay result={relations}/>:domainView&&domains?<DomainOverlay result={domains} selected={activeLayer}/>:partitionView&&partition?<PartitionOverlay result={partition}/>:geometry.overlay}{alignmentOverlay}</>,
  inspection:spaceView?undefined:geometry.inspection,
  focusRegion:scopeFocus ?? comparisonFocus ?? focus
 };
 if(renderDrawing)return renderDrawing(drawing);
 return <DrawingViewer role={role} image={image} {...drawing}
  tools={compact?<details><summary>Ref 상세 분석</summary>{drawing.tools}</details>:drawing.tools}
  technicalDetails={technicalDetails}/>;
}
