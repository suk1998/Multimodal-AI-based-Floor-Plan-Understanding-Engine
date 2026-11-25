// Real browser regression. Requires API 8010, Vite 5173 and test Chrome CDP 9223.
import fs from "node:fs";
import {fileURLToPath} from "node:url";
const root=fileURLToPath(new URL("../../",import.meta.url)).replace(/\/$/,"");
const page=await(await fetch("http://127.0.0.1:9223/json/new?http://127.0.0.1:5174",{method:"PUT"})).json();
const socket=new WebSocket(page.webSocketDebuggerUrl);await new Promise((r,j)=>{socket.onopen=r;socket.onerror=j;});
let alignmentRequests=0,allRunRequests=0;
let sequence=0;const pending=new Map(),paused=[],runtimeErrors=[];
socket.onmessage=({data})=>{
  const m=JSON.parse(data);
  if(m.method==="Network.requestWillBeSent"&&m.params.request.url.includes("/api/alignments"))alignmentRequests++;
  if(m.method==="Network.requestWillBeSent"&&m.params.request.method==="POST"&&/\/api\/(structural-)?alignments$/.test(m.params.request.url))allRunRequests++;
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
const out=root+'/ui_simplification_validation',downloads=fs.mkdtempSync('/tmp/floorplan-structural-anchors-');fs.mkdirSync(out,{recursive:true});
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
const visibleText=()=>js('document.body.innerText');
async function basic(){
 if(await js(`document.querySelector('.developer-tools')?.open===true`))throw Error('Developer tools open by default');
 const text=await visibleText();for(const word of ['Parent','FIT','VALIDATION','HOLD','EXCLUDE','법선','JSON','원본 좌표','Structural Anchor'])if(text.includes(word))throw Error('Technical UI leaked: '+word);
 if(await js(`!document.querySelector('button[aria-label="자동 정렬 준비 중"]').disabled`))throw Error('Unfinished automatic alignment enabled');
}
async function openAdvanced(){await physical('.developer-tools > summary');await until(`document.querySelector('.developer-tools').open`);}
async function closeAdvanced(){await physical('.developer-tools > summary');await until(`!document.querySelector('.developer-tools').open`);}
async function buttonAnywhere(label){await physical(`button[aria-label="${label}"]`);}
async function targetPoint(x,y){
 await js(`document.querySelector('[aria-label="선택한 두 도면"] [data-role=Target] .viewport').scrollIntoView({block:'center'})`);await pause(80);
 const v=await view(1);await mouse('mousePressed',v.left+v.x+x*v.scale,v.top+v.y+y*v.scale,{button:'left',clickCount:1});await mouse('mouseReleased',v.left+v.x+x*v.scale,v.top+v.y+y*v.scale,{button:'left',clickCount:1});await pause(80);
}
const roiPoints=()=>js(`JSON.parse(document.querySelector('[data-roi-coordinates]').textContent)`);
async function current(){const id=await js(`document.querySelector('[data-line-run-id]').dataset.lineRunId`);const r=await fetch('http://127.0.0.1:8011/api/structural-alignments/'+id);if(!r.ok)throw Error('Result not available');return r.json();}
try{
 await cdp('Runtime.enable');await cdp('Page.enable');await cdp('Network.enable');await until(`document.querySelectorAll('input[type=file]').length===2`);await basic();
 if(await js(`document.querySelectorAll('.comparison-workflow li').length`)!==4)throw Error('Workflow stages missing');report.checks.initial_simple_workflow_and_honest_unavailable_state=true;
 await upload(0,root+'/source_pdfs/자이사업승인도서.pdf');await until(`document.querySelector('select[aria-label="Ref 페이지"]')?.options.length===6`);
 await upload(1,root+'/source_pdfs/자이실시도서.pdf');await until(`document.querySelector('select[aria-label="Target 페이지"]')?.options.length===6`);
 await select('Ref 페이지',4);await select('Target 페이지',4);await extract();await basic();
 if(await js(`document.querySelectorAll('.drawing-panel img').length`)!==2)throw Error('Duplicate initial images');
 if(await js(`document.querySelector('.developer-tools').open`))throw Error('Advanced section not folded');report.checks.real_pdfs_page4_single_ref_target_and_folded_developer_tools=true;
 if((await roiPoints()).length)throw Error('Default ROI invented');
 await buttonAnywhere('Target ROI 그리기');for(const p of [[620,480],[1510,430],[1580,1150],[700,1200]])await targetPoint(...p);await buttonAnywhere('Target ROI 완료');
 const points=await roiPoints();if(points.length!==4)throw Error('ROI drawing failed');await until(`document.querySelector('[data-comparison-step="2"]').dataset.complete==='true'`);await basic();report.checks.general_roi_draw_and_stage_updates=true;
 await buttonAnywhere('Target 확대');await js(`const v=document.querySelector('[aria-label="선택한 두 도면"] [data-role=Target] .viewport');v.focus();v.dispatchEvent(new KeyboardEvent('keydown',{key:'ArrowRight',bubbles:true}));`);await pause(100);same(await roiPoints(),points,'Zoom/Pan changed native ROI');await buttonAnywhere('Target 화면 맞춤');report.checks.zoom_pan_and_fit_preserve_roi=true;
 await cdp('Browser.setDownloadBehavior',{behavior:'allow',downloadPath:downloads});await buttonAnywhere('Target ROI 저장');
 for(let i=0;i<100&&!fs.readdirSync(downloads).some(p=>p.endsWith('.json'));i++)await pause(50);
 const name=fs.readdirSync(downloads).find(p=>p.endsWith('.json'));if(!name)throw Error('Plain-label region save missing');const saved=JSON.parse(fs.readFileSync(downloads+'/'+name,'utf8'));same(saved.vertices,points,'Changed ROI file contract');fs.writeFileSync(out+'/test_only_roi.json',JSON.stringify(saved,null,2));
 await buttonAnywhere('Target ROI 초기화');await uploadJSON('Target ROI 불러오기',downloads+'/'+name);same(await roiPoints(),points,'ROI reload changed coordinates');await basic();report.checks.plain_region_save_load_preserves_json_contract=true;
 await js(`document.querySelectorAll('[aria-label="선택한 두 도면"] a.download').forEach(a=>a.click())`);
 for(let i=0;i<100&&fs.readdirSync(downloads).filter(p=>p.endsWith('.png')).length<2;i++)await pause(50);
 const pngs=fs.readdirSync(downloads).filter(p=>p.endsWith('.png'));if(pngs.length!==2)throw Error('PNG downloads unavailable');for(const p of pngs){const b=fs.readFileSync(downloads+'/'+p);if(b.readUInt32BE(16)!==2382||b.readUInt32BE(20)!==1684)throw Error('Native PNG dimensions changed');}report.checks.both_native_png_downloads_preserved=true;
 await js(`document.querySelector('button[aria-label="자동 정렬 준비 중"]').click()`);await pause(100);if(allRunRequests!==0)throw Error('General view ran incomplete automatic function');report.checks.no_fake_automatic_run_or_success=true;
 await js('window.scrollTo(0,0)');await pause(100);await shot(out+'/basic_comparison.png');
 await js(`document.querySelector('.roi-tools').scrollIntoView({block:'center'})`);await pause(100);await shot(out+'/basic_roi.png');
 // Existing developer functionality is explicitly entered, never promoted to general results.
 await openAdvanced();await physical('button[aria-label="구조 Anchor 도구 열기"]');await uploadJSON('구조 Anchor JSON 불러오기',root+'/phase2b3_validation/test_only_page4_default/TEST_ONLY_anchor_fixture.json');
 const fixture=JSON.parse(fs.readFileSync(root+'/phase2b3_validation/test_only_page4_default/TEST_ONLY_anchor_fixture.json','utf8'));
 if(await count()!==6)throw Error('Developer Anchor import lost');for(const p of fixture.pairs){await selectPair(p.id);await checkReview();}
 await physical('.structural-verification > details > summary');await fill('구간 검증 minimum_axis_span_fraction','0.1');await until(`!document.querySelector('button[aria-label="구조 구간 정렬 실행"]').disabled`);await button('구조 구간 정렬 실행');await until(`document.querySelector('[data-line-alignment-status]')?.dataset.lineAlignmentStatus==='VERIFIED_SAMPLED'`);
 await fill('구간 정렬 승인 근거','TEST ONLY preserved developer review; not an actual user approval');await physical('input[aria-label="구간 정렬 표본 범위 확인"]');await button('구간 정렬 결과 승인');await until(`document.querySelector('[data-line-review]')?.textContent==='TEST_APPROVED_SAMPLED'`);const approved=await current();
 if(approved.mapping!==null||approved.roi_mapping_confirmed)throw Error('Developer review promoted final mapping');report.checks.existing_manual_anchor_line_verification_and_test_approval_preserved=true;
 const before=await selected();await closeAdvanced();await basic();if(await js(`document.querySelectorAll('[data-role] img').length`)<4)throw Error('Folding unmounted developer inputs');
 const shown=await js(`[...document.querySelectorAll('.drawing-panel img')].filter(e=>e.checkVisibility()).length`);if(shown!==2)throw Error('Hidden developer drawings leaked');
 if(await js(`document.querySelector('[data-comparison-step="4"]').dataset.complete`)!=='false')throw Error('Developer result promoted to general workflow');report.checks.fold_keeps_state_and_general_view_hides_technical_results=true;
 await openAdvanced();same(await selected(),before,'Fold/reopen lost Anchor selection or data');await closeAdvanced();
 // Editing ROI in normal view must revoke an approval even while its panel is hidden.
 await buttonAnywhere('Target ROI 초기화');await until(`document.querySelector('[data-comparison-step="2"]').dataset.complete==='false'`);
 let stale;for(let i=0;i<100;i++){stale=await(await fetch('http://127.0.0.1:8011/api/structural-alignments/'+approved.run_id)).json();if(stale.validity==='INVALIDATED')break;await pause(50);}
 if(stale.validity!=='INVALIDATED'||stale.review.state!=='INVALIDATED'||stale.matrix!==null)throw Error('Folded developer approval survived ROI edit');await basic();report.checks.general_roi_change_revokes_hidden_developer_approval=true;
 await openAdvanced();if(await count()!==6)throw Error('ROI edit deleted Anchor geometry');if(!(await selected()).review.includes('INVALIDATED'))throw Error('Anchor review not invalidated');await closeAdvanced();
 await uploadJSON('Target ROI 불러오기',root+'/alignment_inputs/page4_user_roi.json');await basic();report.checks.existing_user_roi_loads_without_exposing_technical_controls=true;
 await cdp('Emulation.setDeviceMetricsOverride',{width:1440,height:900,deviceScaleFactor:2,mobile:false});await js('window.scrollTo(0,0)');await pause(150);await basic();await shot(out+'/basic_1440.png');await cdp('Emulation.clearDeviceMetricsOverride');
 await select('Target 페이지',5);await extract();await basic();await openAdvanced();if(await count()!==0)throw Error('New page reused prior Anchor geometry');await closeAdvanced();report.checks.page_change_defaults_to_simple_view_and_drops_old_context=true;
 if(runtimeErrors.length)throw Error(JSON.stringify(runtimeErrors));report.checks.no_browser_runtime_errors=true;
 fs.writeFileSync(out+'/browser.json',JSON.stringify(report,null,2));console.log(JSON.stringify(report));
}finally{await click('Ref PDF 제거').catch(()=>{});await click('Target PDF 제거').catch(()=>{});socket.close();}
