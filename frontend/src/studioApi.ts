import type {Vertex} from "./roi";
export type Role="Ref"|"Target";
export interface Identity{id:string;display:string;instance_kind?:string}
export interface Binding{role:Role;frame_id:string;source_sha256:string;image_sha256:string;page:number;page_count:number;extraction_zoom:number;width:number;height:number;source_path:string;[k:string]:unknown}
export interface NativeGeometry{coordinate_system:"image_native_pixels";points_native?:Vertex[];segments_native?:Vertex[][];closed?:boolean}
export interface GT{gt_id:string;role:Role;frame_id:string;source_image_sha256:string;source_binding:Binding;element_type:string;geometry_type:string;geometry_native:NativeGeometry;geometry_origin:string;review_status:"DRAFT"|"CONFIRMED"|"UNCERTAIN";reviewer:string|null;reviewed_at:string|null;host_wall_gt_id:string|null;related_space_gt_ids:string[];source_evidence_ids:string[];review_notes:string;is_thin_wall:boolean|null;version_review_complete:{poc1:boolean;poc2:boolean};[k:string]:unknown}
export interface Package{schema_version:string;dataset_id:string;dataset_kind:string;annotation_revision:string;registry_sha256:string;bindings:Record<Role,Binding>;gt_annotations:GT[];candidate_reviews:Record<string,{review_status:string;reviewer:string|null;reviewed_at:string|null;review_notes:string;[k:string]:unknown}>;candidate_gt_matches:Record<string,unknown>[];evaluation_scopes:Record<string,unknown>[];legacy_rows:unknown[];[k:string]:unknown}
export interface Candidate{candidate_key:string;candidate_id:string;element_type:string;role:Role;frame_id:string;source_version:string;included_in_evaluation:boolean;display_polygons_native:Vertex[][];display_segments_native:Vertex[][];bbox_native:[number,number,number,number]|null;[k:string]:unknown}
export interface Case{case_id:string;role:Role;title:string;candidate_keys:string[];primary_candidate_key?:string;display_bbox_native:[number,number,number,number];selection_reason:string}
export interface Space{id:string;role:Role;name:string;seed:Vertex|null;focus_bounds:[number,number,number,number]|null;polygon:{outer_rings:Vertex[][];holes?:Vertex[][]}|null;status:string}
export interface Catalog{manifest:{dataset_id:string;bindings:Record<Role,Binding>;registry_sha256:string};cases:Case[];candidates:Candidate[];spaces:Space[];roi:{vertices:Vertex[]};ocr:Record<Role,{text:string;polygon_native:Vertex[]}[]>;vlm:Record<Role,{id:string;bbox:number[];label:string;model:string;status:string}[]>;config:Record<string,unknown>}
export interface Saved{revision:number;latest_revision:number;package:Package;saved_at:string|null;content_sha256?:string}
export class APIError extends Error{status:number;detail?:unknown;constructor(status:number,message:string,detail?:unknown){super(message);this.status=status;this.detail=detail}}
export async function request<T>(url:string,options:RequestInit={}):Promise<T>{
 let r:Response;try{r=await fetch(url,{...options,headers:{...(options.body?{"Content-Type":"application/json"}:{}),...options.headers}})}catch{throw new APIError(0,"서버에 연결하지 못했습니다. 내 수정은 유지했습니다. 다시 저장하세요.")}
 if(!r.ok){let detail:unknown;try{detail=(await r.json()).detail}catch{detail=null}const text=typeof detail==="string"?detail:detail&&typeof detail==="object"&&"message"in detail?String(detail.message):r.status===401?"요청에 접근할 수 없습니다.":"요청을 완료하지 못했습니다. 내 수정은 유지했습니다.";throw new APIError(r.status,text,detail)}
 return r.json();
}
export const clone=<T,>(v:T):T=>structuredClone(v);
export const uuid=()=>crypto.randomUUID();
export function nativePoints(g:NativeGeometry):Vertex[]{return g.segments_native?g.segments_native.flat():g.points_native||[]}
export function focusCase(c:Case,candidates:Candidate[]):[number,number,number,number]{
 const door=c.case_id==="Q05"?candidates.find(p=>p.candidate_key==="poc1|Target:Door:012"):null;
 const b=door?.bbox_native;return b?[b[0]-60,b[1]-60,b[2]+60,b[3]+60]:c.display_bbox_native;
}
function segmentDistance(p:Vertex,a:Vertex,b:Vertex){const dx=b[0]-a[0],dy=b[1]-a[1],t=Math.max(0,Math.min(1,((p[0]-a[0])*dx+(p[1]-a[1])*dy)/(dx*dx+dy*dy||1)));return Math.hypot(p[0]-a[0]-dx*t,p[1]-a[1]-dy*t)}
function inside(p:Vertex,poly:Vertex[]){let yes=false;for(let i=0,j=poly.length-1;i<poly.length;j=i++){const a=poly[i],b=poly[j];if((a[1]>p[1])!==(b[1]>p[1])&&p[0]<(b[0]-a[0])*(p[1]-a[1])/(b[1]-a[1])+a[0])yes=!yes}return yes}
// Interaction hit testing only. These functions do not extract or classify geometry.
export function pickSpace(p:Vertex,spaces:Space[]):Space|null{
 const available=spaces.filter(s=>s.focus_bounds);
 const contained=available.filter(s=>s.polygon?.outer_rings.some(r=>inside(p,r))&&!s.polygon.holes?.some(r=>inside(p,r)));
 const choices=contained.length?contained:available.filter(s=>!s.polygon&&s.seed&&Math.hypot(s.seed[0]-p[0],s.seed[1]-p[1])<130);
 return choices.sort((a,b)=>Math.hypot((a.seed?.[0]??p[0])-p[0],(a.seed?.[1]??p[1])-p[1])-Math.hypot((b.seed?.[0]??p[0])-p[0],(b.seed?.[1]??p[1])-p[1]))[0]||null;
}
export function pickCandidate(p:Vertex,candidates:Candidate[],tolerance:number):Candidate|null{
 const priority:Record<string,number>={Door:0,Window:1,Opening:2,Wall:3,SpaceBoundary:4};
 const hits=candidates.map(c=>{const d=Math.min(Infinity,...c.display_segments_native.flatMap(s=>s.slice(1).map((b,i)=>segmentDistance(p,s[i],b))));const contains=c.display_polygons_native.some(poly=>inside(p,poly));return {c,d:contains?Math.min(d,1):d}}).filter(x=>x.d<=tolerance).sort((a,b)=>a.d-b.d||(priority[a.c.element_type]??5)-(priority[b.c.element_type]??5)||a.c.candidate_key.localeCompare(b.c.candidate_key));return hits[0]?.c||null;
}
export interface History<T>{past:T[];present:T;future:T[];group:string|null}
export const startHistory=<T,>(v:T):History<T>=>({past:[],present:v,future:[],group:null});
export function pushHistory<T>(h:History<T>,v:T,group:string|null=null):History<T>{return {past:group&&group===h.group?h.past:[...h.past,h.present].slice(-50),present:v,future:[],group}}
export function stepHistory<T>(h:History<T>,direction:"undo"|"redo"):History<T>{if(direction==="undo"&&h.past.length)return {past:h.past.slice(0,-1),present:h.past[h.past.length-1],future:[h.present,...h.future],group:null};if(direction==="redo"&&h.future.length)return {past:[...h.past,h.present],present:h.future[0],future:h.future.slice(1),group:null};return h}
