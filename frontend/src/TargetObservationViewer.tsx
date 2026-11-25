import {type ReactNode,useCallback,useEffect,useRef,useState} from 'react';
import type {FocusRegion} from './DrawingViewer';
import type {NativeImage} from './api';
import type {TargetROI} from './roi';
import {ReferenceUnderstandingViewer} from './ReferenceUnderstandingViewer';
import {TargetViewer} from './TargetViewer';
import {bundleMatches,resultFrameMatches,observationInput,observationLayer} from './observations';
import type {ObservationBundle,ObservationRun} from './observations';
import {useSpatialUnderstanding} from './useSpatialUnderstanding';
import {useGeometricUnderstanding} from './useGeometricUnderstanding';
import {isRectangle} from './rectangleROI';

const labels:Record<string,string>={NOT_RUN:'실행 전',QUEUED:'순서 대기',RUNNING:'관찰 생성 중',COMPLETED:'완료',PARTIAL:'부분 결과',FAILED:'실패',UNAVAILABLE:'해당 관찰 없음',STALE:'이전 결과 유지 · 재분석 필요'};
const reasons:Record<string,string>={EXISTING_OCR_ENVIRONMENT_UNAVAILABLE:'기존 OCR 실행 환경이 없습니다.',EXISTING_LOCAL_OCR_WEIGHTS_UNAVAILABLE:'기존 로컬 OCR 가중치가 없습니다.',EXISTING_OCR_PIPELINE_FAILED:'기존 OCR 실행이 실패했습니다.',OCR_TIMEOUT_100_SECONDS:'OCR 실행 시간이 초과됐습니다.',NO_EXTRACTABLE_VECTOR_PATHS_RASTER_FALLBACK_USED:'PDF Vector가 없어 Raster 관찰을 사용했습니다.'};
async function jsonRequest<T>(url:string,options:RequestInit={}):Promise<T>{const response=await fetch(url,options);if(!response.ok){const detail=await response.json().catch(()=>({}));throw Error(typeof detail.detail==='string'?detail.detail:'관찰 요청을 처리하지 못했습니다.')}return response.json()}

