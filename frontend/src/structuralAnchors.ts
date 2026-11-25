import type {NativeImage} from "./api";
import type {TargetROI,Vertex} from "./roi";
import {readROI} from "./roi.ts";

export type Segment=[Vertex,Vertex];
export type AnchorUsage="FIT"|"VALIDATION"|"HOLD"|"EXCLUDE";
export type BoundaryKind="OUTER"|"INNER"|"OTHER"|"UNDETERMINED";
export type ReviewStatus="UNREVIEWED"|"REVIEWED"|"INVALIDATED"|"REJECTED";
export type ImageBinding=TargetROI["image"];
export const ENDPOINT_SEMANTICS="ANALYSIS_INTERVAL_BOUNDARIES" as const;
export const REVIEW_SCOPE="SEGMENT_CORRESPONDENCE_ONLY" as const;
export const MAX_ANCHOR_BYTES=2*1024*1024,MAX_ANCHORS=256;
export interface AnchorBasis {
  revision:number; parent_structure_id:string; boundary_kind:BoundaryKind; usage:AnchorUsage;
  ref_segment:Segment|null;target_segment:Segment|null;
}
export interface AnchorReview {
  status:ReviewStatus;reason:string;at:string|null;applies_to_revision:number|null;
  identity_hypothesis_checked:boolean;interval_scope_checked:boolean;scope:typeof REVIEW_SCOPE;
}
export interface ReviewEvent {status:ReviewStatus;reason:string;at:string;basis:AnchorBasis}
export interface StructuralAnchorPair extends AnchorBasis {
  id:string;rationale:string;endpoint_semantics:typeof ENDPOINT_SEMANTICS;landmark_correspondence:false;
  source:{kind:"MANUAL"|"ANALYST_REFERENCE";reference_id?:string;report_sha256?:string};
  review:AnchorReview;review_history:ReviewEvent[];created_at:string;updated_at:string;
}
export interface AnchorFile {
  schema_version:1;kind:"STRUCTURAL_ANCHOR_SET";coordinate_system:"image_native_pixels";
  endpoint_semantics:typeof ENDPOINT_SEMANTICS;landmark_correspondence:false;
  ref_image:ImageBinding;target_image:ImageBinding;target_roi_context:TargetROI|null;
  pairs:StructuralAnchorPair[];exported_at:string;
}
const copy=<T>(v:T):T=>JSON.parse(JSON.stringify(v));
const now=()=>new Date().toISOString();
const unreviewed=():AnchorReview=>({status:"UNREVIEWED",reason:"",at:null,applies_to_revision:null,
  identity_hypothesis_checked:false,interval_scope_checked:false,scope:REVIEW_SCOPE});
