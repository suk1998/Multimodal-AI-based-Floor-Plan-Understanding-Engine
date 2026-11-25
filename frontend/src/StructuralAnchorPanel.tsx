import {useEffect,useRef,useState} from "react";
import type {NativeImage} from "./api";
import type {TargetROI,Vertex} from "./roi";
import {StructuralAlignmentVerification} from "./StructuralAlignmentVerification";
import {DrawingViewer} from "./DrawingViewer";
import type {FocusRegion,StructuralInteraction} from "./DrawingViewer";
import {anchorReferences,referenceImages,referenceReportSHA256} from "./structuralAnchorReferences";
import {
  bindingFor,bindingsMatch,checkedSegment,completePair,invalidatePair,makeAnchorFile,MAX_ANCHOR_BYTES,MAX_ANCHORS,
  newPair,parentConflicts,patchPair,readAnchorFile,reviewPair,
} from "./structuralAnchors";
import type {AnchorUsage,BoundaryKind,StructuralAnchorPair} from "./structuralAnchors";

type Side="ref"|"target";
type Tool="PAN"|"REF"|"TARGET"|"EDIT";
type Pending=Record<string,Partial<Record<Side,Vertex[]>>>;
const sideName=(side:Side)=>side==="ref"?"Ref":"Target";
const usageText={FIT:"FIT · 정렬용",VALIDATION:"VALIDATION · 별도 검증용",HOLD:"HOLD · 보류",EXCLUDE:"EXCLUDE · 제외"};
const boundaryText={OUTER:"외측선",INNER:"내측선",OTHER:"기타",UNDETERMINED:"미확정"};
const reviewText={UNREVIEWED:"미검토",REVIEWED:"대응 검토 완료",INVALIDATED:"재검토 필요",REJECTED:"대응 기각"};
const pointText=(points:Vertex[]|null|undefined)=>points?.map(p=>`[${p.map(v=>v.toFixed(2)).join(", ")}]`).join(" → ")??"미입력";
const errorText=(e:unknown)=>e instanceof Error?e.message:"Anchor 작업을 완료하지 못했습니다.";

