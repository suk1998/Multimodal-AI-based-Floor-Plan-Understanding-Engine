// Real browser regression. Requires API 8010, Vite 5173 and test Chrome CDP 9223.
import fs from "node:fs";
import {fileURLToPath} from "node:url";
const root=fileURLToPath(new URL("../../",import.meta.url)).replace(/\/$/,"");
const page=await(await fetch("http://127.0.0.1:9223/json/new?http://127.0.0.1:5174",{method:"PUT"})).json();
const socket=new WebSocket(page.webSocketDebuggerUrl);await new Promise((r,j)=>{socket.onopen=r;socket.onerror=j;});
let alignmentRequests=0;
let sequence=0;const pending=new Map(),paused=[],runtimeErrors=[];
socket.onmessage=({data})=>{
  const m=JSON.parse(data);
  if(m.method==="Network.requestWillBeSent"&&m.params.request.url.includes("/api/alignments"))alignmentRequests++;
  if(m.method==="Runtime.exceptionThrown")runtimeErrors.push(m.params.exceptionDetails);
  if(m.method==="Fetch.requestPaused")paused.push(m.params.requestId);
  if(m.id&&pending.has(m.id)){const p=pending.get(m.id);clearTimeout(p.timer);pending.delete(m.id);m.error?p.reject(Error(JSON.stringify(m.error))):p.resolve(m.result);}
};
function cdp(method,params={}){return new Promise((resolve,reject)=>{const id=++sequence,timer=setTimeout(()=>{pending.delete(id);reject(Error(`Timeout ${method}`));},20000);pending.set(id,{resolve,reject,timer});socket.send(JSON.stringify({id,method,params}));});}
async function js(expression){const r=await cdp("Runtime.evaluate",{expression,returnByValue:true,awaitPromise:true});if(r.exceptionDetails)throw Error(JSON.stringify(r.exceptionDetails));return r.result.value;}
const pause=ms=>new Promise(r=>setTimeout(r,ms));
async function until(expression){for(let i=0;i<120;i++){if(await js(expression))return;await pause(150);}throw Error(`Not reached: ${expression}\n${await js('document.body.innerText')}`);}
const near=(a,b,label)=>{if(Math.abs(a-b)>1e-5)throw Error(`${label}: ${a} != ${b}`);};
const inside=(index,code)=>`(()=>{const panel=document.querySelectorAll('.drawing-panel')[${index}];${code}})()`;
async function view(i){return js(inside(i,`const v=panel.querySelector('.viewport'),r=v.getBoundingClientRect(),img=panel.querySelector('img');return {scale:+v.dataset.scale,x:+v.dataset.offsetX,y:+v.dataset.offsetY,width:v.clientWidth,height:v.clientHeight,left:r.left,top:r.top,imageId:v.dataset.imageId,imageWidth:img.naturalWidth,imageHeight:img.naturalHeight,overlay:panel.querySelector('svg').getAttribute('viewBox')};`));}
async function click(label){await js(`[...document.querySelectorAll('button')].find(e=>e.getAttribute('aria-label')===${JSON.stringify(label)})?.click()`);await pause(100);}
async function select(label,value){await js(`(()=>{const s=document.querySelector('select[aria-label=${JSON.stringify(label)}]');s.value=${JSON.stringify(String(value))};s.dispatchEvent(new Event('change',{bubbles:true}));})()`);await pause(100);}
async function extract(){await js(`document.querySelector('button.primary').click()`);await until(`document.querySelectorAll('.drawing-panel img').length===2&&[...document.querySelectorAll('.drawing-panel img')].every(e=>e.complete&&e.naturalWidth>0)&&[...document.querySelectorAll('.viewport')].every(e=>+e.dataset.scale>0)`);await pause(100);}
async function upload(index,path){const d=await cdp('DOM.getDocument');const nodes=await cdp('DOM.querySelectorAll',{nodeId:d.root.nodeId,selector:'input[type=file]'});await cdp('DOM.setFileInputFiles',{nodeId:nodes.nodeIds[index],files:[path]});}
async function shot(path){const data=await cdp('Page.captureScreenshot',{format:'png',captureBeyondViewport:false});fs.writeFileSync(path,Buffer.from(data.data,'base64'));}
// Real PDF input with simulated user review. Records are TEST ONLY, not user approval.
const out=root+'/phase2b3_validation',downloads=fs.mkdtempSync('/tmp/floorplan-structural-anchors-');fs.mkdirSync(out,{recursive:true});
const report={status:'passed',test_only_records:true,checks:{}};
const same=(a,b,label)=>{if(JSON.stringify(a)!==JSON.stringify(b))throw Error(label);};
const scope='.structural-anchor-panel';
async function physical(selector){
 const r=await js(`(()=>{const e=document.querySelector(${JSON.stringify(selector)});if(!e||e.disabled)throw Error('Missing/disabled '+${JSON.stringify(selector)});e.scrollIntoView({block:'center'});const r=e.getBoundingClientRect(),x=r.x+r.width/2,y=r.y+r.height/2;return {x,y,hit:e===document.elementFromPoint(x,y)||e.contains(document.elementFromPoint(x,y))};})()`);
 if(!r.hit)throw Error('Control covered '+selector);
 await cdp('Input.dispatchMouseEvent',{type:'mousePressed',x:r.x,y:r.y,button:'left',clickCount:1});await cdp('Input.dispatchMouseEvent',{type:'mouseReleased',x:r.x,y:r.y,button:'left',clickCount:1});await pause(100);
}
const button=label=>physical(`${scope} button[aria-label="${label}"]`);
async function fill(label,text){await js(`(()=>{const e=document.querySelector(${JSON.stringify(scope+' [aria-label="'+label+'"]')});e.focus();e.select();})()`);await cdp('Input.insertText',{text});await pause(100);}
async function setSelect(label,value){await js(`(()=>{const e=document.querySelector(${JSON.stringify(scope+' select[aria-label="'+label+'"]')});e.value=${JSON.stringify(value)};e.dispatchEvent(new Event('change',{bubbles:true}));})()`);await pause(100);}
async function selected(){return js(`(()=>{const p=document.querySelector('.structural-anchor-editor');return p?{id:p.dataset.selectedAnchor,ref:JSON.parse(p.querySelector('[data-anchor-ref-coordinates]').textContent),target:JSON.parse(p.querySelector('[data-anchor-target-coordinates]').textContent),review:p.querySelector('[data-anchor-review]').textContent}:null;})()`);}
const count=()=>js(`document.querySelectorAll('.structural-anchor-table tbody tr').length`);
async function selectPair(id){await physical(`.structural-anchor-table tr[data-pair-id="${id}"] input[type=radio]`);}
async function anchorView(side){return js(`(()=>{const p=document.querySelector('.structural-anchor-comparison [data-role=${side}]'),v=p.querySelector('.viewport'),r=v.getBoundingClientRect();return {scale:+v.dataset.scale,x:+v.dataset.offsetX,y:+v.dataset.offsetY,left:r.left,top:r.top,width:v.clientWidth,height:v.clientHeight,mode:v.dataset.anchorMode};})()`);}
async function expose(side){await js(`document.querySelector('.structural-anchor-comparison [data-role=${side}] .viewport').scrollIntoView({block:'center'})`);await pause(80);}
async function mouse(type,x,y,extra={}){await cdp('Input.dispatchMouseEvent',{type,x,y,...extra});}
async function nativePoint(side,x,y){
 await expose(side);let v=await anchorView(side),sx=v.left+v.x+x*v.scale,sy=v.top+v.y+y*v.scale;
 if(sx<v.left||sx>=v.left+v.width||sy<v.top||sy>=v.top+v.height){
  await physical(`.structural-anchor-comparison [data-role=${side}] button[aria-label="${side} 화면 맞춤"]`);await expose(side);v=await anchorView(side);sx=v.left+v.x+x*v.scale;sy=v.top+v.y+y*v.scale;
 }
 if(sx<v.left||sx>=v.left+v.width||sy<v.top||sy>=v.top+v.height)throw Error('Requested native test point is outside viewport');
 await mouse('mousePressed',sx,sy,{button:'left',clickCount:1});await mouse('mouseReleased',sx,sy,{button:'left',clickCount:1});await pause(100);
}
async function uploadJSON(label,path){const d=await cdp('DOM.getDocument');const n=await cdp('DOM.querySelector',{nodeId:d.root.nodeId,selector:`input[aria-label="${label}"]`});await cdp('DOM.setFileInputFiles',{nodeId:n.nodeId,files:[path]});await pause(180);}
async function checkReview(){await physical(scope+' input[aria-label="구조 Anchor 동일성 가설 확인"]');await physical(scope+' input[aria-label="구조 Anchor 분석 범위 확인"]');await button('구조 Anchor 대응 검토 완료');if(!(await selected()).review.includes('(REVIEWED)'))throw Error('Explicit review failed');}
const status=()=>js(`document.querySelector('[data-line-alignment-status]')?.dataset.lineAlignmentStatus`);
async function runLines(expected){await until(`!document.querySelector('button[aria-label="구조 구간 정렬 실행"]').disabled`);await button('구조 구간 정렬 실행');await until(`document.querySelector('[data-line-alignment-status]')?.dataset.lineAlignmentStatus===${JSON.stringify(expected)}`);return current();}
async function current(){const id=await js(`document.querySelector('[data-line-run-id]').dataset.lineRunId`);const r=await fetch('http://127.0.0.1:8011/api/structural-alignments/'+id);if(!r.ok)throw Error('Missing server result '+r.status);return r.json();}
const invalidated=async id=>{for(let i=0;i<100;i++){const r=await fetch('http://127.0.0.1:8011/api/structural-alignments/'+id);if(r.status===410)return;const v=await r.json();if(v.validity==='INVALIDATED'){if(v.matrix!==null||v.review.state!=='INVALIDATED')throw Error('Stale approved geometry retained');return;}await pause(50);}throw Error('Server review not invalidated');};
try{
 await cdp('Runtime.enable');await cdp('Page.enable');await cdp('Network.enable');await until(`document.querySelectorAll('input[type=file]').length===2`);
 await upload(0,root+'/source_pdfs/자이사업승인도서.pdf');await until(`document.querySelector('select[aria-label="Ref 페이지"]')?.options.length===6`);
 await upload(1,root+'/source_pdfs/자이실시도서.pdf');await until(`document.querySelector('select[aria-label="Target 페이지"]')?.options.length===6`);
 await select('Ref 페이지',4);await select('Target 페이지',4);await extract();await uploadJSON('Target ROI 불러오기',root+'/alignment_inputs/page4_user_roi.json');
 const originalROI=await js(`document.querySelector('[data-roi-coordinates]').textContent`);
 await physical('button[aria-label="구조 Anchor 도구 열기"]');await uploadJSON('구조 Anchor JSON 불러오기',out+'/test_only_page4_default/TEST_ONLY_anchor_fixture.json');
 if(await count()!==6)throw Error('Fixture import failed');let value=await runLines('DEFERRED');if(value.fit_ids.length||value.matrix!==null)throw Error('Imported review bypassed current confirmation');report.checks.imported_test_fixture_is_not_user_approval=true;
 const pairs=JSON.parse(fs.readFileSync(out+'/test_only_page4_default/TEST_ONLY_anchor_fixture.json','utf8')).pairs;
 for(const p of pairs){await selectPair(p.id);await checkReview();}
 value=await runLines('PARTIAL');if(value.rows.length!==6||value.approval_eligible||value.validation_used_for_estimation||value.mapping!==null||value.roi_mapping_confirmed)throw Error('Default scope gate missing');
 if(value.rows.some(r=>r.normal_max_ref_px>1e-8||r.angle_degrees>1e-6||r.common_window_support_min<.99))throw Error('Fixture line measurements changed');report.checks.real_page4_default_partial_despite_small_fit_and_validation_residuals=true;
 await js(`document.querySelector('.structural-verification-result').scrollIntoView({block:'start'});window.scrollBy(0,-80)`);await pause(100);await shot(out+'/test_only_partial.png');
 const first=value.run_id;await physical('.structural-verification > details > summary');await fill('구간 검증 minimum_axis_span_fraction','0.1');await until(`document.querySelector('[data-line-alignment-status]')===null`);await invalidated(first);report.checks.settings_change_revokes_old_result=true;
 value=await runLines('VERIFIED_SAMPLED');if(value.execution_context!=='TEST_ONLY'||!value.approval_eligible||value.mapping!==null||value.roi_mapping_confirmed)throw Error('Test provenance or final ROI gate missing');
 report.checks.configurable_distribution_demo_is_sampled_test_only=true;
 await until(`[...document.querySelectorAll('.structural-verification-preview img')].every(i=>i.complete&&i.naturalWidth===2382)`);
 const roiPixels=await js(`JSON.stringify([...document.querySelectorAll('.structural-verification-preview polygon')].map(p=>p.getAttribute('points')))`);
 await physical('.structural-verification-preview button[aria-label="Target 확대"]');await js(`const v=document.querySelector('.structural-verification-preview .viewport');v.focus();v.dispatchEvent(new KeyboardEvent('keydown',{key:'ArrowRight',bubbles:true}));`);await pause(100);
 same(await js(`JSON.stringify([...document.querySelectorAll('.structural-verification-preview polygon')].map(p=>p.getAttribute('points')))`),roiPixels,'Preview altered ROI');
 const error=await js(`(()=>{const v=document.querySelector('.structural-verification-preview .viewport'),r=v.querySelector('img').getBoundingClientRect(),p=v.querySelector('polygon'),q=p.points.getItem(0),s=new DOMPoint(q.x,q.y).matrixTransform(p.getScreenCTM());return Math.max(Math.abs(s.x-r.x-q.x*(+v.dataset.scale)),Math.abs(s.y-r.y-q.y*(+v.dataset.scale)));})()`);
 if(error>.01)throw Error('Overlay coordinate drift');report.maximum_overlay_css_error=error;report.checks.zoom_pan_preserves_native_roi_and_transformed_preview=true;
 await js(`document.querySelector('.structural-verification-preview').scrollIntoView({block:'start'})`);await pause(100);await shot(out+'/test_only_overlay.png');
 await fill('구간 정렬 승인 근거','TEST ONLY sampled-line review, not actual user approval');await physical('input[aria-label="구간 정렬 표본 범위 확인"]');await button('구간 정렬 결과 승인');await until(`document.querySelector('[data-line-review]')?.textContent==='TEST_APPROVED_SAMPLED'`);
 const approved=(await current()).run_id;report.checks.explicit_test_approval_cannot_confirm_roi_or_physical_endpoints=true;
 await selectPair(pairs[3].id);await setSelect('구조 Anchor 용도','EXCLUDE');await until(`document.querySelector('[data-line-alignment-status]')===null`);await invalidated(approved);report.checks.exclusion_invalidates_server_result_and_approval=true;
 value=await runLines('PARTIAL');if(value.validation_ids.length!==2)throw Error('Excluded interval used');report.checks.sparse_independent_validation_is_partial=true;
 await setSelect('구조 Anchor 용도','VALIDATION');await checkReview();
 // Deliberately wrong manual interval, TEST ONLY. Refit must not absorb this holdout.
 await button('구조 Anchor Ref 선택');await nativePoint('Ref',570.32,440);await nativePoint('Ref',570.32,480);await checkReview();
 value=await runLines('FAILED');if(value.matrix!==null||value.provisional_mapping!==null||value.approval_eligible||value.rows.filter(r=>r.usage==='FIT').some(r=>r.normal_max_ref_px>1e-8))throw Error('Bad holdout accepted or contaminated fit');
 report.checks.bad_validation_fails_with_small_fit_residual_and_no_mapping=true;
 await button('구조 Anchor Ref 선택');await nativePoint('Ref',550.32,440);await nativePoint('Ref',550.32,480);await checkReview();await runLines('VERIFIED_SAMPLED');
 await fill('구조 Anchor Parent ID',' LEFT_BED_WALL ');await checkReview();value=await runLines('DEFERRED');if(!value.parent_conflicts.length||value.matrix!==null)throw Error('Shared Parent used independently');report.checks.shared_parent_is_deferred=true;
 await fill('구조 Anchor Parent ID','shaft_outline');await checkReview();await selectPair(pairs[2].id);await setSelect('구조 Anchor 용도','EXCLUDE');value=await runLines('DEFERRED');if(value.matrix!==null)throw Error('Parallel FIT sufficient');report.checks.parallel_fit_and_missing_parent_count_are_deferred=true;
 await setSelect('구조 Anchor 용도','FIT');await checkReview();value=await runLines('VERIFIED_SAMPLED');
 await cdp('Browser.setDownloadBehavior',{behavior:'allow',downloadPath:downloads});await button('구간 정렬 JSON 저장');
 for(let i=0;i<100&&!fs.readdirSync(downloads).some(p=>p.startsWith('structural_alignment_'));i++)await pause(50);
 const saved=fs.readdirSync(downloads).find(p=>p.startsWith('structural_alignment_'));if(!saved)throw Error('Result download failed');const exported=JSON.parse(fs.readFileSync(downloads+'/'+saved,'utf8'));if(exported.mapping!==null||exported.source_anchor_file.pairs.length!==6||exported.validation_used_for_estimation)throw Error('Export contract incorrect');fs.writeFileSync(out+'/TEST_ONLY_browser_verification.json',JSON.stringify(exported,null,2));report.checks.result_download_preserves_input_constraints_and_unconfirmed_mapping=true;
 // Hold a run across an Anchor edit; no late result/approval may return.
 await cdp('Fetch.enable',{patterns:[{urlPattern:'*/api/structural-alignments',requestStage:'Request'}]});await button('구조 구간 정렬 실행');for(let i=0;i<100&&!paused.length;i++)await pause(30);if(!paused.length)throw Error('Run not intercepted');
 await setSelect('구조 Anchor 용도','EXCLUDE');for(const requestId of paused)await cdp('Fetch.continueRequest',{requestId}).catch(()=>undefined);await cdp('Fetch.disable');await pause(300);if(await status())throw Error('Late result restored');report.checks.late_run_cannot_restore_edited_anchor_verification=true;
 same(await js(`document.querySelector('[data-roi-coordinates]').textContent`),originalROI,'Anchor alignment mutated original ROI');if(await js(`document.querySelector('[aria-label="선택한 두 도면"] [data-role=Ref] svg').children.length`))throw Error('Original Ref marked');report.checks.original_roi_and_ref_untouched=true;
 await setSelect('구조 Anchor 용도','FIT');await checkReview();value=await runLines('VERIFIED_SAMPLED');const last=value.run_id;
 await select('Target 페이지',5);await extract();await invalidated(last);report.checks.page_change_revokes_old_line_result=true;
 if(alignmentRequests!==0||runtimeErrors.length)throw Error('Legacy point path called or runtime exception '+JSON.stringify(runtimeErrors));report.checks.no_legacy_point_alignment_or_browser_exceptions=true;
 fs.writeFileSync(out+'/browser.json',JSON.stringify(report,null,2));console.log(JSON.stringify(report));
}finally{await click('Ref PDF 제거').catch(()=>{});await click('Target PDF 제거').catch(()=>{});socket.close();}