export function bindingFor(image:NativeImage):ImageBinding {
 return {source_sha256:image.document.sha256,page:image.page,extraction_zoom:image.zoom,width:image.width,height:image.height,image_id:image.id};
}
export function bindingsMatch(binding:unknown,image:NativeImage):boolean {
 if(!binding||typeof binding!=="object")return false;
 const expected=bindingFor(image),value=binding as Record<string,unknown>;
 return Object.keys(expected).every(k=>value[k]===expected[k as keyof ImageBinding]);
}
export function newPair(id=crypto.randomUUID()):StructuralAnchorPair {
 const at=now();return {id,revision:0,parent_structure_id:"",boundary_kind:"UNDETERMINED",usage:"HOLD",
  ref_segment:null,target_segment:null,rationale:"",endpoint_semantics:ENDPOINT_SEMANTICS,landmark_correspondence:false,
  source:{kind:"MANUAL"},review:unreviewed(),review_history:[],created_at:at,updated_at:at};
}
export function basisOf(p:StructuralAnchorPair):AnchorBasis {
 return copy({revision:p.revision,parent_structure_id:p.parent_structure_id,boundary_kind:p.boundary_kind,
  usage:p.usage,ref_segment:p.ref_segment,target_segment:p.target_segment});
}
export function invalidatePair(p:StructuralAnchorPair,reason:string):StructuralAnchorPair {
 if(p.review.status==="UNREVIEWED")return p;
 const at=now(),history=p.review.status==="INVALIDATED"?p.review_history:
  [...p.review_history,{status:"INVALIDATED" as const,reason,at,basis:basisOf(p)}].slice(-32);
 return {...p,review:{...unreviewed(),status:"INVALIDATED",reason,at},review_history:history,updated_at:at};
}
export function patchPair(p:StructuralAnchorPair,patch:Partial<Pick<StructuralAnchorPair,"parent_structure_id"|"boundary_kind"|"usage"|"ref_segment"|"target_segment"|"rationale">>,reason:string):StructuralAnchorPair {
 if(Object.entries(patch).every(([k,v])=>JSON.stringify(p[k as keyof StructuralAnchorPair])===JSON.stringify(v)))return p;
 return {...invalidatePair(p,reason),...copy(patch),revision:p.revision+1,updated_at:now()};
}
export function completePair(p:StructuralAnchorPair):boolean {
 const valid=(s:Segment|null)=>Array.isArray(s)&&s.length===2&&s.every(v=>Array.isArray(v)&&v.length===2&&v.every(n=>typeof n==="number"&&Number.isFinite(n)))&&(s[0][0]!==s[1][0]||s[0][1]!==s[1][1]);
 return Boolean(valid(p.ref_segment)&&valid(p.target_segment)&&p.parent_structure_id.trim());
}
export function reviewPair(p:StructuralAnchorPair,status:"REVIEWED"|"REJECTED",identityChecked:boolean,scopeChecked:boolean):StructuralAnchorPair {
 if(!completePair(p)||!p.rationale.trim())throw Error("양쪽 구간·Parent Structure ID·검토 근거가 필요합니다.");
 if(status==="REVIEWED"&&(!identityChecked||!scopeChecked))throw Error("구간의 동일성 가설과 분석 범위를 직접 확인하세요.");
 const at=now(),reason=p.rationale.trim();
 return {...p,review:{status,reason,at,applies_to_revision:p.revision,identity_hypothesis_checked:status==="REVIEWED"&&identityChecked,
  interval_scope_checked:status==="REVIEWED"&&scopeChecked,scope:REVIEW_SCOPE},
  review_history:[...p.review_history,{status,reason,at,basis:basisOf(p)}].slice(-32),updated_at:at};
}
export function parentConflicts(pairs:StructuralAnchorPair[]):{parent:string;fit_ids:string[];validation_ids:string[]}[] {
 const groups=new Map<string,{parent:string;fit_ids:string[];validation_ids:string[]}>();
 for(const p of pairs){
  if(!["FIT","VALIDATION"].includes(p.usage)||!p.parent_structure_id.trim())continue;
  const key=p.parent_structure_id.trim().normalize("NFC").toLocaleLowerCase("en-US");
  if(!groups.has(key))groups.set(key,{parent:p.parent_structure_id.trim(),fit_ids:[],validation_ids:[]});
  groups.get(key)![p.usage==="FIT"?"fit_ids":"validation_ids"].push(p.id);
 }
 return [...groups.values()].filter(g=>g.fit_ids.length&&g.validation_ids.length);
}