export function StructuralAnchorPanel({refImage,targetImage,roi}:{refImage:NativeImage;targetImage:NativeImage;roi:TargetROI|null}){
 const [open,setOpen]=useState(false),[pairs,setPairs]=useState<StructuralAnchorPair[]>([]),[selectedId,setSelectedId]=useState<string|null>(null);
 const [tool,setTool]=useState<Tool>("PAN"),[pending,setPending]=useState<Pending>({}),[toolRevision,setToolRevision]=useState(0);
 const [focus,setFocus]=useState<{ref?:FocusRegion;target?:FocusRegion}>({});
 const [notice,setNotice]=useState("구조 Anchor는 아직 없습니다. 양쪽 경계를 각각 지정하세요."),[error,setError]=useState<string|null>(null);
 const [identityChecked,setIdentityChecked]=useState(false),[scopeChecked,setScopeChecked]=useState(false),[reading,setReading]=useState(false);
 const loadGeneration=useRef(0),alive=useRef(true),focusRevision=useRef(0);
 const bindingKey=JSON.stringify([bindingFor(refImage),bindingFor(targetImage)]),roiKey=JSON.stringify(roi);
 const currentBinding=useRef(bindingKey);currentBinding.current=bindingKey;
 const priorBinding=useRef(bindingKey),priorROI=useRef(roiKey);
 const selected=pairs.find(p=>p.id===selectedId)??null;
 const conflicts=parentConflicts(pairs);
 const selectedPending=selected?pending[selected.id]:undefined;
 const hasPending=Object.values(pending).some(p=>p.ref!==undefined||p.target!==undefined);
 const hasSelectedPending=Boolean(selectedPending&&(selectedPending.ref!==undefined||selectedPending.target!==undefined));
 const referenceAvailable=bindingsMatch(referenceImages.ref,refImage)&&bindingsMatch(referenceImages.target,targetImage);
 const activeReference=selected?.source.reference_id?anchorReferences.find(r=>r.id===selected.source.reference_id):undefined;

 useEffect(()=>()=>{alive.current=false;loadGeneration.current++;},[]);
 useEffect(()=>{
  if(priorBinding.current!==bindingKey){
   priorBinding.current=bindingKey;loadGeneration.current++;setPairs([]);setPending({});setSelectedId(null);setTool("PAN");setReading(false);setFocus({});
   setNotice("입력 도면이 바뀌어 기존 Anchor와 검토 상태를 적용하지 않습니다.");
  }
 },[bindingKey]);
 useEffect(()=>{
  if(priorROI.current!==roiKey){
   priorROI.current=roiKey;loadGeneration.current++;setReading(false);setPending({});setTool("PAN");setToolRevision(v=>v+1);
   setPairs(previous=>previous.map(p=>invalidatePair(p,"Target ROI 문맥이 바뀌어 구간 재검토 필요")));
   setNotice("Target ROI 문맥이 바뀌었습니다. 완성 구간은 보존하고 기존 검토는 무효화했습니다.");
  }
 },[roiKey]);
 useEffect(()=>{setIdentityChecked(false);setScopeChecked(false);},[selectedId,selected?.revision]);
 function cancelLoad(){loadGeneration.current++;setReading(false);setError(null);}
 function command(){cancelLoad();setToolRevision(v=>v+1);}
 function patch(id:string,fields:Parameters<typeof patchPair>[1],why:string){
  cancelLoad();setPairs(previous=>previous.map(p=>p.id===id?patchPair(p,fields,why):p));
 }
 function addPair(){
  command();const pair=newPair();setPairs(previous=>[...previous,pair]);setSelectedId(pair.id);
  setPending(previous=>({...previous,[pair.id]:{ref:[]}}));setTool("REF");
  setNotice("새 Pair를 만들었습니다. 구조 Anchor Ref 화면에서 분석 구간의 두 점을 클릭하세요. Target은 별도로 지정합니다.");
 }
 function arm(side:Side){
  if(!selected)return;command();setIdentityChecked(false);setScopeChecked(false);
  setPairs(previous=>previous.map(p=>p.id===selected.id?invalidatePair(p,`${sideName(side)} 분석 구간 재선택 시작`):p));
  setPending(previous=>({...previous,[selected.id]:{...previous[selected.id],[side]:previous[selected.id]?.[side]??[]}}));
  setTool(side==="ref"?"REF":"TARGET");setNotice(`${sideName(side)} 구간 지정 모드입니다. 두 점은 분석 범위이며 Landmark 대응점이 아닙니다.`);
 }
 function clearPendingSide(id:string,side:Side){
  setPending(previous=>{const next={...previous},row={...next[id]};delete row[side];if(row.ref===undefined&&row.target===undefined)delete next[id];else next[id]=row;return next;});
 }
 function addPoint(side:Side,point:{x:number;y:number}){
  if(!selected||(tool!=="REF"&&tool!=="TARGET")||(tool==="REF")!==(side==="ref"))return;
  const points=pending[selected.id]?.[side];if(!points||points.length>=2)return;
  cancelLoad();const value:Vertex=[point.x,point.y];
  if(!points.length){setPending(previous=>({...previous,[selected.id]:{...previous[selected.id],[side]:[value]}}));return;}
  try{
   const segment=checkedSegment([points[0],value],side==="ref"?refImage:targetImage);
   patch(selected.id,{[side==="ref"?"ref_segment":"target_segment"]:segment},`${sideName(side)} 분석 구간 좌표 변경`);
   clearPendingSide(selected.id,side);setTool("PAN");setToolRevision(v=>v+1);
   setNotice(`${sideName(side)} 구간 두 점을 기록했습니다. 다른 쪽은 자동 대응하지 않습니다. Parent와 경계 종류·용도·근거를 확인하세요.`);
  }catch(e){setError(errorText(e));}
 }
 function moveEndpoint(side:Side,index:number,point:{x:number;y:number}){
  if(!selected||tool!=="EDIT")return;
  const original=side==="ref"?selected.ref_segment:selected.target_segment;if(!original)return;
  const next=original.map((p,i)=>i===index?[point.x,point.y]:p);
  try{const segment=checkedSegment(next,side==="ref"?refImage:targetImage);patch(selected.id,{[side==="ref"?"ref_segment":"target_segment"]:segment},`${sideName(side)} 구간 지정점 수정`);}catch(e){setError(errorText(e));}
 }
 function cancelInput(){
  command();if(selected)setPending(previous=>{const next={...previous};delete next[selected.id];return next;});setTool("PAN");
  setNotice("선택 Pair의 미완료 지정점만 취소했습니다. 기존 완성 구간은 유지하며 검토 상태를 자동 복원하지 않습니다.");
 }
 function selectPair(id:string){command();setSelectedId(id);setTool("PAN");setNotice("Pair를 선택했습니다. 완성 구간과 다른 Pair의 미완료 입력은 보존합니다.");}
 function deletePair(){
  if(!selected)return;command();setPairs(previous=>previous.filter(p=>p.id!==selected.id));setPending(previous=>{const next={...previous};delete next[selected.id];return next;});
  setSelectedId(null);setTool("PAN");setNotice("선택 Pair를 삭제했습니다. 원본 도면과 Target ROI는 유지합니다.");
 }
 function focusPair(p=selected){
  if(!p)return;const token=String(++focusRevision.current);const next:{ref?:FocusRegion;target?:FocusRegion}={};
  for(const side of ["ref","target"] as Side[]){const points=pending[p.id]?.[side]??p[side==="ref"?"ref_segment":"target_segment"];
   if(!points?.length)continue;const x=points.map(v=>v[0]),y=points.map(v=>v[1]);next[side]={key:token+side,bounds:[Math.min(...x)-80,Math.min(...y)-80,Math.max(...x)+80,Math.max(...y)+80]};}
  setFocus(next);
 }
 function useReference(id:string){
  if(!referenceAvailable||pairs.length>=MAX_ANCHORS)return;const reference=anchorReferences.find(r=>r.id===id);if(!reference)return;
  command();const p={...newPair(),parent_structure_id:reference.parent_structure_id,boundary_kind:reference.boundary_kind,
   ref_segment:structuredClone(reference.ref_segment),target_segment:structuredClone(reference.target_segment),
   source:{kind:"ANALYST_REFERENCE" as const,reference_id:id,report_sha256:referenceReportSHA256}};
  setPairs(previous=>[...previous,p]);setSelectedId(p.id);setTool("PAN");focusPair(p);
  setNotice(`${id} 참고 좌표를 HOLD / 미검토 초안으로만 추가했습니다. 직접 확인과 검토 근거가 필요합니다.`);
 }
 function review(status:"REVIEWED"|"REJECTED"){
  if(!selected)return;command();try{const next=reviewPair(selected,status,identityChecked,scopeChecked);setPairs(previous=>previous.map(p=>p.id===selected.id?next:p));
   setNotice(status==="REVIEWED"?"구간 대응 검토를 기록했습니다. Stable Structure 확정이나 정렬 성공을 의미하지 않습니다.":"구간 대응 기각을 기록했습니다. 좌표와 근거는 보존합니다.");
  }catch(e){setError(errorText(e));}
 }
 async function load(file:File){
  const token=++loadGeneration.current,inputKey=bindingKey;setReading(true);setError(null);
  try{
   if(file.size>MAX_ANCHOR_BYTES)throw Error("Anchor JSON은 2MiB 이하여야 합니다.");
   const text=await file.text();if(!alive.current||token!==loadGeneration.current||currentBinding.current!==inputKey)return;
   const parsed=readAnchorFile(text,refImage,targetImage);setPairs(parsed.pairs);setSelectedId(parsed.pairs[0]?.id??null);setPending({});setTool("PAN");setToolRevision(v=>v+1);setIdentityChecked(false);setScopeChecked(false);setFocus({});
   setNotice("Anchor 좌표와 이력을 불러왔습니다. 과거 검토는 현재 사용 전 재검토 대상으로 표시합니다. 저장된 ROI 문맥은 현재 Target ROI를 덮어쓰지 않습니다.");
  }catch(e){if(alive.current&&token===loadGeneration.current)setError(errorText(e));}
  finally{if(alive.current&&token===loadGeneration.current)setReading(false);}
 }
 function save(){
  try{if(hasPending)throw Error("미완료 구간 입력을 마치거나 취소한 뒤 저장하세요.");const file=makeAnchorFile(pairs,refImage,targetImage,roi),url=URL.createObjectURL(new Blob([JSON.stringify(file,null,2)],{type:"application/json"}));
   const a=document.createElement("a");a.href=url;a.download=`structural_anchors_ref${refImage.page}_${refImage.zoom}x_target${targetImage.page}_${targetImage.zoom}x.json`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);setNotice("구조 Anchor JSON 다운로드를 요청했습니다. 브라우저 다운로드 폴더에서 확인하세요.");setError(null);
  }catch(e){setError(errorText(e));}
 }
 function interaction(side:Side):StructuralInteraction {
  const drawing=(tool==="REF"&&side==="ref")||(tool==="TARGET"&&side==="target"),mode=drawing?"DRAW":tool==="EDIT"?"EDIT":"PAN";
  const count=selected?pending[selected.id]?.[side]?.length??0:0;
  return {mode,activeId:selectedId,interactionKey:`${selectedId}:${tool}:${toolRevision}`,
   message:mode==="DRAW"?`구조 Anchor 구간 선택 ${count}/2 · Landmark 아님`:mode==="EDIT"?"구조 Anchor 지정점 수정 · Shift+드래그 이동":"구조 Anchor 보기 · 드래그 이동",
   lines:pairs.map(p=>({id:p.id,usage:p.usage,selected:p.id===selectedId,points:pending[p.id]?.[side]??p[side==="ref"?"ref_segment":"target_segment"]??[]})),
   add:p=>addPoint(side,p),moveEndpoint:(index,p)=>moveEndpoint(side,index,p),cancel:cancelInput};
 }
 let saveProblem:string|null=null;
 if(hasPending)saveProblem="미완료 구간 지정점이 있습니다.";
 else if(pairs.some(p=>!completePair(p)))saveProblem="모든 Pair에 Ref·Target 두 점 구간과 Parent Structure ID를 입력하세요.";
 const canReview=selected&&completePair(selected)&&Boolean(selected.rationale.trim())&&!hasSelectedPending;
 return <section className="structural-anchor-panel" aria-label="수동 구조 Anchor 선택">
  <h2>Phase 2B-2 · 수동 구조 Anchor</h2>
  <p>Ref와 Target 경계 구간을 각각 지정해 대응·검토를 기록합니다. 검토된 FIT·VALIDATION 구간은 아래 별도 구간 정렬 검증에 연결됩니다.</p>
  <button type="button" aria-label="구조 Anchor 도구 열기" aria-expanded={open} onClick={()=>setOpen(!open)}>{open?"구조 Anchor 도구 접기":"구조 Anchor 도구 열기"}</button>
  {open&&<>
   <p className="anchor-semantics">지정한 두 점은 <strong>분석 구간의 범위</strong>입니다. 물리적 끝점·Landmark 대응을 뜻하지 않으며, 대응 검토 완료도 안정 구조 확정이나 정렬 성공을 뜻하지 않습니다.</p>
   <p className="hint">Ref {refImage.page}p · {refImage.zoom}배 · {refImage.width}×{refImage.height} · PDF {refImage.document.sha256.slice(0,12)}… / Target {targetImage.page}p · {targetImage.zoom}배 · {targetImage.width}×{targetImage.height} · PDF {targetImage.document.sha256.slice(0,12)}…</p>
   {referenceAvailable&&<details className="anchor-references"><summary>S01~S06 참고 후보 · 사용자 확인 전에는 HOLD / 미검토</summary>
    <p>S03은 x1040~1120 중간 구간만 참고합니다. M01의 변경 가능 종료부 x1161~1170을 안정 구간에 포함시키지 마세요.</p>
    <p>M04의 Target 평행선 x442.56과 유지된 기존 벽 x476.64는 자동 대응하지 않습니다. 양쪽 구간을 직접 확인하세요.</p>
    {anchorReferences.map(r=><div key={r.id}><strong>{r.id} · {r.name}</strong><p>{r.note}</p><button type="button" aria-label={`참고 ${r.id} 초안 추가`} disabled={pairs.length>=MAX_ANCHORS} onClick={()=>useReference(r.id)}>참고 좌표로 미검토 초안 추가·보기</button></div>)}
   </details>}
   <div className="structural-anchor-toolbar" role="group" aria-label="구조 Anchor 작업 모드">
    <button type="button" aria-label="구조 Anchor 새 Pair" disabled={pairs.length>=MAX_ANCHORS} onClick={addPair}>새 Pair</button>
    <button type="button" aria-label="구조 Anchor Ref 선택" disabled={!selected} aria-pressed={tool==="REF"} onClick={()=>arm("ref")}>{selectedPending?.ref!==undefined?"Ref 구간 계속":selected?.ref_segment?"Ref 구간 다시 선택":"Ref 구간 두 점 선택"}</button>
    <button type="button" aria-label="구조 Anchor Target 선택" disabled={!selected} aria-pressed={tool==="TARGET"} onClick={()=>arm("target")}>{selectedPending?.target!==undefined?"Target 구간 계속":selected?.target_segment?"Target 구간 다시 선택":"Target 구간 두 점 선택"}</button>
    <button type="button" aria-label="구조 Anchor 지정점 수정" disabled={!selected?.ref_segment||!selected.target_segment||hasSelectedPending} aria-pressed={tool==="EDIT"} onClick={()=>{command();setTool("EDIT");setNotice("선택 Pair의 흰 지정점을 드래그하세요. 좌표 변경 시 기존 검토가 무효화됩니다.");}}>지정점 수정</button>
    <button type="button" aria-label="구조 Anchor 이동 모드" aria-pressed={tool==="PAN"} onClick={()=>{command();setTool("PAN");setNotice("화면 이동 모드입니다. 미완료 지정점과 완성 구간을 보존합니다.");}}>도면 이동</button>
    <button type="button" aria-label="구조 Anchor 입력 취소" disabled={!hasSelectedPending} onClick={cancelInput}>선택 Pair 미완료 입력 취소</button>
    <button type="button" aria-label="구조 Anchor 구간 보기" disabled={!selected} onClick={()=>focusPair()}>선택 구간 보기</button>
    <button type="button" aria-label="구조 Anchor Pair 삭제" disabled={!selected} onClick={deletePair}>선택 Pair 삭제</button>
   </div>
   <p role="status" className="structural-anchor-notice">{notice}</p>
   <p className="anchor-legend">선 색상은 용도입니다: FIT 청록 · VALIDATION 보라 · HOLD 주황 점선 · EXCLUDE 회색 점선. 안정성 점수가 아닙니다.</p>
   {conflicts.length>0&&<div className="anchor-conflicts" role="alert">{conflicts.map(c=><p key={c.parent}>독립 검증 충돌: Parent <strong>{c.parent}</strong>가 FIT와 VALIDATION 양쪽에 있습니다. 같은 부모 구조는 독립 검증이 아닙니다. (대소문자·앞뒤 공백 무시)</p>)}</div>}
   <div className="comparison structural-anchor-comparison" aria-label="구조 Anchor 원본 구간 선택">
    <DrawingViewer image={refImage} role="Ref" inspection={{}} structural={interaction("ref")} focusRegion={focus.ref}/>
    <DrawingViewer image={targetImage} role="Target" inspection={{polygon:roi?.vertices}} structural={interaction("target")} focusRegion={focus.target}/>
   </div>
   {selected&&<section className="structural-anchor-editor" aria-label="선택 구조 Anchor 편집" data-selected-anchor={selected.id}>
    <h3>선택 Pair · <code>{selected.id}</code></h3>
    <p>검토 상태: <output data-anchor-review>{reviewText[selected.review.status]} ({selected.review.status})</output> · 구간 revision {selected.revision}</p>
    <div className="anchor-metadata-grid">
     <label>Parent Structure ID<input aria-label="구조 Anchor Parent ID" value={selected.parent_structure_id} maxLength={120} onChange={e=>patch(selected.id,{parent_structure_id:e.target.value},"Parent Structure ID 변경")}/></label>
     <label>경계 종류<select aria-label="구조 Anchor 경계 종류" value={selected.boundary_kind} onChange={e=>patch(selected.id,{boundary_kind:e.target.value as BoundaryKind},"경계 종류 변경")}>{Object.entries(boundaryText).map(([v,t])=><option key={v} value={v}>{t}</option>)}</select></label>
     <label>용도<select aria-label="구조 Anchor 용도" value={selected.usage} onChange={e=>patch(selected.id,{usage:e.target.value as AnchorUsage},"용도 변경")}>{Object.entries(usageText).map(([v,t])=><option key={v} value={v}>{t}</option>)}</select></label>
    </div>
    <p>Ref 분석 구간: <code data-anchor-ref-coordinates>{JSON.stringify(selected.ref_segment)}</code></p>
    <p>Target 분석 구간: <code data-anchor-target-coordinates>{JSON.stringify(selected.target_segment)}</code></p>
    <p className="hint">분석 구간의 길이와 지정점 순서는 물리적 벽 길이·끝점·개구부 일치 판정이 아닙니다. 양쪽 구간을 동일 길이로 강제하지 않습니다.</p>
    {activeReference&&<p className="anchor-reference-note">참고 {activeReference.id}: {activeReference.note} 참고 좌표를 수정했다면 원래 분석 범위의 근거를 그대로 적용하지 마세요.</p>}
    <label>사용자 검토 근거<textarea aria-label="구조 Anchor 검토 근거" value={selected.rationale} maxLength={2000} onChange={e=>patch(selected.id,{rationale:e.target.value},"검토 근거 수정")}/></label>
    <label><input type="checkbox" aria-label="구조 Anchor 동일성 가설 확인" checked={identityChecked} onChange={e=>setIdentityChecked(e.target.checked)}/>양쪽이 같은 구조 경계의 구간이라는 대응 가설을 직접 확인했습니다.</label>
    <label><input type="checkbox" aria-label="구조 Anchor 분석 범위 확인" checked={scopeChecked} onChange={e=>setScopeChecked(e.target.checked)}/>지정점은 분석 범위이며 물리적 끝점·길이·개구부 일치를 확인한 것이 아님을 구분했습니다.</label>
    <div className="alignment-actions">
     <button type="button" aria-label="구조 Anchor 대응 검토 완료" disabled={!canReview||!identityChecked||!scopeChecked} onClick={()=>review("REVIEWED")}>구간 대응 검토 완료</button>
     <button type="button" aria-label="구조 Anchor 대응 기각" disabled={!canReview} onClick={()=>review("REJECTED")}>대응 기각 기록</button>
    </div>
    {selected.review.reason&&<p>최근 검토/무효화 근거: {selected.review.reason}</p>}
    <details><summary>검토 이력 {selected.review_history.length}개</summary><pre>{JSON.stringify(selected.review_history,null,2)}</pre></details>
   </section>}
   <div className="structural-anchor-table"><table><thead><tr><th>선택 / ID</th><th>Parent</th><th>경계 / 용도</th><th>Ref 원본 구간</th><th>Target 원본 구간</th><th>검토</th></tr></thead><tbody>
    {pairs.map(p=><tr key={p.id} data-pair-id={p.id} data-pair-review={p.review.status} data-usage={p.usage} className={p.id===selectedId?"active-anchor":""}>
     <td><label><input type="radio" name="structural-pair" aria-label={`구조 Pair ${p.id} 선택`} checked={p.id===selectedId} onChange={()=>selectPair(p.id)}/><code>{p.id}</code></label>{p.source.kind==="ANALYST_REFERENCE"&&<span>참고 {p.source.reference_id} · 자동 승인 아님</span>}</td>
     <td>{p.parent_structure_id||"입력 필요"}</td><td>{boundaryText[p.boundary_kind]} / {p.usage}</td><td>{pointText(p.ref_segment)}{pending[p.id]?.ref!==undefined&&<em> · 지정 중</em>}</td><td>{pointText(p.target_segment)}{pending[p.id]?.target!==undefined&&<em> · 지정 중</em>}</td><td>{reviewText[p.review.status]}</td>
    </tr>)}
   </tbody></table></div>
   <div className="alignment-actions">
    <button type="button" aria-label="구조 Anchor JSON 저장" disabled={!pairs.length||Boolean(saveProblem)||reading} onClick={save}>Anchor JSON 저장</button>
    <label className="anchor-file-upload">Anchor JSON 불러오기<input aria-label="구조 Anchor JSON 불러오기" type="file" accept="application/json,.json" onChange={e=>{const file=e.target.files?.[0];e.target.value="";if(file)void load(file);}}/></label>
   </div>
   <p className="hint">JSON 불러오기는 현재 Anchor 목록을 교체합니다. 현재 ROI는 유지합니다. 입력 PDF·페이지·배율·크기가 다르면 적용을 거부하고, 불러온 과거 검토는 재확인해야 합니다.</p>
   {saveProblem&&<p className="hint">저장 전 확인: {saveProblem}</p>}{reading&&<p role="status">Anchor JSON 확인 중…</p>}{error&&<p role="alert" className="error">{error}</p>}
   <StructuralAlignmentVerification refImage={refImage} targetImage={targetImage} roi={roi} pairs={pairs} hasPending={hasPending||reading}/>
  </>}
 </section>;
}
