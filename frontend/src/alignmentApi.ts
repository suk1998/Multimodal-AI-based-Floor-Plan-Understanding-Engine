import type {NativeImage} from "./api";
import type {TargetROI,Vertex} from "./roi";
export interface AlignmentAnchor {id:string;ref:Vertex;target:Vertex;role:"fit"|"check";reason:string;confirmed:boolean;residual_ref_px?:number;used_for_fit?:boolean}
interface Residual {count:number;rmse:number|null;p95:number|null;max:number|null}
export interface AlignmentResult {
  run_id:string;status:string;method:string;anchors:AlignmentAnchor[];matrix:number[][]|null;inverse_matrix:number[][]|null;
  target_roi:TargetROI;approval_eligible:boolean;reasons:string[];preview_modes:string[];
  residuals?:{fit_ref_px:Residual;check_ref_px:Residual;check_target_px:Residual};
  mapping:null|{ref_vertices_unclipped:Vertex[];outside_ref_pixels:number;valid_fraction:number;sampled_hull_roi_fraction:number;outside_ref_vertex_indices:number[];check_support_hull_target:Vertex[]};
  verification?:{independent_check_points:number;check_spatial_cells:number;fit_check_patches_separated:boolean;structures_user_confirmed:boolean};
  review:{state:string;reason?:string;warning?:string};
}
async function request<T>(path:string,body:unknown,signal?:AbortSignal):Promise<T> {
 const response=await fetch(path,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body),signal});
 if(!response.ok){let d;try{d=(await response.json()).detail;}catch{d=null;}throw Error(typeof d==="string"?d:`정렬 요청 실패 (${response.status})`);}
 return response.json();
}
const spec=(i:NativeImage)=>({document_id:i.document.id,page:i.page,zoom:i.zoom});
export function runAlignment(ref:NativeImage,target:NativeImage,roi:TargetROI,mode:"auto"|"manual",anchors:AlignmentAnchor[],signal:AbortSignal) {
 return request<AlignmentResult>("/api/alignments",{ref:spec(ref),target:spec(target),target_roi:roi,mode,anchors,tolerance_ref_px:3*ref.zoom/2},signal);
}
export function reviewAlignment(id:string,decision:"approve"|"reject",reason:string,confirmed:boolean,signal:AbortSignal){
 return request<AlignmentResult>(`/api/alignments/${id}/review`,{decision,reason,stable_structures_confirmed:confirmed,scope:"sampled_support_only"},signal);
}
