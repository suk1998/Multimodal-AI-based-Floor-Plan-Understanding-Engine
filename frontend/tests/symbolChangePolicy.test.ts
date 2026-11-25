import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {isFinalChangeKind} from '../src/finalChangeEligibility.ts';
const cases=JSON.parse(readFileSync(new URL('../../tests/final_change_policy_cases.json',import.meta.url),'utf8'));
const resolved=new Map<string,any>();
for(const item of cases){
 const row=item.row??{...structuredClone(resolved.get(item.mutate)),...item.patch};
 if(item.proof_patch)row.symbol_evidence={...row.symbol_evidence,...item.proof_patch};
 resolved.set(item.id,row);
 assert.equal(isFinalChangeKind(row),item.expected,item.id);
}
console.log(`${cases.length} shared annotation policy cases passed in TypeScript.`);
