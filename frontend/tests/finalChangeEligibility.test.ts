import assert from 'node:assert/strict';
import {isFinalChangeKind, type FinalChangeKind} from '../src/finalChangeEligibility.ts';
const dimension:FinalChangeKind={domain:'ANNOTATION',entity_type:'DimensionText',
  automatic_confirmation:'DIMENSION_TEXT_WITH_PAIRED_LINE_GEOMETRY',change_types:['DIMENSION_TEXT_VALUE'],
  dimension_evidence:{validation_status:'SUPPORTED'},physical_change_inferred:false,before:'900',after:'940'};
assert.equal(isFinalChangeKind(dimension),true);
assert.equal(isFinalChangeKind({domain:'STRUCTURAL',entity_type:'Wall'}),true);
for(const change of [
  {entity_type:'CodeMarker'}, {dimension_evidence:{validation_status:'PROPOSED'}},
  {physical_change_inferred:true}, {physical_change_inferred:undefined},
  {automatic_confirmation:undefined}, {change_types:['MOVED']}, {after:'900'},
  {change_types:['DIMENSION_TEXT_VALUE','MOVED']}, {before:undefined}
]) assert.equal(isFinalChangeKind({...dimension,...change}),false,JSON.stringify(change));
console.log('Final list permits supported dimension text only; existing structural path preserved.');
