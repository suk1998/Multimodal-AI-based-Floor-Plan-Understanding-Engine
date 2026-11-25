import assert from "node:assert/strict";
import {initialEditor,reduceEditor} from "../src/roiEditor.ts";
import type {EditorState,EditorAction} from "../src/roiEditor.ts";
import type {Vertex} from "../src/roi.ts";
const polygon:Vertex[]=[[600,450],[1500,450],[1500,1100],[600,1100]];
const apply=(state:EditorState,...actions:EditorAction[])=>actions.reduce(reduceEditor,state);
const loaded=()=>apply(initialEditor(),{type:"load",points:polygon});
let count=0;
function test(name:string,fn:()=>void){fn();count++;console.log(`PASS ${name}`);}
test("pan pauses drawing without losing vertices, and draw resumes",()=>{
 let state=apply(initialEditor(),{type:"start"},{type:"add",point:[600,450]},{type:"add",point:[1500,450]});
 const points=state.points;state=apply(state,{type:"pan"});assert.deepEqual(state.points,points);assert.equal(state.drawing,true);assert.equal(state.mode,"pan");
 state=apply(state,{type:"start"},{type:"add",point:[1500,1100]},{type:"finish"});assert.equal(state.points.length,3);assert.equal(state.closed,true);assert.equal(state.drawing,false);
});
test("deselect never cancels a draft or changes its mode",()=>{
 const state=apply(initialEditor(),{type:"start"},{type:"add",point:[700,600]});
 const next=apply(state,{type:"deselect"});assert.deepEqual(next.points,state.points);assert.equal(next.mode,"draw");assert.equal(next.drawing,true);
});
test("deselect affects only the selected vertex of a finished polygon",()=>{
 const state=apply(loaded(),{type:"select",index:2}),next=apply(state,{type:"deselect"});
 assert.equal(next.selected,null);assert.deepEqual(next.points,polygon);assert.equal(next.closed,true);assert.equal(next.mode,state.mode);
});
test("last point undo removes exactly one draft vertex even when panning",()=>{
 const state=apply(initialEditor(),{type:"start"},{type:"add",point:[600,450]},{type:"add",point:[1500,450]},{type:"pan"},{type:"undo"});
 assert.deepEqual(state.points,[[600,450]]);assert.equal(state.mode,"pan");assert.equal(state.drawing,true);
 assert.deepEqual(apply(loaded(),{type:"undo"}).points,polygon);
});
test("only explicit drawing cancellation restores the preceding polygon",()=>{
 const state=apply(loaded(),{type:"start"},{type:"add",point:[900,600]},{type:"pan"},{type:"cancelDrawing"});
 assert.deepEqual(state.points,polygon);assert.equal(state.closed,true);assert.equal(state.mode,"pan");assert.equal(state.drawing,false);
});
test("navigation cannot add points or modify vertices",()=>{
 const state=apply(loaded(),{type:"pan"},{type:"add",point:[900,600]},{type:"move",index:0,point:[950,650]});assert.deepEqual(state.points,polygon);
});
test("reset clears draft, backup and selection without leaking into later work",()=>{
 let state=apply(loaded(),{type:"select",index:1},{type:"start"},{type:"add",point:[900,600]},{type:"reset"});
 assert.deepEqual(state.points,[]);assert.equal(state.closed,false);assert.equal(state.drawing,false);assert.equal(state.selected,null);assert.equal(state.backup,null);
 state=apply(state,{type:"start"},{type:"cancelDrawing"});assert.deepEqual(state.points,[]);
});
test("button commands invalidate old gestures even if mode is unchanged",()=>{
 const state=loaded();assert.ok(apply(state,{type:"edit"}).interactionKey>state.interactionKey);
 assert.equal(apply(state,{type:"select",index:0}).interactionKey,state.interactionKey);
});
console.log(`${count} independent ROI command checks passed.`);
