import {useEffect, useRef} from "react";
import type {ReactNode} from "react";
import type {Vertex} from "./roi";
import type {NativeImage} from "./api";
import {geometry as g} from "./geometry";
import type {Point, View} from "./geometry";
import {rectangleBounds} from "./rectangleROI";

export type RectangleGestureKind="draw"|"resize"|"translate";
export interface RectangleInteraction {
  enabled:boolean;
  begin(kind:RectangleGestureKind,point:Point,index:number|null):void;
  update(point:Point):void;
  end():void;
  cancel():void;
}

export interface ROIInteraction {
  mode: "pan" | "draw" | "edit"; points: Vertex[]; closed: boolean; selected: number|null; drawing: boolean; interactionKey: number;
  add(point: Point): void; move(index: number, point: Point): void;
  select(index: number|null): void; cancel(): void; finish(): void;
  rectangle?:RectangleInteraction;
}
export interface Inspection {points?: {id:string;point:Vertex}[]; polygon?: Vertex[]; onPick?(point: Point): void; selectionKey?:string|null;panOnDrag?:boolean;editPoints?:Vertex[];onMove?(index:number,point:Point):void;onEditStart?():void;onEditEnd?():void}
export interface StructuralLine {id:string;points:Vertex[];usage:"FIT"|"VALIDATION"|"HOLD"|"EXCLUDE";selected:boolean}
export interface StructuralInteraction {
  mode:"PAN"|"DRAW"|"EDIT";lines:StructuralLine[];activeId:string|null;interactionKey:string;message:string;
  add(point:Point):void;moveEndpoint(index:number,point:Point):void;cancel():void;
}
export interface FocusRegion {key:string;bounds:[number,number,number,number]}
interface Actions {zoom(factor: number): void; fit(): void; actual(): void; cancelInteraction(): void; focusRegion(bounds:[number,number,number,number]):void}
export function DrawingViewer({image, role, roi, tools, compactROI=false, inspection, onImageError, structural, focusRegion, overlay, showDownload=true, technicalDetails=Boolean(inspection||structural)}: {
  image: NativeImage; role: "Ref" | "Target"; roi?: ROIInteraction; tools?: ReactNode; compactROI?:boolean; inspection?: Inspection; onImageError?:()=>void; structural?:StructuralInteraction; focusRegion?:FocusRegion;overlay?:ReactNode;showDownload?:boolean;technicalDetails?:boolean;
}) {
  const structuralRef=useRef(structural);structuralRef.current=structural;
  const inspectionRef=useRef(inspection);inspectionRef.current=inspection;
  const roiRef=useRef(roi);roiRef.current=role==="Target"?roi:undefined;
  const viewport = useRef<HTMLDivElement>(null), scene = useRef<HTMLDivElement>(null);
  const zoomLabel = useRef<HTMLOutputElement>(null), coordinateLabel = useRef<HTMLOutputElement>(null);
  const actions = useRef<Actions | null>(null);

  useEffect(() => {
    const element = viewport.current!, world = scene.current!;
    let view: View | null = null, fitMode = true, oldWidth = 0, oldHeight = 0;
    let frame: number | null = null;
    const pointers = new Map<number, Point>();
    let gesture: {id: number; index: number|null; start: Point; moved: boolean; draw: boolean; pick?:boolean;anchorDraw?:boolean;anchorIndex?:number|null;inspectionEdit?:boolean;roiRectangle?:boolean}|null=null;
    const fit = () => g.fit(image.width, image.height, element.clientWidth, element.clientHeight);
    function paint() {
      if (frame !== null) return;
      frame = requestAnimationFrame(() => {
        frame = null;
        if (!view) return;
        world.style.setProperty("--viewport-scale",String(view.scale));
        world.style.transform = `matrix(${view.scale},0,0,${view.scale},${view.offsetX},${view.offsetY})`;
        if(zoomLabel.current)zoomLabel.current.textContent = `${(view.scale * 100).toFixed(1)}%`;
        element.dataset.scale = String(view.scale);
        element.dataset.offsetX = String(view.offsetX); element.dataset.offsetY = String(view.offsetY);
      });
    }
    function resize() {
      const width = element.clientWidth, height = element.clientHeight;
      if (!width || !height || (width === oldWidth && height === oldHeight)) return;
      if(gesture?.roiRectangle)cancelInteraction();
      view = !view || fitMode ? fit() : g.resize(view, oldWidth, oldHeight, width, height);
      oldWidth = width; oldHeight = height; paint();
    }
    function zoom(factor: number, x = element.clientWidth/2, y = element.clientHeight/2) {
      if (!view || !Number.isFinite(factor) || factor <= 0) return;
      if(gesture?.roiRectangle)cancelInteraction();
      const fitted = fit().scale;
      view = g.zoomAt(view, factor, x, y, fitted/4, Math.max(8, fitted));
      fitMode = false; paint();
    }
    function reset() {if(gesture?.roiRectangle)cancelInteraction();view = fit(); fitMode = true; paint();}
    function local(event: MouseEvent | PointerEvent) {
      const rect = element.getBoundingClientRect();
      return {x: event.clientX - rect.left, y: event.clientY - rect.top};
    }
    function show(point: Point) {
      if (!view) return;
      const native = g.toNative(view, point.x, point.y);
      const inside = native.x >= 0 && native.y >= 0 && native.x < image.width && native.y < image.height;
      if(coordinateLabel.current)coordinateLabel.current.textContent = inside ? `X ${native.x.toFixed(1)} · Y ${native.y.toFixed(1)}` : "X — · Y —";
    }
    function wheel(event: WheelEvent) {
      event.preventDefault(); const p = local(event);
      const unit = event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? element.clientHeight : 1;
      zoom(Math.exp(Math.max(-.7, Math.min(.7, -event.deltaY * unit * .002))), p.x, p.y); show(p);
    }
    function cancelInteraction() {
      // A pointerup may never arrive after focus loss. Drop navigation gestures,
      // keeping both the camera and the user ROI unchanged.
      const captured=[...pointers.keys()];
      const rectangle=gesture?.roiRectangle;gesture=null;
      if(rectangle)roiRef.current?.rectangle?.cancel();
      pointers.clear();element.classList.remove("dragging");delete element.dataset.roiGesture;
      for(const id of captured)if(element.hasPointerCapture(id))element.releasePointerCapture(id);
    }
    function visibility() {if(document.hidden)cancelInteraction();}
    function down(event: PointerEvent) {
      if (!view || (event.button !== 0 && event.button !== 1)) return;
      event.preventDefault(); element.focus({preventScroll: true});
      pointers.set(event.pointerId, local(event)); element.setPointerCapture(event.pointerId);
      const editor=roiRef.current;
      if(pointers.size>1){const rectangle=gesture?.roiRectangle;gesture=null;if(rectangle)editor?.rectangle?.cancel();delete element.dataset.roiGesture;element.classList.add("dragging");return;}
      const anchor=structuralRef.current;
      if(anchor&&anchor.mode!=="PAN"&&event.button===0&&!event.shiftKey){
        element.classList.remove("dragging");
        const p=local(event),native=g.toNative(view,p.x,p.y);
        const line=anchor.lines.find(l=>l.id===anchor.activeId);
        const hit=anchor.mode==="EDIT"?line?.points.findIndex(v=>Math.hypot(v[0]-native.x,v[1]-native.y)*view!.scale<=10)??-1:-1;
        gesture={id:event.pointerId,index:null,start:p,moved:false,draw:false,anchorDraw:anchor.mode==="DRAW",anchorIndex:hit<0?null:hit};
        return;
      }
      if(editor?.rectangle?.enabled&&editor.mode!=="pan"&&event.button===0&&!event.shiftKey){
        const p=local(event),native=g.toNative(view,p.x,p.y);
        const index=editor.mode==="edit"?editor.points.findIndex(v=>Math.hypot(v[0]-native.x,v[1]-native.y)*view!.scale<=10):-1;
        let kind:RectangleGestureKind|null=editor.mode==="draw"?"draw":index>=0?"resize":null;
        if(!kind&&editor.closed){const [x0,y0,x1,y1]=rectangleBounds(editor.points);if(native.x>=x0&&native.x<=x1&&native.y>=y0&&native.y<=y1)kind="translate";}
        if(!kind){editor.select(null);gesture=null;element.classList.add("dragging");return;}
        gesture={id:event.pointerId,index:index<0?null:index,start:p,moved:false,draw:false,roiRectangle:true};
        element.dataset.roiGesture=kind;element.classList.toggle("dragging",kind==="translate");
        editor.rectangle.begin(kind,native,index<0?null:index);return;
      }
      if((inspectionRef.current?.onPick || (editor && editor.mode!=="pan")) && event.button===0 && !event.shiftKey) {
        element.classList.remove("dragging");
        const p=local(event), native=g.toNative(view,p.x,p.y);
        const custom=inspectionRef.current;
        const index=editor?.mode==="edit"?editor.points.findIndex(v=>Math.hypot(v[0]-native.x,v[1]-native.y)*view!.scale<=10):custom?.editPoints?.findIndex(v=>Math.hypot(v[0]-native.x,v[1]-native.y)*view!.scale<=10)??-1;
        editor?.select(index<0?null:index);
        gesture={id:event.pointerId,index:index<0?null:index,start:p,moved:false,draw:editor?.mode==="draw",pick:Boolean(inspectionRef.current?.onPick),inspectionEdit:!editor&&index>=0};
        if(gesture.inspectionEdit)custom?.onEditStart?.();
      }else{gesture=null;element.classList.add("dragging");}
    }
    function move(event: PointerEvent) {
      const p = local(event);
      if(view && gesture?.id===event.pointerId && pointers.size===1) {
        if(Math.hypot(p.x-gesture.start.x,p.y-gesture.start.y)>5)gesture.moved=true;
        const previous=pointers.get(event.pointerId)||p;pointers.set(event.pointerId,p);
        if(gesture.roiRectangle){roiRef.current?.rectangle?.update(g.toNative(view,p.x,p.y));show(p);return;}
        if(gesture.pick&&gesture.index===null&&gesture.moved&&inspectionRef.current?.panOnDrag){view=g.panBy(view,p.x-previous.x,p.y-previous.y);fitMode=false;paint();show(p);return;}
        if(gesture.anchorIndex!=null){
          const native=g.toNative(view,p.x,p.y);
          structuralRef.current?.moveEndpoint(gesture.anchorIndex,{x:Math.max(0,Math.min(image.width,native.x)),y:Math.max(0,Math.min(image.height,native.y))});
        }else if(gesture.index!==null){
          const native=g.toNative(view,p.x,p.y);
          const next={x:Math.max(0,Math.min(image.width,native.x)),y:Math.max(0,Math.min(image.height,native.y))};
          if(gesture.inspectionEdit)inspectionRef.current?.onMove?.(gesture.index,next);else roiRef.current?.move(gesture.index,next);
        }
        show(p);return;
      }
      if (view && pointers.has(event.pointerId)) {
        const previous = pointers.get(event.pointerId)!, old = [...pointers.values()];
        pointers.set(event.pointerId, p);
        if (pointers.size === 1) view = g.panBy(view, p.x-previous.x, p.y-previous.y);
        else if (pointers.size === 2) {
          const next = [...pointers.values()];
          const oldCenter = {x:(old[0].x+old[1].x)/2,y:(old[0].y+old[1].y)/2};
          const nextCenter = {x:(next[0].x+next[1].x)/2,y:(next[0].y+next[1].y)/2};
          const before = Math.hypot(old[0].x-old[1].x,old[0].y-old[1].y);
          const after = Math.hypot(next[0].x-next[1].x,next[0].y-next[1].y);
          if (before > 0 && after > 0) {
            const fitted = fit().scale;
            view = g.zoomAt(view,after/before,oldCenter.x,oldCenter.y,fitted/4,Math.max(8,fitted));
            view = g.panBy(view,nextCenter.x-oldCenter.x,nextCenter.y-oldCenter.y);
          }
        }
        fitMode = false; paint();
      }
      show(p);
    }
    function up(event: PointerEvent) {
      if(gesture?.id===event.pointerId) {
        const edit=gesture;gesture=null;if(edit.inspectionEdit)inspectionRef.current?.onEditEnd?.();
        if(edit.roiRectangle){
          if(event.type==="pointerup"&&view){const p=local(event);roiRef.current?.rectangle?.update(g.toNative(view,p.x,p.y));roiRef.current?.rectangle?.end();}
          else roiRef.current?.rectangle?.cancel();
          delete element.dataset.roiGesture;
        }else if(event.type==="pointerup" && (edit.draw||edit.pick||edit.anchorDraw) && !edit.moved && view) {
          const p=local(event),native=g.toNative(view,p.x,p.y);
          if(native.x>=0&&native.y>=0&&native.x<=image.width&&native.y<=image.height){if(edit.anchorDraw)structuralRef.current?.add(native);else if(edit.pick)inspectionRef.current?.onPick?.(native);else roiRef.current?.add(native);}
        }
      }
      pointers.delete(event.pointerId);
      if (element.hasPointerCapture(event.pointerId)) element.releasePointerCapture(event.pointerId);
      if (!pointers.size) element.classList.remove("dragging");
    }
    function leave() {if(coordinateLabel.current)coordinateLabel.current.textContent = "X — · Y —";}
    function key(event: KeyboardEvent) {
      if (!view) return;
      if(event.key==="Escape"&&structuralRef.current){event.preventDefault();cancelInteraction();structuralRef.current.cancel();return;}
      if(event.key==="Escape"&&roiRef.current){event.preventDefault();cancelInteraction();roiRef.current.cancel();return;}
      if(event.key==="Enter"&&roiRef.current?.mode==="draw"){event.preventDefault();roiRef.current.finish();return;}
      const pan: Record<string, [number, number]> = {ArrowLeft:[40,0],ArrowRight:[-40,0],ArrowUp:[0,40],ArrowDown:[0,-40]};
      if (pan[event.key]) {event.preventDefault();if(gesture?.roiRectangle)cancelInteraction();view=g.panBy(view,...pan[event.key]);fitMode=false;paint();}
      else if (["+","=","-","0","1"].includes(event.key)) {
        event.preventDefault();
        if (event.key === "0") reset();
        else zoom(event.key === "1" ? 1/view.scale : event.key === "-" ? .8 : 1.25);
      }
    }
    function doubleClick() {if(structuralRef.current&&structuralRef.current.mode!=="PAN")return;if((!roiRef.current&&!inspectionRef.current?.onPick) || roiRef.current?.mode==="pan")reset();}
    function focusBounds(bounds:[number,number,number,number]){
      const [x1,y1,x2,y2]=bounds;
      if(!bounds.every(Number.isFinite)||x2<=x1||y2<=y1)return;
      cancelInteraction();const next=g.fit(x2-x1,y2-y1,element.clientWidth,element.clientHeight);
      const scale=Math.min(8,next.scale);
      view={scale,offsetX:element.clientWidth/2-(x1+x2)*scale/2,offsetY:element.clientHeight/2-(y1+y2)*scale/2};
      fitMode=false;paint();
    }
    actions.current = {focusRegion:focusBounds,zoom: factor => zoom(factor), fit: reset, cancelInteraction, actual: () => {if (view) zoom(1/view.scale);}};
    window.addEventListener("blur",cancelInteraction);document.addEventListener("visibilitychange",visibility);
    element.addEventListener("wheel",wheel,{passive:false});
    element.addEventListener("pointerdown",down);element.addEventListener("pointermove",move);
    element.addEventListener("pointerup",up);element.addEventListener("pointercancel",up);
    element.addEventListener("lostpointercapture",up);element.addEventListener("pointerleave",leave);
    element.addEventListener("keydown",key);element.addEventListener("dblclick",doubleClick);
    const observer=new ResizeObserver(resize);observer.observe(element);resize();
    return () => {
      cancelInteraction();window.removeEventListener("blur",cancelInteraction);document.removeEventListener("visibilitychange",visibility);
      observer.disconnect();if(frame!==null)cancelAnimationFrame(frame);actions.current=null;
      element.removeEventListener("wheel",wheel);element.removeEventListener("pointerdown",down);
      element.removeEventListener("pointermove",move);element.removeEventListener("pointerup",up);
      element.removeEventListener("pointercancel",up);element.removeEventListener("lostpointercapture",up);
      element.removeEventListener("pointerleave",leave);element.removeEventListener("keydown",key);
      element.removeEventListener("dblclick",doubleClick);
    };
  }, [image.id, image.width, image.height]);

  useEffect(()=>{
    actions.current?.cancelInteraction();
    if(roi?.mode==="draw") {
      viewport.current?.focus({preventScroll:true});
    }
  },[roi?.mode,roi?.interactionKey,inspection?.selectionKey,Boolean(inspection?.onPick),structural?.interactionKey]);

  useEffect(()=>{if(focusRegion)actions.current?.focusRegion(focusRegion.bounds);},[focusRegion?.key,image.id]);

  useEffect(()=>{
    if(compactROI&&roi?.mode==="draw"){
      viewport.current?.scrollIntoView({block:"nearest"});
      viewport.current?.focus({preventScroll:true});
    }
  },[compactROI,roi?.mode]);

  const name = `${image.document.filename.replace(/\.pdf$/i, "")}_page_${image.page}_${role.toLowerCase()}_${image.zoom}x.png`;
  return <section className="drawing-panel" data-role={role} aria-label={`${role} 도면`}>
    <header><h2>{role} · {image.page} 페이지</h2><p title={image.document.filename}>{image.document.filename}{technicalDetails&&<> · {image.width} × {image.height}px · {image.zoom}배</>}</p></header>
    <div className="viewer">
      <div className="toolbar">
        <button aria-label={`${role} 축소`} onClick={()=>actions.current?.zoom(.8)}>−</button>
        <button aria-label={`${role} 확대`} onClick={()=>actions.current?.zoom(1.25)}>+</button>
        <button aria-label={`${role} 화면 맞춤`} onClick={()=>actions.current?.fit()}>화면 맞춤</button>
        <button aria-label={`${role} 100%`} onClick={()=>actions.current?.actual()}>100%</button>
        <output className="zoom" ref={zoomLabel}>—</output>
      </div>
      <div className="viewport" ref={viewport} tabIndex={0} role="region" aria-label={`${role} 확대 및 이동`}
           data-anchor-mode={structural?.mode} data-inspection={Boolean(inspection)} data-picking={Boolean(inspection?.onPick)} data-roi-mode={roi?.mode} data-image-id={image.id} data-coordinate-system="image_native_pixels">
        {role==="Target"&&roi&&(!compactROI||roi.mode!=="pan")&&<div className="roi-mode-indicator" role="status">
          {roi.mode==="draw"?(roi.rectangle?.enabled?"도면에서 한 모서리부터 반대쪽 모서리까지 드래그하세요":`Target · ROI 그리기 — 클릭으로 점 추가 (${roi.points.length}개)`):roi.mode==="edit"?(roi.rectangle?.enabled?"Target · 모서리: 크기 수정 · 영역 내부: 전체 이동":"Target · 기존 다각형 — 직사각형 보정 가능"):roi.drawing?"Target · 도면 이동 — 작성 중인 영역 보존":"Target · 도면 이동 모드"}
        </div>}
        {structural&&<div className="structural-mode-indicator" role="status">{role} · {structural.message}</div>}
        <div className="scene" ref={scene} style={{width:image.width,height:image.height}}>
          <img src={image.url} alt={`${role} 선택 도면`} draggable={false} onError={onImageError}/>
          <svg viewBox={`0 0 ${image.width} ${image.height}`} aria-hidden="true" style={inspection?{overflow:"hidden"}:undefined}>
            {overlay}
            {inspection&&<g className="inspection-overlay">
              {inspection.polygon&&<polygon points={inspection.polygon.map(p=>p.join(",")).join(" ")} vectorEffect="non-scaling-stroke"/>}
              {inspection.points?.map(p=><g key={p.id}><circle cx={p.point[0]} cy={p.point[1]} vectorEffect="non-scaling-stroke"/><text x={p.point[0]} y={p.point[1]}>{p.id}</text></g>)}
            </g>}
            {structural&&<g className="structural-overlay">
              {structural.lines.map(line=><g key={line.id} data-structural-id={line.id} data-usage={line.usage} data-selected={line.selected}>
                <polyline points={line.points.map(p=>p.join(",")).join(" ")} vectorEffect="non-scaling-stroke"/>
                {line.selected&&line.points.map((p,index)=><circle key={index} cx={p[0]} cy={p[1]} data-structural-endpoint={index} vectorEffect="non-scaling-stroke"><title>분석 구간 지정점 {index+1} · Landmark 아님</title></circle>)}
              </g>)}
            </g>}
            {role==="Target"&&roi&&<g className="roi-overlay" data-closed={roi.closed}>
              {roi.points.length>0&&((roi.closed||(roi.rectangle?.enabled&&roi.points.length===4))
                ?<polygon points={roi.points.map(p=>p.join(",")).join(" ")} vectorEffect="non-scaling-stroke"/>
                :<polyline points={roi.points.map(p=>p.join(",")).join(" ")} vectorEffect="non-scaling-stroke"/>)}
              {roi.points.map((p,index)=><circle key={index} cx={p[0]} cy={p[1]} data-vertex={index}
                className={roi.selected===index?"selected":""} vectorEffect="non-scaling-stroke"/>)}
            </g>}
          </svg>
        </div>
      </div>
      <footer><span>{(roi&&roi.mode!=="pan")||(structural&&structural.mode!=="PAN")||(inspection?.onPick&&!inspection.panOnDrag)?"휠 확대·축소 · Shift+드래그 이동 · 방향키 이동":"휠 확대·축소 · 드래그 이동 · 방향키 이동"}</span><output ref={coordinateLabel} hidden={!technicalDetails}>X — · Y —</output></footer>
    </div>
    {showDownload&&<a className="download" href={image.url} download={name}>{role} 선택 페이지 PNG 다운로드</a>}
    {tools}
  </section>;
}
