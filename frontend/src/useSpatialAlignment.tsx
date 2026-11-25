import {useEffect, useRef, useState} from 'react';
import {useObjectComparison, type SavedComparison} from './useObjectComparison';
import type {NativeImage} from './api';
import type {TargetROI, Vertex} from './roi';

type Role = 'Ref'|'Target';
type Source = {document_id:string; frame_id:string; geometry_run_id:string};
type Pair = {id:string; usage:string; ref_segment:Vertex[]; target_segment:Vertex[]; interval_stability:string};
type Tile = {status:string; target_bbox_native:[number,number,number,number]; ref_tile_native?:Vertex[]};
interface Result {
 input_fingerprint:string; status:string; reasons:string[]; sources:{geometry_run_id:string;frame_id:string}[]; pairs:Pair[];
 target_roi:TargetROI; matrix:number[][]|null; roi_mapping_confirmed:boolean;
 search:{held:{target_segment:Vertex[];reason:string}[]};
 rows:{usage:string;normal_max_ref_px:number;angle_degrees:number;other_face:{normal_max_ref_px:number;angle_degrees:number}}[];
 coverage:{fraction:number;tiles:Tile[]}|null;
}
const states:Record<string,string>={DEFERRED:'판단 보류',FAILED:'검증 실패',PARTIAL:'부분 검증',VERIFIED_ROI:'ROI 좌표 대응 검증 완료'};
const points=(v:Vertex[])=>v.map(p=>p.join(',')).join(' ');

function overlay(result:Result|null,role:Role,inspectHeld=false){
 if(!result)return null;
 const clipId='alignment-region-'+role, matrix=result.matrix;
 const clip=role==='Target'?result.target_roi.vertices:matrix?result.target_roi.vertices.map(([x,y]):Vertex=>[
  matrix[0][0]*x+matrix[0][1]*y+matrix[0][2],matrix[1][0]*x+matrix[1][1]*y+matrix[1][2]]):[];
 return <g data-stage9-overlay={role} pointerEvents="none">
  <defs><clipPath id={clipId}><polygon points={points(clip)}/></clipPath></defs>
  <g clipPath={'url(#'+clipId+')'}>{result.coverage?.tiles.map((t,i)=>{
   if(t.status!=='SUPPORTED')return null;
   const [x,y,x2,y2]=t.target_bbox_native;
   return <polygon key={i} fill="#197849" fillOpacity=".25" points={points(role==='Ref'?t.ref_tile_native!:[[x,y],[x2,y],[x2,y2],[x,y2]])}/>;
  })}</g>
  {role==='Target'&&inspectHeld&&result.search.held.map((p,i)=><polyline key={'held-'+i} points={points(p.target_segment)} fill="none" stroke="#a95712" strokeWidth="2" strokeDasharray="5 3" vectorEffect="non-scaling-stroke"><title>대응 보류 구간</title></polyline>)}
  {result.pairs.map(p=><polyline key={p.id} points={points(role==='Ref'?p.ref_segment:p.target_segment)}
   fill="none" stroke={p.usage==='FIT'?'#1e62ae':'#a53387'} strokeWidth="3" vectorEffect="non-scaling-stroke"><title>{p.id+' · '+p.usage+' · '+p.interval_stability}</title></polyline>)}
 </g>;
}

