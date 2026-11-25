import assert from 'node:assert/strict';
import {publicKey,savePublicReview,readPublicReview,validatePublicReview} from '../src/publicDraft.ts';
import type {Catalog,Package} from '../src/studioApi.ts';
let checks=0;const test=(name:string,f:()=>void)=>{f();checks++;console.log('ok',name)};
const binding={frame_id:'Target:source:4:2',source_sha256:'source',image_sha256:'image',width:2382,height:1684,page:4,extraction_zoom:2};
const catalog={manifest:{dataset_id:'public-test',registry_sha256:'registry',bindings:{Target:binding,Ref:{...binding,frame_id:'Ref:source:4:2'}}},candidates:[]} as unknown as Catalog;
const packet=()=>({dataset_id:'public-test',dataset_kind:'PUBLIC_PAGE4_BROWSER_REVIEW',storage_scope:'PUBLIC_BROWSER_ONLY',registry_sha256:'registry',bindings:catalog.manifest.bindings,gt_annotations:[],candidate_reviews:{},annotation_revision:'r1'}) as unknown as Package;
class Memory{values=new Map<string,string>();getItem(k:string){return this.values.get(k)||null}setItem(k:string,v:string){this.values.set(k,v)}}
test('public namespace does not use legacy authenticated keys',()=>{assert.match(publicKey(catalog),/^fpue-public-review:/);assert.ok(!publicKey(catalog).includes('fpue-draft'))});
test('explicit save and restoration keep exact original coordinates',()=>{const s=new Memory(),p=packet();savePublicReview(s,catalog,p,0);assert.deepEqual(readPublicReview(s,catalog)?.package,p)});
test('saving from a stale tab preserves previous saved snapshot',()=>{const s=new Memory(),p=packet();savePublicReview(s,catalog,p,0);assert.throws(()=>savePublicReview(s,catalog,p,0),/다른 탭/);assert.equal(readPublicReview(s,catalog)?.revision,1)});
test('storage quota failure never reports success',()=>{assert.throws(()=>savePublicReview({getItem:()=>null,setItem:()=>{throw Error('quota')}},catalog,packet(),0),/내 수정은 유지/)});
test('private developer GT is not silently imported',()=>{const p=packet();p.dataset_kind='REAL_PAGE4_HUMAN_REVIEW';assert.throws(()=>validatePublicReview(p,catalog),/공개 도면/)});
test('changed page zoom frame or image invalidates restoration',()=>{for(const key of ['page','extraction_zoom','frame_id','image_sha256']){const p=structuredClone(packet());p.bindings.Target[key]='changed';assert.throws(()=>validatePublicReview(p,catalog),/Frame 정보/)}});
test('corrupted stored data is retained and reported',()=>{const s=new Memory();s.setItem(publicKey(catalog),'broken');assert.throws(()=>readPublicReview(s,catalog));assert.equal(s.getItem(publicKey(catalog)),'broken')});
test('manual GT cannot escape its native frame',()=>{const p=packet();p.gt_annotations=[{gt_id:'g',role:'Target',frame_id:binding.frame_id,source_image_sha256:'image',source_binding:binding,element_type:'Door',review_status:'DRAFT',geometry_native:{coordinate_system:'image_native_pixels',points_native:[[10,10],[20,20]]}} as never];assert.doesNotThrow(()=>validatePublicReview(p,catalog));p.gt_annotations[0].geometry_native.points_native![1]=[5000,20];assert.throws(()=>validatePublicReview(p,catalog),/픽셀 좌표/)});
console.log(checks+' public browser storage tests passed');
