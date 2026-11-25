import type {NativeImage} from "./api";

export type Vertex = [number, number];
export interface TargetROI {
  schema_version: 1;
  role: "Target";
  coordinate_system: "image_native_pixels";
  image: {source_sha256: string; page: number; extraction_zoom: number; width: number; height: number; image_id: string};
  vertices: Vertex[];
}
export const MAX_ROI_BYTES = 1024 * 1024;
const fail = (message: string): never => {throw new Error(message);};
const cross = (a: Vertex, b: Vertex, c: Vertex) => (b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0]);
const between = (a: number,b: number,p: number) => p >= Math.min(a,b) && p <= Math.max(a,b);
function intersects(a: Vertex,b: Vertex,c: Vertex,d: Vertex) {
  const x=cross(a,b,c),y=cross(a,b,d),z=cross(c,d,a),w=cross(c,d,b);
  return (x*y<0 && z*w<0) ||
    (x===0 && between(a[0],b[0],c[0]) && between(a[1],b[1],c[1])) ||
    (y===0 && between(a[0],b[0],d[0]) && between(a[1],b[1],d[1])) ||
    (z===0 && between(c[0],d[0],a[0]) && between(c[1],d[1],a[1])) ||
    (w===0 && between(c[0],d[0],b[0]) && between(c[1],d[1],b[1]));
}
export function validateVertices(value: unknown, width: number, height: number): Vertex[] {
  if (!Array.isArray(value) || value.length<3 || value.length>512) fail("ROI 꼭짓점은 3~512개여야 합니다.");
  const vertices = (value as unknown[]).map(p=>{
    if (!Array.isArray(p) || p.length!==2 || !p.every(n=>typeof n==="number" && Number.isFinite(n)) ||
        p[0]<0 || p[1]<0 || p[0]>width || p[1]>height) fail("ROI 좌표는 원본 이미지 경계 안의 유한한 숫자여야 합니다.");
    return [...(p as Vertex)] as Vertex;
  });
  for(let i=0;i<vertices.length;i++) {
    const a=vertices[i],b=vertices[(i+1)%vertices.length],c=vertices[(i+2)%vertices.length];
    if(a[0]===b[0]&&a[1]===b[1])fail("중복 꼭짓점은 사용할 수 없습니다.");
    if(cross(a,b,c)===0 && (b[0]-a[0])*(c[0]-b[0])+(b[1]-a[1])*(c[1]-b[1])<=0)fail("겹치거나 되돌아가는 변은 사용할 수 없습니다.");
    for(let j=i+1;j<vertices.length;j++) {
      if(j===i+1 || (i===0&&j===vertices.length-1))continue;
      if(intersects(a,b,vertices[j],vertices[(j+1)%vertices.length]))fail("자기 교차 또는 겹치는 ROI는 사용할 수 없습니다.");
    }
  }
  const twiceArea=vertices.reduce((sum,a,i)=>{const b=vertices[(i+1)%vertices.length];return sum+a[0]*b[1]-b[0]*a[1];},0);
  if(Math.abs(twiceArea)<1e-8)fail("ROI 면적이 0입니다.");
  return vertices;
}
function imageBinding(image: NativeImage): TargetROI["image"] {
  return {source_sha256:image.document.sha256,page:image.page,extraction_zoom:image.zoom,
    width:image.width,height:image.height,image_id:image.id};
}
export function makeROI(image: NativeImage, vertices: unknown): TargetROI {
  return {schema_version:1,role:"Target",coordinate_system:"image_native_pixels",
    image:imageBinding(image),
    vertices:validateVertices(vertices,image.width,image.height)};
}
export function readROI(text: string, image: NativeImage): TargetROI {
  if(new TextEncoder().encode(text).length>MAX_ROI_BYTES)fail("ROI JSON은 1MiB 이하여야 합니다.");
  let input!: TargetROI;
  try{input=JSON.parse(text);}catch{fail("올바른 ROI JSON 파일이 아닙니다.");}
  if(!input || input.schema_version!==1 || input.role!=="Target" || input.coordinate_system!=="image_native_pixels")
    fail("지원하지 않는 ROI 형식 또는 좌표계입니다.");
  const expected=imageBinding(image);
  if(!input.image || Object.keys(expected).some(k=>input.image[k as keyof typeof expected]!==expected[k as keyof typeof expected]))
    fail("ROI의 Target PDF·페이지·추출 배율·이미지 크기가 현재 도면과 다릅니다. 원래 도면을 선택하거나 ROI를 다시 지정하세요.");
  return makeROI(image,input.vertices);
}