export function useSpatialAlignment(
 ref:NativeImage|null,target:NativeImage|null,roi:TargetROI|null,refRun:string|null,targetRun:string|null,
 restored:SavedComparison|null,prepare:()=>void,preparationPhase:string
){
 const saved=restored?.comparison;
 const savedRoiMatches=Boolean(saved&&roi&&saved.target_roi.image.image_id===roi.image.image_id
  && JSON.stringify(saved.target_roi.vertices)===JSON.stringify(roi.vertices));
 function source(role:Role,image:NativeImage|null,manualRun:string|null,index:number):Source|null{
  if(!image)return null;
  const frame_id=role+':'+image.id;
  const binding=saved?.sources[index];
  const savedRun=savedRoiMatches&&restored?.documents[index]?.id===image.document.id&&binding?.frame_id===frame_id
   ?binding.geometry_run_id:undefined;
  const geometry_run_id=savedRun||manualRun;
  return geometry_run_id?{document_id:image.document.id,frame_id,geometry_run_id}:null;
 }
 const Ref=source('Ref',ref,refRun,0),Target=source('Target',target,targetRun,1);
 const key=JSON.stringify({Ref,Target,target_roi:roi});
 const [snapshot,setSnapshot]=useState<{key:string;result:Result}|null>(null);
 const [error,setError]=useState(''),[busy,setBusy]=useState(false),[show,setShow]=useState(false);
 const [inspectHeld,setInspectHeld]=useState(false);
 const request=useRef<AbortController|null>(null);
 useEffect(()=>{
  setSnapshot(null);setError('');setBusy(false);
  return ()=>{request.current?.abort();request.current=null};
 },[key]);
 const result=snapshot?.key===key?snapshot.result:null;
 const comparison=useObjectComparison(key,restored?undefined:result?.input_fingerprint,restored);
 async function run(){
  if(!ref||!target||!roi||request.current||preparationPhase)return;
  if(!Ref||!Target){prepare();return;}
  const controller=new AbortController();request.current=controller;setBusy(true);setError('');setSnapshot(null);
  try{
   const reply=await fetch('/api/spatial-alignments',{method:'POST',headers:{'Content-Type':'application/json'},body:key,signal:controller.signal});
   if(reply.status===404||reply.status===410)
    throw Error('이전 기하 분석이 만료됐습니다. 위의 비교 분석 실행으로 현재 도면을 다시 분석하세요.');
   const data=await reply.json();if(!reply.ok)throw Error(data.detail||'정렬 검증 요청이 실패했습니다.');
   if(![Ref,Target].every((s,i)=>s.frame_id===data.sources[i]?.frame_id&&s.geometry_run_id===data.sources[i]?.geometry_run_id)
     ||JSON.stringify(data.target_roi.vertices)!==JSON.stringify(roi.vertices))throw Error('입력이 변경된 결과입니다.');
   if(!controller.signal.aborted)setSnapshot({key,result:data});
  }catch(e){if(!controller.signal.aborted)setError(e instanceof Error?e.message:'정렬 요청 실패')}
  finally{if(request.current===controller){request.current=null;setBusy(false)}}
 }
 function save(){
  if(!result)return;
  const url=URL.createObjectURL(new Blob([JSON.stringify(result,null,2)],{type:'application/json'}));
  const a=document.createElement('a');a.href=url;a.download='spatial_alignment.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
 }
 const panel=<section className="observation-panel" aria-label="구조 정렬 검증">
  <h2>비교 영역 대응</h2>
  <button disabled={!ref||!target||!roi||busy||Boolean(preparationPhase)} onClick={()=>void run()}>
   {busy?'검증 중…':result||saved?'구조 정렬 다시 검증':'구조 정렬 검증'}
  </button>
  {!roi&&<p>Target 비교 영역을 먼저 지정하세요.</p>}
  {roi&&(!Ref||!Target)&&!preparationPhase&&<p>버튼을 누르면 두 도면의 기하 분석부터 시작합니다.</p>}
  {preparationPhase&&<p role="status">{preparationPhase}</p>}
  {error&&<p role="alert">{error}</p>}
  {!result&&saved&&<>
   <h3 role="status" data-alignment-state={saved.alignment_status}>{states[saved.alignment_status]??saved.alignment_status}</h3>
   {saved.coverage&&<p>검증한 ROI 면적 {(saved.coverage.fraction*100).toFixed(1)}%</p>}
   {saved.alignment_diagnostics?.reasons?.map(reason=><p key={reason}>{reason}</p>)}
   {saved.alignment_diagnostics?.wall_registration?.independent_max_normal_ref_px!=null&&
    <p>독립 검증 벽의 최대 법선 잔차 {saved.alignment_diagnostics.wall_registration.independent_max_normal_ref_px.toFixed(2)} Ref px</p>}
  </>}
  {result&&<><h3 role="status" data-alignment-state={result.status}>{states[result.status]}</h3>
   <p>구조 구간 대응 후보 {result.pairs.length}개 · {result.coverage?'검증한 ROI 면적 '+(result.coverage.fraction*100).toFixed(1)+'%':'검증한 ROI 면적 없음'}</p>
   {result.reasons.map(reason=><p key={reason}>{reason}</p>)}
   <details><summary>정렬 검증 근거</summary>
   <label><input type="checkbox" checked={show} onChange={e=>setShow(e.target.checked)}/>구간과 대응 범위 표시</label>
   <p>파랑은 적합 후보, 자주는 독립 검증 후보입니다. 녹색 채움은 국소 검사를 통과한 좌표 범위입니다.</p>
   {result.search.held.length>0&&<label><input type="checkbox" checked={inspectHeld} onChange={e=>setInspectHeld(e.target.checked)}/>Target에서 대응을 보류한 {result.search.held.length}개 구간 표시 (주황 점선)</label>}
   <details><summary>구간별 검증 오차</summary>
    {result.rows.length===0?<p>변환 추정에 필요한 근거가 부족해 측정하지 못했습니다.</p>:
     <ul>{result.rows.map((r,i)=><li key={i}>{result.pairs[i].id} · {r.usage} · 두 경계의 최대 법선 거리 {Math.max(r.normal_max_ref_px,r.other_face.normal_max_ref_px).toFixed(2)} Ref px · 최대 방향 차이 {Math.max(r.angle_degrees,r.other_face.angle_degrees).toFixed(2)}°</li>)}</ul>}
   </details>
   <button onClick={save}>정렬 근거 저장</button></details></>}
  {comparison.panel}
 </section>;
 return {panel, summary:comparison.summary, refFocus:comparison.focus('Ref'),targetFocus:comparison.focus('Target'),refOverlay:<>{overlay(show?result:null,'Ref')}{comparison.overlay('Ref')}</>,targetOverlay:<>{overlay(show?result:null,'Target',inspectHeld)}{comparison.overlay('Target')}</>};
}
