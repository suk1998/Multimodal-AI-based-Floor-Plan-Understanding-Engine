import {observationLayer} from '../src/observations.ts';
import assert from 'node:assert/strict';
import {omittedInCell,validDrawingRegion} from '../src/observationAudit.ts';
import type {DrawingRegion} from '../src/observationAudit.ts';
import {referenceInput} from '../src/referenceUnderstanding.ts';
import type {NativeImage} from '../src/api.ts';
const image={id:'native',width:2382,height:1684,page:4,zoom:2,document:{id:'doc',sha256:'sha'}} as NativeImage;
const region={region_id:'proposal',frame_id:'Ref:native',source_role:'Ref',bbox_native:[20.25,30.5,1200.5,1500],status:'PROPOSED',is_physical_space_polygon:false,kind:'MIXED_OR_UNKNOWN'} as DrawingRegion;
assert.ok(validDrawingRegion(region,image));
for(const update of [{frame_id:'Target:native'},{source_role:'Target'},{bbox_native:[0,0,3000,100]},{bbox_native:[0,0,0,100]},{bbox_native:[0,0,NaN,100]}])assert.equal(validDrawingRegion({...region,...update} as DrawingRegion,image),false);
assert.equal(omittedInCell({cell_id:'0:0',bbox_native:[0,0,100,100],counts_by_type:{PdfLine:{retained:5,omitted_due_to_limit:3},TextRegion:{retained:1},PdfCurve:{omitted_due_to_limit:2}}}),5);
const selected=referenceInput(image,{run_id:'old-owned-run',region_id:'proposal'});
assert.equal(selected.image.width,2382);assert.equal(selected.image.frame_id,'Ref:native');assert.equal(selected.drawing_region_selection?.region_id,'proposal');assert.ok(!('roi_bbox_native' in selected));assert.ok(!('bbox_native' in selected.drawing_region_selection!));
console.log('DrawingRegion frame/scope binding, server-selected coordinates and coverage semantics passed');

assert.equal(observationLayer('TextDetection'),'detections');assert.equal(observationLayer('TextRegion'),'text');assert.equal(observationLayer('PdfText'),'pdftext');
