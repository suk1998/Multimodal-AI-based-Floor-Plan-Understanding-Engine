import assert from 'node:assert/strict';
import {startHistory,pushHistory,stepHistory,pickCandidate,pickSpace,focusCase} from '../src/studioApi.ts';
import type {Candidate,Case,Space} from '../src/studioApi.ts';
let tests=0;
function test(name:string,fn:()=>void){fn();tests++;console.log('ok',name)}
test('undo/redo keeps immutable snapshots',()=>{const a={n:0},b={n:1};const h=pushHistory(startHistory(a),b);assert.equal(stepHistory(h,'undo').present,a);assert.equal(stepHistory(stepHistory(h,'undo'),'redo').present,b);assert.deepEqual(a,{n:0})});
test('a drag is one undo operation',()=>{let h=startHistory({x:0});h=pushHistory(h,{x:1},'drag');h=pushHistory(h,{x:2},'drag');assert.equal(h.past.length,1);assert.equal(stepHistory(h,'undo').present.x,0)});
test('new edits invalidate redo',()=>{let h=pushHistory(startHistory(0),1);h=stepHistory(h,'undo');h=pushHistory(h,2);assert.equal(h.future.length,0)});
test('empty undo is stable',()=>{const h=startHistory(0);assert.equal(stepHistory(h,'undo'),h);assert.equal(stepHistory(h,'redo'),h)});
test('history stays bounded',()=>{let h=startHistory(0);for(let i=1;i<=80;i++)h=pushHistory(h,i);assert.equal(h.past.length,50)});
const candidate=(id:string,type:string,line:number[][])=>({candidate_key:id,element_type:type,display_segments_native:[line],display_polygons_native:[]} as unknown as Candidate);
test('native hit testing prefers a visible door over wall at same location',()=>{const wall=candidate('wall','Wall',[[20,20],[20,100]]),door=candidate('door','Door',[[20,20],[20,100]]);assert.equal(pickCandidate([20,50],[wall,door],4)?.candidate_key,'door')});
test('distant clicks do not select a candidate',()=>assert.equal(pickCandidate([100,100],[candidate('door','Door',[[20,20],[20,100]])],4),null));
test('Q05 focus derives only camera bounds from existing old Door',()=>{const c={case_id:'Q05',display_bbox_native:[0,0,200,200]} as Case;const p={...candidate('poc1|Target:Door:012','Door',[[20,20],[20,100]]),bbox_native:[20,20,25,100]} as Candidate;assert.deepEqual(focusCase(c,[p]),[-40,-40,85,160]);assert.deepEqual(p.bbox_native,[20,20,25,100])});
const space={id:'room',seed:[20,20],focus_bounds:[0,0,500,500],polygon:{outer_rings:[[[0,0],[500,0],[500,500],[0,500]]],holes:[[[100,100],[200,100],[200,200],[100,200]]]}} as Space;
test('space interior click uses preserved polygon beyond label seed',()=>assert.equal(pickSpace([400,400],[space])?.id,'room'));
test('space holes and outside clicks do not use camera rectangle as geometry',()=>{assert.equal(pickSpace([150,150],[space]),null);assert.equal(pickSpace([501,400],[space]),null)});
test('open-boundary space without polygon has label-near navigation only',()=>{const open={...space,polygon:null};assert.equal(pickSpace([20,20],[open])?.id,'room');assert.equal(pickSpace([400,400],[open]),null)});
console.log(tests+' studio interaction tests passed');