export function TargetObservationViewer({image,onROIChange,technicalDetails=false,compact=false,onState,onGeometryRun,alignmentOverlay,comparisonFocus,initialROI,comparisonAction}:{onGeometryRun?:(id:string|null)=>void;alignmentOverlay?:ReactNode;comparisonFocus?:FocusRegion;initialROI?:TargetROI;comparisonAction?:ReactNode;compact?:boolean;image:NativeImage;onROIChange:(roi:TargetROI|null)=>void;technicalDetails?:boolean;onState:(state:string)=>void}){
 const [analysisMode,setAnalysisMode]=useState('independent');
 const [roi,setROI]=useState<TargetROI|null>(null),[bundle,setBundle]=useState<ObservationBundle|null>(null),[phase,setPhase]=useState('NOT_RUN'),[error,setError]=useState('');
 const [layers,setLayers]=useState<Record<string,boolean>>({lines:true,parallel:true,curves:true,text:true,points:false,paths:false,context:true,detections:false,pdftext:false});
 const request=useRef<AbortController|null>(null),generation=useRef(0),running=useRef(false);
 const sourceKey=JSON.stringify([image.document.id,image.id]);
 const inputKey=JSON.stringify([sourceKey,roi?.vertices]);
 const changed=useCallback((next:TargetROI|null)=>{setROI(next);onROIChange(next)},[onROIChange]);
 // Source changes clear snapshots. ROI edits only cancel work for the old ROI;
 // completed results keep their original native coordinates and analysis scope.
 useEffect(()=>{setBundle(null);setPhase('NOT_RUN');setError('')},[sourceKey]);
 useEffect(()=>{generation.current++;request.current?.abort();running.current=false;setPhase('NOT_RUN');setError('');return()=>{generation.current++;request.current?.abort()}},[inputKey]);
 const current=bundle&&resultFrameMatches(bundle,image)?bundle:null;
 const stale=Boolean(current&&!bundleMatches(current,image,roi));
 const displayPhase=['QUEUED','RUNNING','FAILED'].includes(phase)?phase:stale?'STALE':current?.status||phase;
 useEffect(()=>{onState(displayPhase)},[displayPhase,onState]);
 async function run(){
  if(!roi||running.current)return;const controller=new AbortController(),own=++generation.current;request.current?.abort();request.current=controller;running.current=true;setError('');setPhase('QUEUED');
  try{
   let result=await jsonRequest<ObservationRun>('/api/observations',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(observationInput(image,roi)),signal:controller.signal});
   const deadline=Date.now()+400000;
   while(['QUEUED','RUNNING'].includes(result.status)){
    if(own!==generation.current||controller.signal.aborted)return;setPhase(result.status);
    if(Date.now()>deadline)throw Error('분석 대기 시간이 초과됐습니다. 잠시 후 다시 확인하세요.');
    await new Promise(resolve=>setTimeout(resolve,700));result=await jsonRequest<ObservationRun>('/api/observations/'+result.run_id,{signal:controller.signal});
   }
   if(own!==generation.current||controller.signal.aborted)return;
   if(result.status==='FAILED'||!result.result)throw Error(result.failure?.reason||'관찰 생성이 실패했습니다.');
   if(!bundleMatches(result.result,image,roi))throw Error('결과의 원본 Frame 또는 ROI가 현재 도면과 다릅니다.');
   setBundle(result.result);setPhase(result.status);
  }catch(e){if(own===generation.current&&!controller.signal.aborted){setError(e instanceof Error?e.message:'관찰 생성 실패');setPhase('FAILED')}}finally{if(own===generation.current)running.current=false;}
 }
 function save(){if(!current)return;const url=URL.createObjectURL(new Blob([JSON.stringify(current,null,2)],{type:'application/json'})),a=document.createElement('a');a.href=url;a.download=`target_page_${image.page}_observations.json`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000)}
 const display=current?.observations.filter(o=>layers[observationLayer(o.observation_type)])||[];
 const buckets=Object.values(display.reduce<Record<string,typeof display>>((all,o)=>{(all[o.observation_type]??=[]).push(o);return all},{})),shown:typeof display=[];
 for(let i=0;shown.length<2500&&buckets.some(b=>i<b.length);i++)for(const b of buckets)if(b[i]&&shown.length<2500)shown.push(b[i]);
 const context=current?.analysis_scope.context.bbox_native;
 const overlay=current&&<g className="architectural-observations" data-frame-id={current.frame_id} data-results-stale={stale}>
  <defs><clipPath id={'observation-clip-'+current.run_id}><rect x={context![0]} y={context![1]} width={context![2]-context![0]} height={context![3]-context![1]}/></clipPath></defs>
  {stale&&<rect className="analysis-snapshot-roi" data-observation-analysis-roi={JSON.stringify(current.analysis_scope.core_roi.bbox_native)} x={current.analysis_scope.core_roi.bbox_native[0]} y={current.analysis_scope.core_roi.bbox_native[1]} width={current.analysis_scope.core_roi.bbox_native[2]-current.analysis_scope.core_roi.bbox_native[0]} height={current.analysis_scope.core_roi.bbox_native[3]-current.analysis_scope.core_roi.bbox_native[1]} fill="none" stroke="#ad6100" strokeDasharray="9 5" strokeWidth={2} vectorEffect="non-scaling-stroke"><title>이전 관찰의 분석 영역 · 원본 좌표 유지</title></rect>}
  {layers.context&&<rect className="observation-context" x={context![0]} y={context![1]} width={context![2]-context![0]} height={context![3]-context![1]} fill="none" stroke="#7c4dbe" strokeDasharray="6 4" strokeWidth={2} vectorEffect="non-scaling-stroke"/>}
  <g clipPath={'url(#observation-clip-'+current.run_id+')'}>{shown.map(o=>{
   const p=o.geometry_native.points_native,group=observationLayer(o.observation_type),color=group==='detections'?'#cc7000':group==='pdftext'?'#734abe':group==='text'?'#d53d49':group==='parallel'?'#098c7e':group==='curves'?'#a03aaf':group==='points'?'#db8514':group==='paths'?'#737e8c':o.observation_type==='PdfLine'?'#1769d2':'#dd8519';
   return <g key={o.observation_id} data-observation-id={o.observation_id} data-observation-type={o.observation_type} opacity={o.core_roi_intersection?0.75:0.25}><title>{o.observation_type+' · PROPOSED'+(['TextRegion','PdfText'].includes(o.observation_type)?' · '+String(o.source_evidence.text):o.observation_type==='TextDetection'?' · 인식 '+String(o.source_evidence.recognition_status):'')+(o.boundary_truncated?' · 경계 절단 가능':'')}</title>{p.length===1?<circle cx={p[0][0]} cy={p[0][1]} r={3} fill={color}/>:<polyline points={(o.geometry_native.trace_closed?[...p,p[0]]:p).map(v=>v.join(',')).join(' ')} fill="none" stroke={color} strokeWidth={group==='parallel'||group==='text'?1.5:1} vectorEffect="non-scaling-stroke"/>}</g>
  })}</g>
 </g>;
 const geometry=useGeometricUnderstanding(image,roi,current);
 const spatial=useSpatialUnderstanding(image,roi,current,geometry.bundle);
 const inspection=spatial.inspection?{...spatial.inspection,onPick:(p:{x:number;y:number})=>{if(spatial.pickAt(p))geometry.clearSelection();else geometry.inspection?.onPick?.(p)}}:geometry.inspection;
 const tools=<section className="observation-panel" aria-label="Architectural Observation">
  <h3>영역 관찰</h3><div className="observation-actions"><button onClick={()=>void run()} disabled={!roi||!isRectangle(roi.vertices)||['QUEUED','RUNNING'].includes(phase)}>영역 분석 실행</button>{current&&<button onClick={save}>관찰 JSON 저장</button>}<span role="status" data-observation-state={displayPhase}>{labels[displayPhase]}</span></div>
  {!roi?<p>Target 직사각형 ROI를 먼저 지정하세요.</p>:!isRectangle(roi.vertices)?<p>기존 다각형은 직사각형 보정을 선택한 뒤 분석할 수 있습니다.</p>:<p>Core ROI와 주변 48px 문맥을 사용합니다. Ref에는 적용하지 않습니다.</p>}
  {stale&&<p role="status" className="analysis-stale-notice">ROI가 변경됐습니다. 이전 분석 결과는 원래 위치에 유지합니다. 새 영역은 영역 분석 실행으로 다시 분석하세요. 내려받는 JSON도 이전 분석 영역 기준입니다.</p>}
  {error&&<p role="alert" className="error">{error}</p>}
  {current&&<>
   <div className="observation-layers">{Object.entries({lines:'선분',parallel:'평행 경계',curves:'원호·윤곽',text:'OCR 인식 문자',detections:'OCR 검출 영역',pdftext:'PDF 문자',points:'끝점·접촉',paths:'PDF 경로범위',context:'문맥 여백'}).map(([key,label])=><label key={key}><input type="checkbox" checked={layers[key]} onChange={e=>setLayers(v=>({...v,[key]:e.target.checked}))}/>{label}</label>)}</div>
   <p className="observation-summary">관찰 {current.observations.length}개 · 화면 {shown.length}개{display.length>2500?' (표시 한도 2,500개; 생성된 관찰은 JSON에 모두 보존)':''}</p>
   <p>녹색은 사용자 ROI, 보라색 점선은 문맥입니다. 문맥에서만 관찰된 요소는 연하게 표시합니다.</p>
   {Object.entries(current.components).map(([name,c])=><p key={name} data-component={name}>{name}: {labels[c.status]||c.status}{c.reason?' · '+(reasons[c.reason]||c.reason):''}{c.regions!==undefined?' · 문자 '+c.regions+'개':''}</p>)}
   {Object.keys(current.coverage.omitted_due_to_limits).length>0&&<p role="status">일부 유형은 관찰 수 한도로 제한됐습니다. JSON에 누락 수를 기록했습니다. 더 작은 ROI로 다시 관찰할 수 있습니다.</p>}
   <details><summary>OCR 판독 문자열</summary><ul>{current.observations.filter(o=>o.observation_type==='TextRegion').map(o=><li key={o.observation_id}>{String(o.source_evidence.text)||'(빈 판독)'} · 점수 {o.confidence?.toFixed(3)??'없음'}</li>)}</ul></details>
   <p>모든 결과는 검토 전 관찰입니다. 선분에 문자획·치수선·가구가 포함될 수 있으며, 텍스트 박스는 실제 공간 경계가 아닙니다. 경계 절단점도 벽 끝으로 확정하지 않습니다.</p>
  </>}
 </section>;
 return <ReferenceUnderstandingViewer image={image} role="Target" onGeometryRun={onGeometryRun} alignmentOverlay={alignmentOverlay} comparisonFocus={comparisonFocus} technicalDetails={technicalDetails} renderDrawing={view=>
  <TargetViewer image={image} initialROI={initialROI} compact={compact} comparisonAction={comparisonAction} onROIChange={changed} technicalDetails={technicalDetails}
   observationOverlay={analysisMode==='independent'?view.overlay:<>{overlay}{geometry.overlay}{spatial.overlay}{alignmentOverlay}</>}
   observationTools={<details open={!compact || undefined}><summary>Target 상세 분석</summary><label>분석 방식 <select aria-label="Target 분석 방식" value={analysisMode} onChange={e=>setAnalysisMode(e.target.value)}>
    <option value="independent">Target 독립 이해 · 전체 페이지 또는 도면 범위</option>
    <option value="roi">ROI 관찰·기하·공간 분석</option></select></label>
    <div hidden={analysisMode!=='independent'}>{view.tools}</div>
    <div hidden={analysisMode!=='roi'}>{tools}{geometry.tools}{spatial.tools}</div></details>}
   focusRegion={analysisMode==='independent'?view.focusRegion:comparisonFocus}
   candidateInspection={analysisMode==='independent'?view.inspection:inspection}/>
 }/>;
}