const fail=(message:string):never=>{throw Error(message);};
function obj(value:unknown,label:string):Record<string,unknown>{
 if(!value||typeof value!=="object"||Array.isArray(value))return fail(label+" 형식이 잘못됐습니다.");return value as Record<string,unknown>;
}
function str(value:unknown,label:string,max=2000,empty=false):string {
 if(typeof value!=="string"||value.length>max||(!empty&&!value.trim()))return fail(label+" 값이 잘못됐습니다.");return value;
}
function integer(value:unknown,label:string):number {
 if(typeof value!=="number"||!Number.isSafeInteger(value)||value<0)return fail(label+" 값이 잘못됐습니다.");return value;
}
function choice<T extends string>(value:unknown,choices:readonly T[],label:string):T {
 if(typeof value!=="string"||!choices.includes(value as T))return fail(label+" 값이 잘못됐습니다.");return value as T;
}
function date(value:unknown):string {
 const text=str(value,"시각",40);if(!/^\d{4}-\d{2}-\d{2}T.*Z$/.test(text)||!Number.isFinite(Date.parse(text)))return fail("시각 형식이 잘못됐습니다.");return text;
}
function boolean(value:unknown):boolean {if(typeof value!=="boolean")return fail("확인 값은 true/false여야 합니다.");return value;}
export function checkedSegment(value:unknown,image:Pick<NativeImage,"width"|"height">):Segment {
 if(!Array.isArray(value)||value.length!==2)return fail("경계 구간은 두 점이어야 합니다.");
 const points=value.map(p=>{
  if(!Array.isArray(p)||p.length!==2||p.some(n=>typeof n!=="number"||!Number.isFinite(n))||p[0]<0||p[1]<0||p[0]>image.width||p[1]>image.height)return fail("구간 좌표는 해당 원본 이미지 안의 유한한 숫자여야 합니다.");
  return [p[0],p[1]] as Vertex;
 });
 if(points[0][0]===points[1][0]&&points[0][1]===points[1][1])return fail("길이 0인 구간은 저장할 수 없습니다.");
 return points as Segment;
}
function readBasis(v:unknown,ref:NativeImage,target:NativeImage,nullable:boolean):AnchorBasis {
 const b=obj(v,"구간 기록");return {revision:integer(b.revision,"revision"),parent_structure_id:str(b.parent_structure_id,"Parent Structure ID",120),
  boundary_kind:choice(b.boundary_kind,["OUTER","INNER","OTHER","UNDETERMINED"] as const,"경계 종류"),
  usage:choice(b.usage,["FIT","VALIDATION","HOLD","EXCLUDE"] as const,"용도"),
  ref_segment:nullable&&b.ref_segment===null?null:checkedSegment(b.ref_segment,ref),
  target_segment:nullable&&b.target_segment===null?null:checkedSegment(b.target_segment,target)};
}
function readPair(value:unknown,ref:NativeImage,target:NativeImage):StructuralAnchorPair {
 const p=obj(value,"Anchor Pair"),basis=readBasis(p,ref,target,false);
 if(p.endpoint_semantics!==ENDPOINT_SEMANTICS||p.landmark_correspondence!==false)fail("분석 구간 지정점을 Landmark 대응점으로 해석할 수 없습니다.");
 const source=obj(p.source,"출처"),kind=choice(source.kind,["MANUAL","ANALYST_REFERENCE"] as const,"출처");
 const review=obj(p.review,"검토 기록");if(review.scope!==REVIEW_SCOPE)fail("지원하지 않는 검토 범위입니다.");
 const status=choice(review.status,["UNREVIEWED","REVIEWED","INVALIDATED","REJECTED"] as const,"검토 상태");
 const parsedReview:AnchorReview={status,reason:str(review.reason,"검토 기록 근거",2000,true),at:review.at===null?null:date(review.at),
  applies_to_revision:review.applies_to_revision===null?null:integer(review.applies_to_revision,"검토 revision"),
  identity_hypothesis_checked:boolean(review.identity_hypothesis_checked),interval_scope_checked:boolean(review.interval_scope_checked),scope:REVIEW_SCOPE};
 if(status==="REVIEWED"&&(!parsedReview.reason.trim()||!parsedReview.at||parsedReview.applies_to_revision!==basis.revision||!parsedReview.identity_hypothesis_checked||!parsedReview.interval_scope_checked))fail("검토 완료 기록의 근거·범위·revision이 맞지 않습니다.");
 if(!Array.isArray(p.review_history)||p.review_history.length>32)fail("검토 이력은 32개 이하의 배열이어야 합니다.");
 const history=(p.review_history as unknown[]).map(item=>{
  const e=obj(item,"검토 이력");return {status:choice(e.status,["UNREVIEWED","REVIEWED","INVALIDATED","REJECTED"] as const,"이력 상태"),reason:str(e.reason,"이력 근거",2000,true),at:date(e.at),basis:readBasis(e.basis,ref,target,true)};
 });
 return {...basis,id:str(p.id,"Anchor ID",80),rationale:str(p.rationale,"근거",2000,true),
  endpoint_semantics:ENDPOINT_SEMANTICS,landmark_correspondence:false,
  source:kind==="MANUAL"?{kind}:{kind,reference_id:str(source.reference_id,"참고 ID",40),report_sha256:str(source.report_sha256,"참고 문서 해시",64)},
  review:parsedReview,review_history:history,created_at:date(p.created_at),updated_at:date(p.updated_at)};
}
export function makeAnchorFile(pairs:StructuralAnchorPair[],ref:NativeImage,target:NativeImage,roi:TargetROI|null):AnchorFile {
 if(pairs.length>MAX_ANCHORS)fail("Anchor Pair는 256개까지 저장할 수 있습니다.");
 const parsed=pairs.map(p=>readPair(p,ref,target));if(new Set(parsed.map(p=>p.id)).size!==parsed.length)fail("Anchor ID가 중복됩니다.");
 const file:AnchorFile={schema_version:1,kind:"STRUCTURAL_ANCHOR_SET",coordinate_system:"image_native_pixels",endpoint_semantics:ENDPOINT_SEMANTICS,
  landmark_correspondence:false,ref_image:bindingFor(ref),target_image:bindingFor(target),target_roi_context:roi?readROI(JSON.stringify(roi),target):null,pairs:parsed,exported_at:now()};
 if(new TextEncoder().encode(JSON.stringify(file)).length>MAX_ANCHOR_BYTES)fail("Anchor JSON은 2MiB 이하여야 합니다.");return file;
}
export function readAnchorFile(text:string,ref:NativeImage,target:NativeImage):AnchorFile {
 if(new TextEncoder().encode(text).length>MAX_ANCHOR_BYTES)fail("Anchor JSON은 2MiB 이하여야 합니다.");
 let parsed:unknown;try{parsed=JSON.parse(text);}catch{fail("올바른 Anchor JSON이 아닙니다.");}
 const file=obj(parsed,"Anchor JSON");
 if(file.schema_version!==1||file.kind!=="STRUCTURAL_ANCHOR_SET"||file.coordinate_system!=="image_native_pixels"||file.endpoint_semantics!==ENDPOINT_SEMANTICS||file.landmark_correspondence!==false)fail("구조 Anchor 구간 JSON 형식이 아닙니다. ROI나 SIFT 점 대응 JSON은 사용할 수 없습니다.");
 if(!bindingsMatch(file.ref_image,ref)||!bindingsMatch(file.target_image,target))fail("Ref/Target PDF·페이지·추출 배율·원본 크기가 현재 입력과 다릅니다. 기존 검토 상태를 적용할 수 없습니다.");
 if(!Array.isArray(file.pairs)||file.pairs.length>MAX_ANCHORS)fail("Anchor Pair 배열이 잘못됐습니다.");
 const pairs=(file.pairs as unknown[]).map(p=>readPair(p,ref,target));
 const checked=makeAnchorFile(pairs,ref,target,file.target_roi_context==null?null:readROI(JSON.stringify(file.target_roi_context),target));
 return {...checked,exported_at:date(file.exported_at),pairs:checked.pairs.map(p=>p.review.status==="UNREVIEWED"?p:invalidatePair(p,"JSON 불러오기 후 대응 구간 재검토 필요"))};
}
