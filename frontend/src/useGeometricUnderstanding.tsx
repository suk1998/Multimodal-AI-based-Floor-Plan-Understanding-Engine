import {useEffect,useRef,useState} from 'react';
import type {NativeImage} from './api';
import type {TargetROI} from './roi';
import {observationInput,bundleMatches,resultFrameMatches} from './observations';
import type {ObservationBundle} from './observations';
import {referenceInput,referenceMatches} from './referenceUnderstanding';
import type {ReferenceBundle} from './referenceUnderstanding';
import type {Inspection} from './DrawingViewer';
import {geometryMatches,geometricLayer,pickGeometricEntity} from './geometricUnderstanding';
import type {GeometryBundle,GeometryRun} from './geometricUnderstanding';
const names:Record<string,string>={Wall:'벽 후보',Door:'문 후보',Window:'창문 후보',Opening:'개구부 후보',Unknown:'유형 불확실 후보'};
const states:Record<string,string>={NOT_RUN:'실행 전',QUEUED:'순서 대기',RUNNING:'기하 분석 중',COMPLETED:'후보 생성 완료',PARTIAL:'분석 불완전 · 부분 후보',FAILED:'분석 실패',STALE:'이전 결과 유지 · 재분석 필요'};
export function useGeometricUnderstanding(image:NativeImage,roi:TargetROI|null,parent:ObservationBundle|null,reference?:{bundle:ReferenceBundle|null;scopeChanged:boolean;role?:'Ref'|'Target'}){
 const [bundle,setBundle]=useState<GeometryBundle|null>(null),[phase,setPhase]=useState('NOT_RUN'),[error,setError]=useState(''),[selected,setSelected]=useState<string|null>(null);
 const [layers,setLayers]=useState<Record<string,boolean>>({walls:true,doors:true,windows:true}),[pick,setPick]=useState(true);
 const generation=useRef(0),abort=useRef<AbortController|null>(null),busy=useRef(false);
 const sourceKey=JSON.stringify([image.document.id,image.id]);
 const key=JSON.stringify([sourceKey,parent?.run_id,roi?.vertices,reference?.bundle?.run_id,reference?.scopeChanged]);
 useEffect(()=>{setBundle(null);setPhase('NOT_RUN');setError('');setSelected(null)},[sourceKey]);
 useEffect(()=>{generation.current++;abort.current?.abort();busy.current=false;setPhase('NOT_RUN');setError('');return()=>{generation.current++;abort.current?.abort()}},[key]);
 const understandingRole=reference?.role||'Ref';
 const ref=reference?.bundle, refObs=ref?.observation_bundle;
 const frameMatches=(b:GeometryBundle)=>reference?
  b.frame_id===understandingRole+':'+image.id&&b.source_binding.document_id===image.document.id&&b.source_binding.image_id===image.id:
  resultFrameMatches(b,image);
 const matches=(b:GeometryBundle)=>reference?Boolean(refObs&&frameMatches(b)&&b.parent_observation_run_id===refObs.run_id&&
  refObs.frame_id===b.frame_id&&JSON.stringify(b.source_binding.core_bbox_native)===JSON.stringify(refObs.source_binding.core_bbox_native)):
  Boolean(parent&&geometryMatches(b,parent,image,roi));
 const current=bundle&&frameMatches(bundle)?bundle:null;
 const parentReady=reference?Boolean(ref&&refObs&&referenceMatches(ref,image,understandingRole)&&!reference.scopeChanged):Boolean(parent&&bundleMatches(parent,image,roi));
 const stale=Boolean(current&&(!parentReady||!matches(current)));
 const displayPhase=['QUEUED','RUNNING','FAILED'].includes(phase)?phase:stale?'STALE':current?.status||phase;
 async function run(){
  if(!parentReady||busy.current)return;
  const input=ref?{...referenceInput(image,undefined,understandingRole),[understandingRole==='Ref'?'reference_run_id':'target_run_id']:ref.run_id}:parent&&roi?{...observationInput(image,roi),observation_run_id:parent.run_id}:null;
  if(!input)return;
  const controller=new AbortController(),own=++generation.current;abort.current?.abort();abort.current=controller;busy.current=true;setError('');setPhase('QUEUED');
  async function request(url:string,options:RequestInit={}):Promise<GeometryRun>{const r=await fetch(url,{...options,signal:controller.signal});if(!r.ok){const body=await r.json().catch(()=>({}));throw Error(typeof body.detail==='string'?body.detail:'기하 요청이 실패했습니다.')}return r.json()}
  try{
   let job=await request('/api/geometries',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(input)});
   const deadline=Date.now()+760000;
   while(['QUEUED','RUNNING'].includes(job.status)){
    if(own!==generation.current)return;setPhase(job.status);if(Date.now()>deadline)throw Error('기하 분석 대기 시간이 초과됐습니다.');await new Promise(r=>setTimeout(r,1000));job=await request('/api/geometries/'+job.run_id);
   }
   if(own!==generation.current||controller.signal.aborted)return;
   if(!job.result||job.status==='FAILED')throw Error(job.failure?.reason||'기하 분석이 실패했습니다.');
   if(!matches(job.result))throw Error('결과의 PDF·ROI·좌표계가 현재 입력과 다릅니다.');
   setBundle(job.result);setSelected(null);setPhase(job.status);setPick(true);
  }catch(e){if(own===generation.current&&!controller.signal.aborted){setError(e instanceof Error?e.message:'분석 실패');setPhase('FAILED')}}finally{if(own===generation.current)busy.current=false;}
 }
 const assessment=current?.architectural_assessment;
 const fragments=new Map(assessment?.structural_parents.map(p=>[p.parent_structure_id,p.fragments]));
 const selectedAssessment=fragments.get(selected||'')||assessment?.openings.find(o=>o.source_geometry_entity_id===selected);
 const filtered=current?.entities.filter(e=>layers[geometricLayer(e)])||[];
 const visible=filtered.slice(0,2000),chosen=current?.entities.find(e=>e.entity_id===selected),chosenEvidence=current?.evidence.filter(e=>chosen?.evidence_refs.includes(e.evidence_id))||[];
 const inspection:Inspection|undefined=current&&pick?{panOnDrag:true,selectionKey:selected,onPick:p=>{setSelected(pickGeometricEntity([p.x,p.y],visible)?.entity_id||null)}}:undefined;
 function save(){if(!current)return;const url=URL.createObjectURL(new Blob([JSON.stringify(current)],{type:'application/json'})),a=document.createElement('a');a.href=url;a.download=`${reference?understandingRole.toLowerCase():'target'}_page_${image.page}_geometry.json`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000)}
 const overlay=current&&<g className="architectural-geometry" data-frame-id={current.frame_id} data-results-stale={stale}>{visible.map(e=>{
  const selected=e.entity_id===chosen?.entity_id,layer=geometricLayer(e),color=selected?'#ef2b19':layer==='walls'?'#1759c6':layer==='doors'?'#d77005':'#8c26c6';
  const decision=e.geometry_properties.architectural_admissibility?.decision;
  const uncertain=e.element_type==='Unknown'||['HOLD','CONTRADICTED','PARTIAL_GEOMETRIC_SUPPORT'].includes(decision||'');
  const parts=fragments.get(e.entity_id);
  return <g data-admission={decision} key={e.entity_id} data-geometry-id={e.entity_id} data-element-type={e.element_type} data-geometry-layer={layer} data-selected={selected} opacity={e.roi_intersection?1:.35}>
   <title>{names[e.element_type]} · PROPOSED{e.boundary_truncated?' · 경계 절단 가능':''}</title>
   {(e.geometry_native.polygons_native||[]).map((poly,i)=><polygon key={'p'+i} points={poly.map(p=>p.join(',')).join(' ')} fill={selected?'#ef2b1922':'none'} stroke={color} strokeWidth={selected?3:1.6} strokeDasharray={(parts?.[i]?parts[i].decision==='HOLD':uncertain)?'4 3':undefined} vectorEffect="non-scaling-stroke"/>)}
   {(parts?(selected?parts.map(f=>f.centerline_native):[]):e.geometry_native.polylines_native||[]).map((line,i)=><polyline key={'l'+i} points={line.map(p=>p.join(',')).join(' ')} fill="none" stroke={color} strokeWidth={selected?3:1.6} strokeDasharray={parts?'2 4':uncertain?'4 3':undefined} vectorEffect="non-scaling-stroke"/>)}
  </g>;
 })}</g>;
 const tools=<section className="geometry-panel" aria-label="건축 기하 후보">
  <h3>건축 기하 후보</h3><div className="observation-actions"><button onClick={()=>void run()} disabled={!parentReady||['QUEUED','RUNNING'].includes(phase)}>{reference?understandingRole+' 건축 기하 분석':'Geometry 분석 실행'}</button>{current&&<button onClick={save}>Geometry JSON 저장</button>}<span role="status" data-geometry-state={displayPhase}>{states[displayPhase]}</span></div>
  {!parentReady&&<p>{reference?'선택 범위의 Reference 이해를 먼저 실행하세요.':'현재 ROI의 영역 관찰을 먼저 생성하세요.'}</p>}
  {stale&&<p role="status" className="analysis-stale-notice">분석 범위 또는 관찰 결과가 변경됐습니다. 이전 기하 결과는 원래 위치에 유지합니다. 현재 영역의 관찰 생성 후 Geometry를 다시 실행하세요. 저장 JSON은 이전 분석 결과입니다.</p>}{error&&<p role="alert" className="error">{error}</p>}
  {current&&<>
   <div className="geometry-layers">{Object.entries({walls:'벽 후보',doors:'문·개구부 후보',windows:'창문 후보'}).map(([key,label])=><label key={key}><input type="checkbox" checked={layers[key]} onChange={e=>{setLayers(v=>({...v,[key]:e.target.checked}));setSelected(null)}}/>{label}</label>)}</div>
   <p>벽 {current.audit.candidate_counts.Wall||0} · 문 {current.audit.candidate_counts.Door||0} · 개구부 {current.audit.candidate_counts.Opening||0} · 창문 {current.audit.candidate_counts.Window||0} · 유형 불확실 {current.audit.candidate_counts.Unknown||0}</p>
   {assessment&&<p>가시성·접합 조건을 만족한 벽 구간 {assessment.summary.fragment_decisions.ADMISSIBLE_GEOMETRIC_HYPOTHESIS||0}개 · 보류 {assessment.summary.fragment_decisions.HOLD||0}개. 개구부는 조건 충족 {assessment.summary.opening_decisions.ADMISSIBLE_OPENING_GEOMETRY||0}개 · 보류 {assessment.summary.opening_decisions.HOLD||0}개 · 고체 벽과 충돌 {assessment.summary.opening_decisions.CONTRADICTED||0}개입니다.</p>}
   {!current.entities.length&&<p role="status">미검출 · 현재 범위에서 건축 기하 후보를 생성하지 못했습니다.</p>}
   <label><input type="checkbox" checked={pick} onChange={e=>setPick(e.target.checked)}/>도면에서 후보 확인</label><p>클릭: 후보 근거 · 드래그: 도면 이동. 영역 수정 모드에서는 ROI 편집을 우선합니다. 점선은 유형 미확정 또는 근거 보류·충돌 후보입니다.</p>
   {filtered.length>visible.length&&<p>화면에는 최대 2,000개만 표시합니다. 생성 후보와 근거는 JSON에 보존합니다.</p>}
   <p>모두 PROPOSED입니다. 기하 근거가 실제 건축 객체의 존재를 검증하지는 않습니다. 사선 벽·Raster 원호 해석과 주석 구분에는 미해결 범위가 있습니다.</p>
   {current.coverage.reasons.length>0&&<p>분석 불완전: 일부 유형·문맥의 해석이 보류됐습니다. 저장한 JSON의 Coverage에서 범위를 확인할 수 있습니다.</p>}
   {chosen&&<article className="geometry-detail" data-geometry-selection={chosen.entity_id}>
    <h4>{names[chosen.element_type]} · 검토 전</h4><p>원본 위치: {chosen.geometry_native.bbox_native.map(v=>v.toFixed(2)).join(', ')} px{chosen.boundary_truncated?' · ROI/문맥 경계 절단 가능':''}</p>
    <p>{chosen.geometry_properties.thickness_estimate_px!=null?'평행 경계 간격 추정 '+chosen.geometry_properties.thickness_estimate_px.toFixed(2)+'px · ':''}{chosen.geometry_properties.opening_width_px!=null?'개구부 분석 폭 '+chosen.geometry_properties.opening_width_px.toFixed(2)+'px · ':''}물리적 끝점 미확정</p>
    <p>근거 관찰 {chosen.observation_refs.length}개 · 반대 근거 {chosenEvidence.reduce((n,e)=>n+e.contradicting.length,0)}개. {chosen.geometry_properties.classification==='WINDOW_OR_SLIDING_TRACK'?'창문/슬라이딩 도어 구분 보류. ':''}{chosen.element_type==='Door'?'가시 스윙·문짝·양쪽 Jamb 연결 가설. ':''}{chosen.element_type==='Wall'?'평행 경계·간격·영상 지지의 통합 후보. ':''}</p>
    <details><summary>상세 기하 근거</summary><pre>{JSON.stringify({assessment:selectedAssessment,geometry:chosen.geometry_native,properties:chosen.geometry_properties,evidence:chosenEvidence,uncertainty:chosen.uncertainty_reason,frame_id:chosen.frame_id},null,2)}</pre></details>
   </article>}
  </>}
 </section>;
 return {tools,overlay,inspection,stale,readyForComparison:!stale&&parentReady&&!['QUEUED','RUNNING','FAILED'].includes(phase),save,bundle:current,clearSelection:()=>setSelected(null)};
}
