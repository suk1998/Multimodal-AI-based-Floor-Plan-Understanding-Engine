import assert from "node:assert/strict";
import {makeROI,readROI,validateVertices,MAX_ROI_BYTES} from "../src/roi.ts";
import type {NativeImage} from "../src/api.ts";
// Test-only shapes, never loaded as a default application ROI.
const image={id:"test-sha:4:2:2382x1684",document:{sha256:"test-sha",id:"session-a",filename:"test.pdf",page_count:6},page:4,zoom:2,width:2382,height:1684} as NativeImage;
const vertices=[[480.125,350.25],[1600.5,400],[1550.5,1200.25],[500.75,1100]];
let count=0;
function test(name: string,fn:()=>void){fn();count++;console.log(`PASS ${name}`);}
test("JSON round trip preserves fractional native pixel coordinates",()=>{
  const roi=makeROI(image,vertices);assert.deepEqual(readROI(JSON.stringify(roi),image).vertices,vertices);
  vertices[0][0]=481;assert.equal(roi.vertices[0][0],480.125);
});
test("identity survives upload session ID and filename changes",()=>{
  const text=JSON.stringify(makeROI(image,vertices));
  assert.deepEqual(readROI(text,{...image,document:{...image.document,id:"session-b",filename:"renamed.pdf"}}).vertices,vertices);
});
test("rejects another PDF, page, zoom, dimensions and image ID",()=>{
  const text=JSON.stringify(makeROI(image,vertices));
  for(const next of [{...image,document:{...image.document,sha256:"other"}},{...image,page:5},{...image,zoom:1},
    {...image,width:1191},{...image,height:842},{...image,id:"other-image"}])assert.throws(()=>readROI(text,next),/다릅니다/);
});
test("rejects other coordinate formats, versions, roles and missing metadata",()=>{
  const roi=makeROI(image,vertices);
  for(const value of [null,[],{},"text",{...roi,schema_version:2},{...roi,role:"Ref"},{...roi,coordinate_system:"normalized"},{...roi,image:null}])assert.throws(()=>readROI(JSON.stringify(value),image));
});
test("rejects malformed and oversized JSON",()=>{
  assert.throws(()=>readROI("not json",image));assert.throws(()=>readROI(" ".repeat(MAX_ROI_BYTES+1),image));
});
test("rejects invalid vertices without clamping or coordinate coercion",()=>{
  for(const points of [[],[[1,1],[2,2]],[[1,1],[2,2],[3,3]],[[1,1],[NaN,2],[3,4]],
    [[1,1],[Infinity,2],[3,4]],[[1,1],["2",2],[3,4]],[[1,1],[-1,2],[3,4]],
    [[1,1],[2383,2],[3,4]],[[1,1],[2,1685],[3,4]],[[1,1],[1,1],[3,4]],
    [[1,1],[4,4],[1,4],[4,1]],[[0,0],[10,0],[5,0],[5,10]],
    Array.from({length:513},()=>[1,1])])assert.throws(()=>validateVertices(points,2382,1684));
});
test("supports image-edge coordinates and concave simple polygons",()=>{
  assert.equal(validateVertices([[0,0],[2382,0],[2382,1684],[0,1684]],2382,1684).length,4);
  assert.equal(validateVertices([[1,1],[9,1],[9,9],[5,5],[1,9]],10,10).length,5);
});
console.log(`${count} ROI contract checks passed.`);
