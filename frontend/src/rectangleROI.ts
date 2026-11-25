// Interactive ROI geometry only; unrelated to structural Geometry analysis.
import type {Vertex} from "./roi";

export function clippedPoint(p:Vertex,width:number,height:number):Vertex {
  if(![...p,width,height].every(Number.isFinite)||width<=0||height<=0)throw Error("유효한 원본 이미지 좌표가 필요합니다.");
  return [Math.max(0,Math.min(width,p[0])),Math.max(0,Math.min(height,p[1]))];
}
export function rectangleBetween(a:Vertex,b:Vertex,width:number,height:number):Vertex[] {
  a=clippedPoint(a,width,height);b=clippedPoint(b,width,height);
  const xmin=Math.min(a[0],b[0]),xmax=Math.max(a[0],b[0]),ymin=Math.min(a[1],b[1]),ymax=Math.max(a[1],b[1]);
  return [[xmin,ymin],[xmax,ymin],[xmax,ymax],[xmin,ymax]];
}
export function rectangleBounds(points:Vertex[]):[number,number,number,number] {
  if(!points.length||points.some(p=>p.length!==2||!p.every(Number.isFinite)))throw Error("유효한 비교 영역 좌표가 필요합니다.");
  return [Math.min(...points.map(p=>p[0])),Math.min(...points.map(p=>p[1])),Math.max(...points.map(p=>p[0])),Math.max(...points.map(p=>p[1]))];
}
export function isRectangle(points:Vertex[]):boolean {
  if(points.length!==4)return false;
  const [x0,y0,x1,y1]=rectangleBounds(points),epsilon=1e-8;
  if(x1-x0<=epsilon||y1-y0<=epsilon)return false;
  const corners=new Set<string>();
  return points.every((p,i)=>{
    const x=Math.abs(p[0]-x0)<=epsilon?0:Math.abs(p[0]-x1)<=epsilon?1:-1;
    const y=Math.abs(p[1]-y0)<=epsilon?0:Math.abs(p[1]-y1)<=epsilon?1:-1;
    const key=x+":"+y;if(x<0||y<0||corners.has(key))return false;corners.add(key);
    const q=points[(i+1)%4];return (Math.abs(p[0]-q[0])<=epsilon)!==(Math.abs(p[1]-q[1])<=epsilon);
  });
}
export function rectangularize(points:Vertex[],width:number,height:number):Vertex[] {
  const [x0,y0,x1,y1]=rectangleBounds(points);return rectangleBetween([x0,y0],[x1,y1],width,height);
}
// Use the pointer-down snapshot: the opposite physical corner stays fixed even
// when the dragged handle crosses it and normalized vertex indexes change.
export function resizeRectangle(original:Vertex[],index:number,point:Vertex,width:number,height:number):Vertex[] {
  if(!isRectangle(original)||!Number.isInteger(index)||index<0||index>3)throw Error("직사각형 모서리를 선택하세요.");
  return rectangleBetween(original[(index+2)%4],point,width,height);
}
export function translateRectangle(original:Vertex[],delta:Vertex,width:number,height:number):Vertex[] {
  if(!isRectangle(original)||!delta.every(Number.isFinite))throw Error("유효한 직사각형 이동이 필요합니다.");
  const [x0,y0,x1,y1]=rectangleBounds(original),dx=Math.max(-x0,Math.min(width-x1,delta[0])),dy=Math.max(-y0,Math.min(height-y1,delta[1]));
  return original.map(p=>clippedPoint([p[0]+dx,p[1]+dy],width,height));
}
