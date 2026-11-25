import assert from 'node:assert/strict';
import {referenceInput,referenceMatches} from '../src/referenceUnderstanding.ts';
import {validDrawingRegion} from '../src/observationAudit.ts';
const image={id:'target-frame',document:{id:'target-pdf',sha256:'target-sha'},page:4,zoom:2,width:2382,height:1684};
const request=referenceInput(image as never,undefined,'Target');
assert.equal(request.source_role,'Target');
assert.equal(request.image.frame_id,'Target:target-frame');
assert.ok(!('reference_run_id' in request));
const bundle={schema_version:'target_understanding_bundle/1',source_role:'Target',frame_id:'Target:target-frame',
 source_binding:{document_id:'target-pdf',image_id:'target-frame',source_role:'Target',source_sha256:'target-sha',page:4,extraction_zoom:2,width:2382,height:1684}};
assert.equal(referenceMatches(bundle as never,image as never,'Target'),true);
assert.equal(referenceMatches(bundle as never,image as never,'Ref'),false);
const region={frame_id:'Target:target-frame',source_role:'Target',bbox_native:[100,100,1000,1000]};
assert.equal(validDrawingRegion(region as never,image as never,'Target'),true);
assert.equal(validDrawingRegion(region as never,image as never,'Ref'),false);
console.log('Target request, own-frame binding and DrawingRegion role isolation passed');
