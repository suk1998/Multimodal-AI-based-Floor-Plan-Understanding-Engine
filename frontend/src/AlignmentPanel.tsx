import {useEffect,useRef,useState} from "react";
import type {NativeImage} from "./api";
import type {TargetROI,Vertex} from "./roi";
import {DrawingViewer} from "./DrawingViewer";
import {runAlignment,reviewAlignment} from "./alignmentApi";
import type {AlignmentAnchor,AlignmentResult} from "./alignmentApi";
interface Draft {id:string;ref:Vertex|null;target:Vertex|null;role:"fit"|"check";reason:string;confirmed:boolean;enabled:boolean;residual_ref_px?:number}
const errorText=(e:unknown)=>e instanceof Error?e.message:"정렬 요청을 완료하지 못했습니다.";
const fixed=(value:number|null|undefined)=>value==null?"미측정":value.toFixed(3);
export function AlignmentPanel({refImage,targetImage,roi}:{refImage:NativeImage;targetImage:NativeImage;roi:TargetROI|null}){
 const [open,setOpen]=useState(false),[rows,setRows]=useState<Draft[]>([]),[active,setActive]=useState<string|null>(null);
 const [result,setResult]=useState<AlignmentResult|null>(null),[busy,setBusy]=useState(false),[error,setError]=useState<string|null>(null);
 const [reason,setReason]=useState(""),[confirmed,setConfirmed]=useState(false),[previewMode,setPreviewMode]=useState("overlay");
 const generation=useRef(0),controller=useRef<AbortController|null>(null),roiKey=JSON.stringify(roi);
 function invalidate(){generation.current++;controller.current?.abort();controller.current=null;setResult(null);setBusy(false);setError(null);setConfirmed(false);setReason("");}
 useEffect(()=>{invalidate();setRows([]);setActive(null);return()=>{generation.current++;controller.current?.abort();};},[roiKey,refImage.id,targetImage.id]);
 function editRows(next:Draft[]){invalidate();setRows(next);}
 function update(id:string,patch:Partial<Draft>){editRows(rows.map(r=>r.id===id?{...r,...patch}:r));}
 function add(role:"fit"|"check"){
  if(rows.length>=128)return;let id=`point-${rows.length+1}`;while(rows.some(r=>r.id===id))id+="a";
  editRows([...rows,{id,ref:null,target:null,role,reason:"",confirmed:false,enabled:true}]);setActive(id);
 }
 async function run(mode:"auto"|"manual"){
  if(!roi)return;
  const selected=rows.filter(r=>r.enabled);
  if(mode==="manual"&&selected.some(r=>!r.ref||!r.target)){setError("활성 대응점의 Ref와 Target 좌표를 모두 지정하세요.");return;}
  invalidate();const token=generation.current,abort=new AbortController();controller.current=abort;setBusy(true);
  try{
   const response=await runAlignment(refImage,targetImage,roi,mode,selected as AlignmentAnchor[],abort.signal);
   if(token!==generation.current)return;
   if(mode==="auto"){setRows(response.anchors.map(a=>({...a,enabled:true})));setActive(null);}
   setPreviewMode(response.preview_modes.includes("overlay")?"overlay":"anchors_target");setResult(response);
  }catch(e){if(token===generation.current)setError(errorText(e));}
  finally{if(token===generation.current){setBusy(false);controller.current=null;}}
 }
 async function review(decision:"approve"|"reject"){
  if(!result)return;const token=generation.current,abort=new AbortController();controller.current=abort;setBusy(true);
  try{const response=await reviewAlignment(result.run_id,decision,reason,confirmed,abort.signal);if(token===generation.current)setResult(response);}
  catch(e){if(token===generation.current)setError(errorText(e));}
  finally{if(token===generation.current){setBusy(false);controller.current=null;}}
 }
 const current=result&&JSON.stringify(result.target_roi)===roiKey?result:null;
 const pick=(side:"ref"|"target",p:{x:number;y:number})=>{if(active)update(active,{[side]:[p.x,p.y],confirmed:false});};
 function download(){if(!current)return;const url=URL.createObjectURL(new Blob([JSON.stringify(current,null,2)],{type:"application/json"}));const a=document.createElement("a");a.href=url;a.download=`alignment_page_${targetImage.page}_${current.run_id}.json`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
 const previewBase=previewMode==="anchors_ref"?refImage:targetImage;
 const temporary=current&&current.preview_modes.includes(previewMode)?{...previewBase,id:`${previewBase.id}:inspection:${current.run_id}`,url:`/api/alignments/${current.run_id}/preview/${previewMode}`,document:{...previewBase.document,filename:previewMode==="anchors_ref"?"임시 Ref 구조 후보 · Ref 원본 좌표":"임시 정렬 확인 · 원본 Target 좌표"}}:null;
 return <section className="alignment-panel" aria-label="구조 정렬 및 ROI 매핑">
  <h2>Phase 2B · 구조 정렬 및 ROI 매핑</h2>
  <p>완료된 사용자 Target ROI로 비교 준비를 수행합니다. 자동 후보는 안정 구조 확정 결과가 아닙니다.</p>
  <button type="button" aria-label="정렬 도구 열기" disabled={!roi} onClick={()=>setOpen(!open)}>{open?"정렬 도구 접기":"정렬 도구 열기"}</button>
  {!roi&&<p role="status">먼저 Target ROI를 완료하거나 저장한 ROI JSON을 불러오세요.</p>}
  {open&&roi&&<>
   <div className="alignment-actions">
    <button type="button" aria-label="자동 정렬 실행" disabled={busy} onClick={()=>void run("auto")}>자동 후보 정렬 · 재시도</button>
    <button type="button" aria-label="수동 추정점 추가" disabled={rows.length>=128} onClick={()=>add("fit")}>추정점 추가</button>
    <button type="button" aria-label="독립 검증점 추가" disabled={rows.length>=128} onClick={()=>add("check")}>독립 검증점 추가</button>
    <button type="button" aria-label="대응점 선택 해제" onClick={()=>setActive(null)}>대응점 선택 해제</button>
    <button type="button" aria-label="수동 정렬 실행" disabled={busy||!rows.length} onClick={()=>void run("manual")}>선택 대응점으로 정렬</button>
   </div>
   <p className="hint">서로 다른 안정 벽 구간에 추정점 3개 이상, 추정에 쓰지 않는 검증점 3개 이상을 지정하세요. 대응점 행을 선택한 뒤 아래 임시 Ref와 Target에서 같은 구조의 위치를 각각 클릭합니다. 치수선·문자·가구·변경 구간을 제외하고 근거를 기록하세요. Shift+드래그는 화면 이동입니다.</p>
   <p role="status">{active?`좌표 수정 대상: ${active}. 임시 도면 클릭으로 해당 좌표를 수정합니다.`:"대응점 선택 없음 · 임시 도면 드래그는 화면 이동입니다."}</p>
   <div className="anchor-table"><table><thead><tr><th>사용</th><th>선택 / ID</th><th>용도</th><th>Ref 픽셀</th><th>Target 픽셀</th><th>안정 구조 근거</th><th>직전 잔차 Ref px</th><th>직접 확인</th></tr></thead><tbody>
    {rows.map(r=><tr key={r.id} className={active===r.id?"active-anchor":""}>
     <td><input type="checkbox" aria-label={`${r.id} 사용`} checked={r.enabled} onChange={e=>update(r.id,{enabled:e.target.checked})}/></td>
     <td><label><input type="radio" name="alignment-anchor" checked={active===r.id} onChange={()=>setActive(r.id)}/>{r.id}</label></td>
     <td><select aria-label={`${r.id} 용도`} value={r.role} onChange={e=>update(r.id,{role:e.target.value as "fit"|"check"})}><option value="fit">추정</option><option value="check">독립 검증</option></select></td>
     <td>{r.ref?.map(v=>v.toFixed(2)).join(", ")??"클릭 필요"}</td><td>{r.target?.map(v=>v.toFixed(2)).join(", ")??"클릭 필요"}</td>
     <td><input aria-label={`${r.id} 근거`} value={r.reason} maxLength={1000} onChange={e=>update(r.id,{reason:e.target.value,confirmed:false})}/></td>
     <td>{fixed(r.residual_ref_px)}</td><td><input type="checkbox" aria-label={`${r.id} 안정 구조 확인`} checked={r.confirmed} onChange={e=>update(r.id,{confirmed:e.target.checked})}/></td>
    </tr>)}
   </tbody></table></div>
   <div className="comparison temporary-comparison" aria-label="임시 대응점 지정">
    <DrawingViewer image={refImage} role="Ref" inspection={{selectionKey:active,points:rows.filter(r=>r.enabled&&r.ref).map(r=>({id:r.id,point:r.ref!})),polygon:current?.mapping?.ref_vertices_unclipped,onPick:active?p=>pick("ref",p):undefined}}/>
    <DrawingViewer image={targetImage} role="Target" inspection={{selectionKey:active,points:rows.filter(r=>r.enabled&&r.target).map(r=>({id:r.id,point:r.target!})),polygon:roi.vertices,onPick:active?p=>pick("target",p):undefined}}/>
   </div>
   {busy&&<p role="status">정렬 / 검증 요청 중…</p>}{error&&<p role="alert" className="error">{error}</p>}
   {current&&<section className="alignment-result" aria-label="정렬 결과">
    <h3>정렬 상태: <output data-alignment-status>{current.status}</output> · 승인: <output data-alignment-review>{current.review.state}</output></h3>
    <p>변환 방향: 원본 Target 픽셀 → 원본 Ref 픽셀. 낮은 잔차만으로 ROI 전체 검증을 선언하지 않습니다.</p>
    <ul>{current.reasons.map((r,i)=><li key={i}>{r}</li>)}</ul>
    <p>추정 RMSE: {fixed(current.residuals?.fit_ref_px.rmse)} Ref px · 미사용 검증점: {current.residuals?.check_ref_px.count??0}개 · 검증 P95: {fixed(current.residuals?.check_ref_px.p95)} Ref px</p>
    {current.mapping&&<>
     <p>Ref 이미지 경계 안: {(current.mapping.valid_fraction*100).toFixed(2)}% · Ref 밖: {current.mapping.outside_ref_pixels} Target 픽셀 · 검증 표본 Hull과 ROI 교집합: {(current.mapping.sampled_hull_roi_fraction*100).toFixed(2)}%</p>
     <p className="hint">Hull은 검증점의 공간 분포 지표입니다. 내부 전체의 검증 완료 영역이 아닙니다. 회색은 Ref 외부, 황색은 검증 표본 Hull 밖의 ROI입니다.</p>
     <details><summary>변환 및 대응 Ref ROI 원본 좌표</summary><pre>{JSON.stringify({target_to_ref:current.matrix,ref_to_target:current.inverse_matrix,ref_roi:current.mapping.ref_vertices_unclipped},null,2)}</pre></details>
    </>}
    <div className="alignment-actions">{current.preview_modes.map(mode=><button type="button" key={mode} aria-pressed={previewMode===mode} onClick={()=>setPreviewMode(mode)}>{({overlay:"겹쳐 보기 · Ref 빨강 / Target 청록",warped_ref:"정렬된 Ref",coverage:"유효 범위",anchors_ref:"Ref 구조 후보",anchors_target:"Target 구조 후보"} as Record<string,string>)[mode]??mode}</button>)}</div>
    {temporary&&<DrawingViewer image={temporary} role="Target" onImageError={()=>{invalidate();setError("임시 정렬 미리보기를 불러올 수 없습니다. 결과 만료 또는 서버 재시작 여부를 확인하고 다시 실행하세요.");}} inspection={{polygon:previewMode==="anchors_ref"?undefined:roi.vertices}}/>}
    {previewMode==="anchors_ref"&&<p className="error">Ref 후보 화면은 Ref 원본 좌표입니다. 정렬된 Target 좌표 확인은 겹쳐 보기에서 수행하세요.</p>}
    <label>검토 근거<input aria-label="정렬 검토 근거" value={reason} maxLength={2000} onChange={e=>setReason(e.target.value)}/></label>
    <label><input type="checkbox" aria-label="정렬 안정 구조 최종 확인" checked={confirmed} onChange={e=>setConfirmed(e.target.checked)}/>사용된 구조와 독립 검증 구간을 확인했으며 치수·문자·변경 구간을 기준에서 제외했습니다.</label>
    <div className="alignment-actions">
     <button type="button" aria-label="정렬 결과 승인" disabled={busy||!current.approval_eligible||!confirmed||reason.trim().length<3} onClick={()=>void review("approve")}>기록된 표본 범위 승인</button>
     <button type="button" aria-label="정렬 결과 기각" disabled={busy||reason.trim().length<3} onClick={()=>void review("reject")}>기각 · 보정 후 재시도</button>
     <button type="button" aria-label="정렬 결과 JSON 저장" onClick={download}>근거·변환 JSON 저장</button>
    </div>
    {current.review.warning&&<p>{current.review.warning}</p>}
   </section>}
  </>}
 </section>;
}
