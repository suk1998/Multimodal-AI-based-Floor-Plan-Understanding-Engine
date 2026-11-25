import {useEffect,useRef,useState} from "react";
import type {NativeImage} from "./api";
import type {TargetROI} from "./roi";
import type {StructuralAnchorPair} from "./structuralAnchors";
import {bindingFor,completePair,makeAnchorFile} from "./structuralAnchors";
import {DrawingViewer} from "./DrawingViewer";

type Settings={normal_tolerance_ref_px:number;angle_tolerance_degrees:number;support_radius_native_px:number;ink_threshold:number;minimum_visible_support:number;minimum_axis_span_fraction:number;minimum_normal_eigen_ratio:number;maximum_condition_number:number;maximum_rotation_degrees:number;minimum_rotation_concentration:number};
const defaults:Settings={normal_tolerance_ref_px:3,angle_tolerance_degrees:1,support_radius_native_px:2,ink_threshold:160,minimum_visible_support:.70,minimum_axis_span_fraction:.15,minimum_normal_eigen_ratio:.05,maximum_condition_number:10000,maximum_rotation_degrees:15,minimum_rotation_concentration:.90};
interface Row{id:string;parent_structure_id:string;usage:string;normal_max_ref_px:number;normal_rmse_ref_px:number;angle_degrees:number;visible_ink_support:{ref:number;target:number};analysis_interval_overlap_ref_px:number;common_window_support_min:number|null;geometry_passed:boolean;support_passed:boolean}
interface Distribution{parent_count:number;segment_count:number;normal_eigen_ratio:number;axis_span_fraction:number[];sufficient:boolean;midpoint_hull:number[][]}
interface Result {run_id:string;status:"VERIFIED_SAMPLED"|"PARTIAL"|"FAILED"|"DEFERRED";validity:string;reasons:string[];rows:Row[];fit_ids:string[];validation_ids:string[];eligibility:{id:string;eligible:boolean;reason:string}[];fit_distribution?:Record<string,Distribution>;validation_distribution?:Record<string,Distribution>;fit_conditioning?:Record<string,unknown>;coverage?:{validation_probe_band_roi_fraction:number;whole_roi_verified:false};approval_eligible:boolean;matrix:number[][]|null;provisional_mapping:{ref_vertices_unclipped:number[][];target_vertices:number[][];outside_ref_vertex_indices:number[];confirmed:false}|null;review:{state:string;scope:string};preview_modes:string[];execution_context:string}
const states={VERIFIED_SAMPLED:"검증 완료 · 선택 선 표본",PARTIAL:"부분 검증",FAILED:"실패",DEFERRED:"판단 보류"};
const number=(n:number|null|undefined)=>typeof n==="number"?n.toFixed(4):"—";
async function request<T>(path:string,body:unknown,method="POST",signal?:AbortSignal):Promise<T>{
 const r=await fetch(path,{method,headers:{"Content-Type":"application/json"},body:JSON.stringify(body),signal});
 const value=await r.json();if(!r.ok)throw Error(typeof value.detail==="string"?value.detail:`검증 요청 실패 (${r.status})`);return value;
}
export function StructuralAlignmentVerification({refImage,targetImage,roi,pairs,hasPending}:{refImage:NativeImage;targetImage:NativeImage;roi:TargetROI|null;pairs:StructuralAnchorPair[];hasPending:boolean}){
 const [settings,setSettings]=useState<Settings>(defaults),[ready,setReady]=useState(false),[busy,setBusy]=useState(false);
 const [record,setRecord]=useState<{key:string;value:Result}|null>(null),[error,setError]=useState<string|null>(null);
 const [reason,setReason]=useState(""),[confirmed,setConfirmed]=useState(false),[preview,setPreview]=useState("overlay");
 const contextId=useRef(crypto.randomUUID()),controller=useRef<AbortController|null>(null),alive=useRef(true);
 const key=JSON.stringify([bindingFor(refImage),bindingFor(targetImage),roi,pairs,hasPending,settings]);
 const stamp=useRef({key:"",version:0,input_token:""});
 if(stamp.current.key!==key)stamp.current={key,version:stamp.current.version+1,input_token:crypto.randomUUID()};
 const current=record?.key===key?record.value:null;
 const currentCheck=(s:typeof stamp.current)=>alive.current&&stamp.current===s;
 async function sync(s:typeof stamp.current,signal?:AbortSignal){await request(`/api/structural-alignment-contexts/${contextId.current}`,{version:s.version,input_token:s.input_token},"PUT",signal);}
 useEffect(()=>{
  const s=stamp.current,c=new AbortController();controller.current?.abort();controller.current=c;
  setRecord(null);setReady(false);setBusy(false);setError(null);setReason("");setConfirmed(false);
  void sync(s,c.signal).then(()=>{if(currentCheck(s))setReady(true);}).catch(e=>{if(currentCheck(s)&&e.name!=="AbortError")setError(e.message);});
  return ()=>c.abort();
 },[key]);
 useEffect(()=>()=>{
  alive.current=false;controller.current?.abort();const s=stamp.current;
  // Closing/unmounting the editor also revokes a live server approval.
  void fetch(`/api/structural-alignment-contexts/${contextId.current}`,{method:"PUT",keepalive:true,headers:{"Content-Type":"application/json"},body:JSON.stringify({version:s.version+1,input_token:crypto.randomUUID()})}).catch(()=>undefined);
 },[]);
 async function run(){
  if(!roi||hasPending)return;
  const s={...stamp.current,version:stamp.current.version+1,input_token:crypto.randomUUID()};stamp.current=s;
  const c=new AbortController();controller.current?.abort();controller.current=c;setBusy(true);setError(null);setRecord(null);setConfirmed(false);setReason("");
  try{
   const file=makeAnchorFile(pairs,refImage,targetImage,roi);
   await sync(s,c.signal);
   if(!currentCheck(s))return;
   const spec=(image:NativeImage)=>({document_id:image.document.id,page:image.page,zoom:image.zoom});
   const value=await request<Result>("/api/structural-alignments",{context_id:contextId.current,version:s.version,input_token:s.input_token,ref:spec(refImage),target:spec(targetImage),target_roi:roi,anchor_file:file,settings},"POST",c.signal);
   if(currentCheck(s)){setRecord({key,value});setReady(true);setPreview("overlay");}
  }catch(e){if(currentCheck(s)&&(e as Error).name!=="AbortError")setError((e as Error).message);}
  finally{if(currentCheck(s))setBusy(false);}
 }
 async function review(decision:"approve"|"reject"){
  if(!current)return;const s=stamp.current,c=new AbortController();controller.current=c;setBusy(true);setError(null);
  try{
   const value=await request<Result>(`/api/structural-alignments/${current.run_id}/review`,{version:s.version,input_token:s.input_token,decision,reason,sampled_line_scope_confirmed:confirmed},"POST",c.signal);
   if(currentCheck(s))setRecord({key,value});
  }catch(e){if(currentCheck(s)&&(e as Error).name!=="AbortError")setError((e as Error).message);}
  finally{if(currentCheck(s))setBusy(false);}
 }
 function download(){if(!current)return;const url=URL.createObjectURL(new Blob([JSON.stringify(current,null,2)],{type:"application/json"}));const a=document.createElement("a");a.href=url;a.download=`structural_alignment_${current.run_id}.json`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
 function dist(title:string,values:Record<string,Distribution>|undefined){return values&&<details><summary>{title} · 방향과 공간 분포</summary>{Object.entries(values).map(([side,d])=><p key={side}>{side}: Parent {d.parent_count} / 구간 {d.segment_count} · 법선 고유값 비 {number(d.normal_eigen_ratio)} · 양축 분포 {d.axis_span_fraction.map(number).join(" / ")} · {d.sufficient?"표본 배치 조건 충족":"분포 부족"}</p>)}<p>Hull과 구간 분포는 표본 배치이며 그 내부의 조밀 검증을 의미하지 않습니다.</p></details>;}
 const definitions:[keyof Settings,string,number,number,number][]=[
  ["normal_tolerance_ref_px","법선 허용 오차 · Ref px",.1,30,.1],["angle_tolerance_degrees","방향 허용 차이 · 도",.01,10,.1],
  ["support_radius_native_px","가시 지지 검색 반폭 · 각 원본 px",0,8,.5],["minimum_visible_support","최소 가시 지지 비율",.1,1,.05],
  ["ink_threshold","가시 잉크 회색값 상한",1,254,1],["minimum_axis_span_fraction","양축 최소 공간 분포 비율",.01,1,.01],
  ["minimum_normal_eigen_ratio","최소 법선 고유값 비",.001,1,.01],["maximum_condition_number","최대 설계행렬 조건수",1,1000000,100],
  ["maximum_rotation_degrees","회전 분기 범위 · 도",.1,80,1],["minimum_rotation_concentration","최소 방향 일관성",.5,1,.01]];
 return <section className="structural-verification" aria-label="구조 구간 정렬 검증">
  <h3>Phase 2B-3 · 구조 구간 정렬 검증</h3>
  <p>현재 대응 검토를 완료한 FIT 구간만 추정에 사용합니다. FIT는 위치·방향이 안정적이라는 근거가 있는 구간에 배정하세요. VALIDATION은 추정에서 제외하고 다른 Parent의 구간으로 검사합니다.</p>
  <p>지정점은 무한선의 방향·위치와 가시 분석 범위를 정합니다. 물리적 끝점 대응, 전체 길이·개구부 불변성은 검증하지 않습니다. 자동 참고 후보와 SIFT 결과는 승인으로 사용하지 않습니다.</p>
  <details><summary>검증 설정 · 실행에 사용한 값은 JSON에 기록</summary><div className="anchor-verification-settings">{definitions.map(([field,label,min,max,step])=><label key={field}>{label}<input aria-label={`구간 검증 ${field}`} type="number" min={min} max={max} step={step} value={settings[field]} onChange={e=>{const v=e.target.valueAsNumber;if(Number.isFinite(v))setSettings(s=>({...s,[field]:v}));}}/></label>)}</div><p>표본 FIT/VALIDATION에 각각 Parent 3개와 비평행 방향·공간 분포를 요구합니다. 수치는 실행 설정이며 보편적인 안정성 정답이나 정확도 확률이 아닙니다.</p></details>
  {!roi&&<p>완료된 사용자 Target ROI를 먼저 불러오거나 지정하세요.</p>}
  <button type="button" aria-label="구조 구간 정렬 실행" disabled={busy||!ready||!roi||hasPending||pairs.some(p=>!completePair(p))} onClick={()=>void run()}>{busy?"구간 검증 중…":"검토된 구조 구간으로 정렬·검증"}</button>
  {!ready&&!busy&&<button type="button" aria-label="구간 검증 연결 재시도" onClick={()=>{const s=stamp.current;void sync(s).then(()=>{if(currentCheck(s)){setReady(true);setError(null);}}).catch(e=>setError(e.message));}}>검증 연결 재시도</button>}
  <p role="status">Anchor·ROI·설정을 변경하거나 제외하면 이전 검증과 승인은 무효화됩니다. 현재 결과가 없으면 다시 실행하세요.</p>
  {error&&<p role="alert" className="error">{error}</p>}
  {current&&<div className="structural-verification-result" data-line-run-id={current.run_id}>
   <h4>상태: <output data-line-alignment-status={current.status}>{states[current.status]}</output></h4>
   <p>검토 기록: <output data-line-review>{current.review.state}</output> · FIT {current.fit_ids.length} / VALIDATION {current.validation_ids.length}</p>
   {current.execution_context==="TEST_ONLY"&&<p className="error">TEST ONLY 검증 기록입니다. 실제 사용자 안정 구조 승인이나 GT로 사용하지 마세요.</p>}
   <ul>{current.reasons.map((r,i)=><li key={i}>{r}</li>)}</ul>
   <details><summary>추정·검증에 사용하거나 제외한 근거</summary>{current.eligibility.map(e=><p key={e.id}>{e.id}: {e.eligible?"사용":"미사용"} · {e.reason}</p>)}</details>
   {dist("FIT",current.fit_distribution)}{dist("VALIDATION",current.validation_distribution)}
   {current.fit_conditioning&&<details><summary>추정 식별 가능성</summary><pre>{JSON.stringify(current.fit_conditioning,null,2)}</pre></details>}
   {current.rows.length>0&&<div className="structural-anchor-table"><table><thead><tr><th>구간 / Parent</th><th>용도</th><th>법선 최대 / RMS · Ref px</th><th>방향 · 도</th><th>가시 잉크 Ref / Target</th><th>공통 분석 범위 · Ref px</th><th>공통 범위 지지</th></tr></thead><tbody>{current.rows.map(r=><tr key={r.id} data-line-row={r.id}><td>{r.id}<br/>{r.parent_structure_id}</td><td>{r.usage}</td><td>{number(r.normal_max_ref_px)} / {number(r.normal_rmse_ref_px)}</td><td>{number(r.angle_degrees)}</td><td>{number(r.visible_ink_support.ref)} / {number(r.visible_ink_support.target)}</td><td>{number(r.analysis_interval_overlap_ref_px)}</td><td>{number(r.common_window_support_min)}</td></tr>)}</tbody></table></div>}
   {current.coverage&&<p>VALIDATION 탐침 띠 / ROI: {(current.coverage.validation_probe_band_roi_fraction*100).toFixed(3)}%. 띠와 Hull 내부도 전체 검증 영역이 아닙니다. 끝점·물리적 길이·개구부는 미검증입니다.</p>}
   {current.provisional_mapping&&<details><summary>Target ROI → Ref ROI 임시 좌표 · 확정 매핑 아님</summary><p>이미지 밖 꼭짓점: {current.provisional_mapping.outside_ref_vertex_indices.join(", ")||"없음"}</p><pre>{JSON.stringify(current.provisional_mapping,null,2)}</pre></details>}
   {current.preview_modes.length>0&&<div className="structural-verification-preview">
    <label>정렬 진단 보기<select aria-label="구간 정렬 미리보기" value={preview} onChange={e=>setPreview(e.target.value)}>{current.preview_modes.map(m=><option key={m} value={m}>{m==="overlay"?"빨강 Ref / 청록 Target 겹침":m==="warped_ref"?"Target 좌표의 임시 Ref":"VALIDATION 탐침 띠"}</option>)}</select></label>
    <p>별도 임시 Target 좌표 화면입니다. 초록 Polygon은 원래 사용자 ROI이고, 청록/보라 선은 FIT/VALIDATION 선택 범위입니다.</p>
    <DrawingViewer key={`${current.run_id}:${preview}`} image={{...targetImage,id:`line:${current.run_id}:${preview}`,url:`/api/structural-alignments/${current.run_id}/preview/${preview}`}} role="Target" inspection={{polygon:roi?.vertices}} onImageError={()=>{setRecord(null);setError("임시 미리보기가 만료됐습니다. 다시 검증하세요.");}}/>
   </div>}
   <label>검증 결과 검토 근거<input aria-label="구간 정렬 승인 근거" value={reason} onChange={e=>setReason(e.target.value)} maxLength={2000}/></label>
   <label><input type="checkbox" aria-label="구간 정렬 표본 범위 확인" checked={confirmed} onChange={e=>setConfirmed(e.target.checked)}/>선택 선 표본의 위치·방향·가시 지지 검증 범위만 확인합니다. 물리적 끝점·개구부·ROI 전체는 미검증입니다.</label>
   <div className="alignment-actions"><button type="button" aria-label="구간 정렬 결과 승인" disabled={busy||!current.approval_eligible||!confirmed||reason.trim().length<3} onClick={()=>void review("approve")}>선택 선 표본 검증 승인</button><button type="button" aria-label="구간 정렬 결과 기각" disabled={busy||reason.trim().length<3} onClick={()=>void review("reject")}>검증 결과 기각</button><button type="button" aria-label="구간 정렬 JSON 저장" onClick={download}>구간 검증 JSON 저장</button></div>
   <p>표본 승인 후에도 ROI 확정 매핑은 생성하지 않습니다. 원본 좌표와 도면 파일은 유지됩니다.</p>
  </div>}
 </section>;
}
