import {useEffect,useReducer,useRef,useState} from "react";
import type {ReactNode} from "react";
import type {NativeImage} from "./api";
import {DrawingViewer} from "./DrawingViewer";
import type {TargetROI,Vertex} from "./roi";
import {makeROI,readROI,MAX_ROI_BYTES} from "./roi";
import {initialEditor,reduceEditor} from "./roiEditor";
import type {EditorAction,EditorState} from "./roiEditor";
import type {ROIInteraction,RectangleGestureKind,Inspection} from "./DrawingViewer";
import type {Point} from "./geometry";
import {clippedPoint,isRectangle,rectangleBetween,rectangularize,resizeRectangle,translateRectangle} from "./rectangleROI";

interface RectangleDrag {kind:RectangleGestureKind;start:Vertex;index:number|null;before:EditorState;last:Vertex[];selected:number|null}
const copy=(points:Vertex[])=>points.map(p=>[...p] as Vertex);
export function TargetViewer({image,initialROI,onROIChange,compact=false,simple=false,comparisonAction,technicalDetails=false,observationOverlay,observationTools,candidateInspection,focusRegion}: {image: NativeImage;initialROI?:TargetROI;compact?:boolean;simple?:boolean;comparisonAction?:ReactNode;onROIChange?:(roi:TargetROI|null)=>void;technicalDetails?:boolean;observationOverlay?:ReactNode;observationTools?:ReactNode;candidateInspection?:Inspection;focusRegion?:import('./DrawingViewer').FocusRegion}) {
  const [editor,dispatch]=useReducer(reduceEditor,initialROI, roi => roi
    ? {...initialEditor(), points:roi.vertices, closed:true}
    : initialEditor());
  const {points,closed,drawing,mode,selected,interactionKey,gestureActive,correctionBackup}=editor;
  const state=useRef(editor);state.current=editor;
  const drag=useRef<RectangleDrag|null>(null),loadGeneration=useRef(0);
  const rectangular=isRectangle(points);
  const [error,setError]=useState<string|null>(null),[notice,setNotice]=useState("그리기를 누른 뒤 대각선으로 드래그해 비교 영역을 만드세요.");
  useEffect(()=>{
    if(gestureActive)return;
    let value:TargetROI|null=null;
    if(closed&&!drawing){try{value=makeROI(image,points);}catch{value=null;}}
    onROIChange?.(value);
  },[points,closed,drawing,gestureActive,image,onROIChange]);
  useEffect(()=>{if(candidateInspection)dispatch({type:"pan"});},[Boolean(candidateInspection)]);
  useEffect(()=>()=>{loadGeneration.current++;drag.current=null;},[]);
  function cancelGesture(){const active=drag.current;drag.current=null;if(active)dispatch({type:"restoreGesture",state:active.before});}
  function change(action:EditorAction){cancelGesture();loadGeneration.current++;setError(null);dispatch(action);}
  function beginRectangle(kind:RectangleGestureKind,point:Point,index:number|null){
    loadGeneration.current++;setError(null);
    const current=state.current,before={...current,points:copy(current.points),backup:current.backup?copy(current.backup):null,correctionBackup:current.correctionBackup?copy(current.correctionBackup):null};
    drag.current={kind,start:clippedPoint([point.x,point.y],image.width,image.height),index,before,last:copy(current.points),selected:index};
    updateRectangle(point);
  }
  function updateRectangle(point:Point){
    const active=drag.current;if(!active)return;
    const p:Vertex=[point.x,point.y];
    const next=active.kind==="draw"?rectangleBetween(active.start,p,image.width,image.height)
      :active.kind==="resize"?resizeRectangle(active.before.points,active.index!,p,image.width,image.height)
      :translateRectangle(active.before.points,[p[0]-active.start[0],p[1]-active.start[1]],image.width,image.height);
    const clipped=clippedPoint(p,image.width,image.height);
    active.last=next;active.selected=active.kind==="resize"?next.findIndex(v=>v[0]===clipped[0]&&v[1]===clipped[1]):null;
    dispatch({type:"rectanglePreview",points:next,closed:active.kind!=="draw",selected:active.selected});
  }
  function finishRectangle(){
    const active=drag.current;if(!active)return;drag.current=null;
    try{
      makeROI(image,active.last);
      dispatch({type:"rectangleComplete",points:active.last,created:active.kind==="draw",selected:active.selected});
      if(compact&&active.kind==="draw")dispatch({type:"pan"});
      setNotice(active.kind==="draw"?"직사각형을 만들었습니다. 모서리로 크기를, 내부 드래그로 위치를 바꿀 수 있습니다.":active.kind==="resize"?"직사각형을 유지하며 크기를 수정했습니다.":"크기를 유지하며 비교 영역 전체를 이동했습니다.");
    }catch{
      dispatch({type:"restoreGesture",state:active.before});setNotice("너비와 높이가 있는 영역을 드래그하세요. 이전 좌표는 유지했습니다.");
    }
  }
  function deselect(){change({type:"deselect"});setNotice("모서리 선택을 해제했습니다. 비교 영역은 유지합니다.");}
  function cancelDrawing(){change({type:"cancelDrawing"});setNotice(editor.backup?"새 그리기를 취소하고 이전 비교 영역을 복원했습니다.":"그리기를 취소했습니다. 비교 영역 미지정 상태입니다.");}
  function convert(){
    try{const next=rectangularize(points,image.width,image.height);makeROI(image,next);change({type:"rectangularize",points:next});setNotice("min/max 좌표로 직사각형 보정했습니다. 보정 취소로 이전 좌표를 복원할 수 있습니다.");}
    catch(e){setError((e as Error).message);}
  }
  let validation:string|null=null;
  if(closed&&!gestureActive){try{makeROI(image,points);}catch(e){validation=(e as Error).message;}}
  async function load(file:File){
    cancelGesture();const generation=++loadGeneration.current;setError(null);
    try{
      if(file.size>MAX_ROI_BYTES)throw Error("ROI JSON은 1MiB 이하여야 합니다.");
      const roi=readROI(await file.text(),image);
      if(generation!==loadGeneration.current)return;
      dispatch({type:"load",points:roi.vertices});if(compact)dispatch({type:"pan"});setNotice(isRectangle(roi.vertices)?"직사각형 비교 영역을 불러왔습니다. 모서리 크기 수정과 내부 이동이 가능합니다.":"기존 다각형 좌표를 그대로 불러왔습니다. 직사각형 보정을 선택하면 수평·수직으로 맞춥니다.");
    }catch(e){if(generation===loadGeneration.current)setError((e as Error).message);}
  }
  function save(){
    if(!closed||gestureActive)return;
    try{
      const roi=makeROI(image,points),url=URL.createObjectURL(new Blob([JSON.stringify(roi,null,2)],{type:"application/json"}));
      const a=document.createElement("a");a.href=url;a.download=`target_page_${image.page}_${image.zoom}x_roi.json`;
      a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);setError(null);setNotice("영역 파일 다운로드를 요청했습니다. 브라우저 다운로드 폴더에서 확인하세요.");
    }catch(e){setError((e as Error).message);}
  }
  const interaction:ROIInteraction={mode,points,closed,selected,drawing,interactionKey,
    add:p=>change({type:"add",point:[p.x,p.y]}),
    move:(index,p)=>change({type:"move",index,point:[p.x,p.y]}),
    select:index=>change({type:"select",index}),cancel:()=>{if(drawing)cancelDrawing();else deselect();},finish:finishRectangle,
    rectangle:{enabled:drawing||rectangular||gestureActive,begin:beginRectangle,update:updateRectangle,end:finishRectangle,cancel:cancelGesture}};
  const fullPagePoints:Vertex[]=[[0,0],[image.width,0],[image.width,image.height],[0,image.height]];
  const wholePage=closed&&rectangular&&points.every(p=>(p[0]===0||p[0]===image.width)&&(p[1]===0||p[1]===image.height));
  const drawButton=<button type="button" aria-label="Target ROI 그리기" aria-pressed={mode==="draw"} disabled={compact&&drawing}
    onClick={()=>{change({type:"start"});setNotice("비교할 부분을 감싸도록 대각선으로 드래그하세요.");}}>
    {compact?(closed?"범위 다시 선택":"사각형 그리기"):"직사각형 그리기"}
  </button>;
  const cancelButton=<button type="button" aria-label="Target ROI 그리기 취소" hidden={!drawing} onClick={cancelDrawing}>그리기 취소</button>;
  const editTools=<>
    <div className="roi-buttons">
      {!compact&&drawButton}
      <button type="button" aria-label="Target ROI 수정" hidden={!closed&&!technicalDetails} aria-pressed={mode==="edit"} disabled={!closed}
        onClick={()=>{change({type:"edit"});setNotice(rectangular?"모서리를 드래그해 크기를 바꾸거나 사각형 안을 드래그해 옮기세요.":"꼭짓점을 드래그해 범위를 바꾸세요.");}}>
        {rectangular?"크기·위치 수정":"꼭짓점 수정"}
      </button>
      <button type="button" aria-label="Target ROI 이동 모드" hidden={!points.length&&!drawing&&!technicalDetails} aria-pressed={mode==="pan"}
        onClick={()=>{if(compact&&drawing){cancelDrawing();return;}change({type:"pan"});setNotice("도면을 움직여도 선택한 범위는 유지됩니다.");}}>도면 이동</button>
      <button type="button" aria-label="Target ROI 직사각형 보정" hidden={!closed||rectangular} disabled={!closed||gestureActive} onClick={convert}>직사각형 보정</button>
      <button type="button" aria-label="Target ROI 보정 취소" hidden={!correctionBackup} disabled={gestureActive}
        onClick={()=>{change({type:"cancelCorrection"});setNotice("보정 전 범위로 되돌렸습니다.");}}>보정 취소</button>
      <button type="button" aria-label="Target ROI 선택 취소" hidden={selected===null&&!technicalDetails} disabled={selected===null} onClick={deselect}>선택 취소</button>
      {!compact&&cancelButton}
      <button type="button" aria-label="Target ROI 초기화" hidden={!closed&&!drawing&&!technicalDetails}
        onClick={()=>{change({type:"reset"});setNotice("비교 범위를 지웠습니다.");}}>범위 지우기</button>
    </div>
    {!simple&&<div className="roi-buttons roi-file-actions">
      <button type="button" aria-label="Target ROI 저장" hidden={!closed&&!technicalDetails} disabled={!closed||gestureActive||Boolean(validation)} onClick={save}>범위 저장</button>
      <label className="roi-upload">저장한 범위 불러오기
        <input aria-label="Target ROI 불러오기" type="file" accept="application/json,.json"
          onChange={e=>{const file=e.target.files?.[0];e.target.value="";if(file)void load(file);}}/>
      </label>
    </div>}
    <details hidden={!technicalDetails}><summary>원본 좌표 확인</summary><pre data-roi-coordinates>{JSON.stringify(points)}</pre><pre data-roi-before-correction>{JSON.stringify(correctionBackup)}</pre></details>
  </>;
  const tools=<section className="roi-tools" aria-label="Target Polygon ROI" data-drawing={drawing} data-rectangle={rectangular} data-gesture-active={gestureActive}>
    <h3>Target에서 비교할 범위</h3>
    {compact?<>{(drawing||!closed)&&<p className="scope-instruction">{drawing
      ?"위 도면에서 비교할 부분을 감싸도록 대각선으로 드래그하세요."
      :"비교할 방과 벽을 넉넉히 감싸세요. 여러 평면이 한 페이지에 있으면 비교할 평면 하나를 선택하세요."}</p>}
      <div className="roi-buttons">{drawButton}
        <button type="button" aria-pressed={wholePage} disabled={drawing||wholePage} onClick={()=>{
          change({type:"load",points:fullPagePoints});dispatch({type:"pan"});setNotice("현재 Target 페이지 전체를 선택했습니다.");
        }}>페이지 전체 선택</button>
        {cancelButton}
      </div>
      {closed&&<p className="scope-status" role="status">{wholePage?"선택한 범위 · 페이지 전체":"선택한 범위 · 녹색 테두리 안쪽"}</p>}
      {mode==="edit"&&<p role="status">{notice}</p>}
      {!simple&&comparisonAction}
      {simple?editTools:<details className="scope-advanced"><summary>범위 편집 및 불러오기</summary>{editTools}</details>}
    </>:<>{editTools}<p className="roi-status" role="status">{notice} · 꼭짓점 {points.length}개</p></>}
    {(error||validation)&&<p role="alert" className="error">{error||validation}</p>}
  </section>;
  return <DrawingViewer focusRegion={focusRegion} image={image} role="Target" roi={interaction} compactROI={compact} inspection={!gestureActive&&!drawing&&mode==="pan"?candidateInspection:undefined} showDownload={!simple} tools={<>{simple?<><details className="optional-scope"><summary>영역 지정 (선택){closed?" · 선택됨":""}</summary>{tools}</details>{comparisonAction}</>:tools}{observationTools}</>} overlay={observationOverlay} technicalDetails={technicalDetails}/>;
}
