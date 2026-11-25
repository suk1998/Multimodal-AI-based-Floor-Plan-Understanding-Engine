import type {Catalog,Package,Role,Saved} from './studioApi';

export interface StoredReview extends Saved{schema_version:1;storage_scope:'PUBLIC_BROWSER_ONLY'}
export interface StorageLike{getItem(key:string):string|null;setItem(key:string,value:string):void}
export function publicKey(c:Catalog){return 'fpue-public-review:'+c.manifest.dataset_id+':'+c.manifest.registry_sha256}
export function validatePublicReview(p:Package,c:Catalog):void{
 if(!p||p.dataset_kind!=='PUBLIC_PAGE4_BROWSER_REVIEW'||p.storage_scope!=='PUBLIC_BROWSER_ONLY'||p.dataset_id!==c.manifest.dataset_id||p.registry_sha256!==c.manifest.registry_sha256)throw Error('이 공개 도면의 검토 파일이 아닙니다. 현재 검토는 유지했습니다.');
 if(JSON.stringify(p.bindings)!==JSON.stringify(c.manifest.bindings))throw Error('PDF·페이지·배율·Frame 정보가 다릅니다. 현재 검토는 유지했습니다.');
 if(!Array.isArray(p.gt_annotations)||p.gt_annotations.length>5000||!p.candidate_reviews)throw Error('검토 파일 형식 또는 크기를 확인하세요.');
 const ids=new Set();
 for(const g of p.gt_annotations){
  const b=p.bindings[g.role as Role];
  if(!b||g.frame_id!==b.frame_id||g.source_image_sha256!==b.image_sha256||JSON.stringify(g.source_binding)!==JSON.stringify(b)||!['DRAFT','CONFIRMED','UNCERTAIN'].includes(g.review_status)||!['Wall','Door','Window','Opening','SpaceBoundary'].includes(g.element_type)||ids.has(g.gt_id))throw Error('정답의 원본 Frame 또는 유형이 다릅니다.');
  ids.add(g.gt_id);const geom=g.geometry_native,points=geom?.segments_native?.flat()||geom?.points_native;
  if(geom?.coordinate_system!=='image_native_pixels'||!Array.isArray(points)||points.length<2||points.length>512||points.some(p=>!Array.isArray(p)||p.length!==2||p.some(v=>typeof v!=='number'||!Number.isFinite(v))||p[0]<0||p[1]<0||p[0]>b.width||p[1]>b.height))throw Error('정답의 원본 픽셀 좌표가 올바르지 않습니다.');
 }
 const keys=new Set(c.candidates.map(v=>v.candidate_key));if(Object.keys(p.candidate_reviews).some(k=>!keys.has(k)))throw Error('다른 후보 데이터에 대한 검토입니다.');
}
export function readPublicReview(storage:StorageLike,c:Catalog):StoredReview|null{
 const text=storage.getItem(publicKey(c));if(!text)return null;
 const s=JSON.parse(text) as StoredReview;if(s.schema_version!==1||s.storage_scope!=='PUBLIC_BROWSER_ONLY'||!Number.isSafeInteger(s.revision)||s.revision<1)throw Error('브라우저 저장본을 읽지 못했습니다. JSON 백업을 확인하세요.');
 validatePublicReview(s.package,c);return s;
}
export function savePublicReview(storage:StorageLike,c:Catalog,p:Package,base:number):StoredReview{
 validatePublicReview(p,c);const old=readPublicReview(storage,c);
 if((old?.revision||0)!==base)throw Error('다른 탭에서 저장한 검토가 있습니다. 내 수정은 유지했습니다. 브라우저 저장본을 확인하세요.');
 const saved:StoredReview={schema_version:1,storage_scope:'PUBLIC_BROWSER_ONLY',revision:base+1,latest_revision:base+1,package:p,saved_at:new Date().toISOString()};
 try{storage.setItem(publicKey(c),JSON.stringify(saved))}catch{throw Error('브라우저에 저장하지 못했습니다. 내 수정은 유지했습니다. GT JSON을 다운로드하세요.');}
 return saved;
}
export function downloadPublicReview(p:Package){
 const url=URL.createObjectURL(new Blob([JSON.stringify(p,null,2)],{type:'application/json'})),a=document.createElement('a');a.href=url;a.download='fpue_public_page4_GT.json';document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);
}
