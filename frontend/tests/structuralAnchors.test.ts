import assert from "node:assert/strict";
import type {NativeImage} from "../src/api.ts";
import type {Vertex} from "../src/roi.ts";
import {bindingFor,bindingsMatch,checkedSegment,ENDPOINT_SEMANTICS,makeAnchorFile,MAX_ANCHOR_BYTES,newPair,parentConflicts,patchPair,readAnchorFile,reviewPair} from "../src/structuralAnchors.ts";
import {anchorReferences,referenceImages} from "../src/structuralAnchorReferences.ts";
const image=(sha:string,w:number,h:number):NativeImage=>({id:`${sha}:4:2:${w}x${h}`,document:{sha256:sha,id:"upload-a",filename:"drawing.pdf",page_count:6},page:4,zoom:2,width:w,height:h,blob:new Blob(),url:"blob:test"});
const ref=image("a".repeat(64),2400,1700),target=image("b".repeat(64),1900,1400);
const pair=(id="manual-1")=>({...newPair(id),parent_structure_id:"wall-A",ref_segment:checkedSegment([[600.125,450.25],[600.125,600]],ref),target_segment:checkedSegment([[490,520],[490,470]],target),rationale:"사용자가 국소 경계 대응을 검토한 테스트 기록"});
const reviewed=()=>reviewPair(pair(),"REVIEWED",true,true);
let count=0;function test(name:string,fn:()=>void){fn();count++;console.log(`PASS ${name}`);}
test("new pairs are HOLD/unreviewed and do not infer counterpart geometry",()=>{
 const p=newPair("empty");assert.equal(p.usage,"HOLD");assert.equal(p.review.status,"UNREVIEWED");assert.equal(p.ref_segment,null);assert.equal(p.target_segment,null);assert.equal(p.landmark_correspondence,false);
});
test("unequal interval lengths and reversed order preserve fractional native coordinates",()=>{
 const p=pair(),file=makeAnchorFile([p],ref,target,null),read=readAnchorFile(JSON.stringify(file),ref,target);
 assert.deepEqual(read.pairs[0].ref_segment,p.ref_segment);assert.deepEqual(read.pairs[0].target_segment,p.target_segment);assert.equal(read.endpoint_semantics,ENDPOINT_SEMANTICS);
 assert.equal("matrix" in read,false);assert.equal("stable" in read.pairs[0],false);
});
test("review requires complete pair, parent, reason and explicit interval checks",()=>{
 assert.throws(()=>reviewPair(newPair("empty"),"REVIEWED",true,true));
 assert.throws(()=>reviewPair({...pair(),parent_structure_id:""},"REVIEWED",true,true));
 assert.throws(()=>reviewPair({...pair(),rationale:" "},"REVIEWED",true,true));
 assert.throws(()=>reviewPair(pair(),"REVIEWED",false,true));assert.throws(()=>reviewPair(pair(),"REVIEWED",true,false));
 const p=reviewed();assert.equal(p.review.status,"REVIEWED");assert.equal(p.review.scope,"SEGMENT_CORRESPONDENCE_ONLY");assert.equal(p.landmark_correspondence,false);
});
test("geometry and metadata edits invalidate review and retain the reviewed basis",()=>{
 const p=reviewed();for(const patch of [{ref_segment:[[620,450],[620,600]] as [Vertex,Vertex]},{usage:"EXCLUDE" as const},{parent_structure_id:"wall-B"},{boundary_kind:"INNER" as const},{rationale:"보완된 근거"}]){
  const next=patchPair(p,patch,"사용자 변경");assert.equal(next.review.status,"INVALIDATED");assert.equal(next.review.applies_to_revision,null);assert.deepEqual(next.review_history[0].basis.ref_segment,p.ref_segment);assert.equal(p.review.status,"REVIEWED");
 }
 assert.equal(patchPair(p,{usage:p.usage},"same"),p);
});
test("same Parent across FIT/VALIDATION warns; HOLD/EXCLUDE never count as independent checks",()=>{
 const a={...pair("a"),usage:"FIT" as const,parent_structure_id:" Wall-A "},b={...pair("b"),usage:"VALIDATION" as const,parent_structure_id:"wall-a"};
 assert.equal(parentConflicts([a,b]).length,1);assert.equal(parentConflicts([a,{...b,usage:"HOLD"}]).length,0);assert.equal(parentConflicts([a,{...b,usage:"EXCLUDE"}]).length,0);assert.equal(parentConflicts([a,{...b,parent_structure_id:"wall-B"}]).length,0);
});
test("import validates each Ref and Target PDF/page/zoom/dimensions/image ID",()=>{
 const file=makeAnchorFile([pair()],ref,target,null);
 for(const side of ["ref_image","target_image"] as const)for(const [key,value] of Object.entries({source_sha256:"c".repeat(64),page:5,extraction_zoom:1,width:99,height:88,image_id:"other"})){
  const bad=structuredClone(file);(bad[side] as unknown as Record<string,unknown>)[key]=value;assert.throws(()=>readAnchorFile(JSON.stringify(bad),ref,target),/현재 입력과 다릅니다/);
 }
});
test("reupload with same contents works, but imported review becomes invalidated",()=>{
 const file=makeAnchorFile([reviewed()],ref,target,null);const renamed={...ref,document:{...ref.document,id:"new-upload",filename:"renamed.pdf"}};
 const imported=readAnchorFile(JSON.stringify(file),renamed,target);assert.equal(imported.pairs[0].review.status,"INVALIDATED");assert.deepEqual(imported.pairs[0].ref_segment,file.pairs[0].ref_segment);assert.ok(imported.pairs[0].review_history.some(e=>e.status==="REVIEWED"));
});
test("ROI JSON, SIFT results and Landmark reinterpretations are rejected",()=>{
 const f=makeAnchorFile([pair()],ref,target,null);
 for(const bad of [{schema_version:1,role:"Target",vertices:[]},{matrix:[[1,0,0],[0,1,0],[0,0,1]],anchors:[]},{...f,landmark_correspondence:true},{...f,endpoint_semantics:"LANDMARKS"}])assert.throws(()=>readAnchorFile(JSON.stringify(bad),ref,target));
 const bad=structuredClone(f);bad.pairs[0].landmark_correspondence=true as false;assert.throws(()=>readAnchorFile(JSON.stringify(bad),ref,target));
});
test("coordinate validation rejects coercion and zero length without a fixed minimum pixel span",()=>{
 for(const bad of [[[1,1],[1,1]],[[true,1],[3,4]],[["1",1],[3,4]],[[NaN,1],[3,4]],[[Infinity,1],[3,4]],[[-1,1],[3,4]],[[2401,1],[3,4]],[[1,1701],[3,4]],[[1,1]]])assert.throws(()=>checkedSegment(bad,ref));
 assert.deepEqual(checkedSegment([[0,0],[2400,1700]],ref),[[0,0],[2400,1700]]);assert.equal(checkedSegment([[1,1],[1.00001,1]],ref).length,2);
});
test("incomplete pair, duplicate ID, oversized file and forged review revision are rejected",()=>{
 assert.throws(()=>makeAnchorFile([newPair("incomplete")],ref,target,null));assert.throws(()=>makeAnchorFile([pair(),pair()],ref,target,null));
 assert.throws(()=>readAnchorFile(" ".repeat(MAX_ANCHOR_BYTES+1),ref,target));assert.throws(()=>readAnchorFile("bad json",ref,target));
 const file=makeAnchorFile([reviewed()],ref,target,null);file.pairs[0].revision++;assert.throws(()=>readAnchorFile(JSON.stringify(file),ref,target));
});
test("review history is bounded and carries source geometry before invalidation",()=>{
 let p=pair();for(let i=0;i<40;i++){p=reviewPair(p,"REVIEWED",true,true);p=patchPair(p,{rationale:`검토 근거 ${i}`},"근거 수정");}
 assert.equal(p.review_history.length,32);assert.equal(p.review.status,"INVALIDATED");assert.ok(p.review_history.every(e=>e.basis.ref_segment?.length===2));
});
test("reference spans are unapproved and S03 does not include the modified M01 end",()=>{
 assert.equal(anchorReferences.length,6);const s3=anchorReferences.find(r=>r.id==="S03")!;
 for(const span of [s3.ref_segment,s3.target_segment]){assert.equal(span[0][0],1040);assert.equal(span[1][0],1120);assert.ok(span.every(p=>p[0]<1161));}
 const s6=anchorReferences.find(r=>r.id==="S06")!;assert.ok(s6.target_segment.every(p=>p[0]>470));assert.ok(!anchorReferences.some(r=>r.id==="M04"));
 const realRef={...ref,...referenceImages.ref,document:{...ref.document,sha256:referenceImages.ref.source_sha256},page:referenceImages.ref.page,zoom:referenceImages.ref.extraction_zoom,id:referenceImages.ref.image_id};
 assert.equal(bindingsMatch(referenceImages.ref,realRef),true);assert.equal(bindingsMatch(referenceImages.ref,ref),false);
});
test("ROI context round trip does not mutate the source ROI or become a geometry mapping",()=>{
 const roi={schema_version:1 as const,role:"Target" as const,coordinate_system:"image_native_pixels" as const,image:bindingFor(target),vertices:[[20,20],[1500,20],[1500,1100],[20,1100]] as Vertex[]};
 const original=structuredClone(roi),file=makeAnchorFile([pair()],ref,target,roi),loaded=readAnchorFile(JSON.stringify(file),ref,target);
 assert.deepEqual(roi,original);assert.deepEqual(loaded.target_roi_context,roi);assert.notEqual(loaded.target_roi_context,roi);
});
console.log(`${count} structural anchor contract checks passed.`);
