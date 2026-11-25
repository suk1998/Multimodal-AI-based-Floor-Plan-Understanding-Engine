// Real browser regression. Requires API 8010, Vite 5173 and test Chrome CDP 9223.
import fs from "node:fs";
import {fileURLToPath} from "node:url";
const root=fileURLToPath(new URL("../../",import.meta.url)).replace(/\/$/,"");
const page=await(await fetch("http://127.0.0.1:9223/json/new?http://127.0.0.1:5174",{method:"PUT"})).json();
const socket=new WebSocket(page.webSocketDebuggerUrl);await new Promise((r,j)=>{socket.onopen=r;socket.onerror=j;});
let sequence=0;const pending=new Map(),paused=[],runtimeErrors=[];
socket.onmessage=({data})=>{
  const m=JSON.parse(data);
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
// Isolated API 8011 / preview 5174. Any approval here is TEST ONLY.
const report={status:'passed',isolated_test_only:true,checks:{}};
const source=fs.readFileSync(root+'/alignment_inputs/page4_user_roi.json','utf8'),expectedROI=JSON.parse(source);
async function uploadROI(){const d=await cdp('DOM.getDocument');const n=await cdp('DOM.querySelector',{nodeId:d.root.nodeId,selector:'input[aria-label="Target ROI 불러오기"]'});await cdp('DOM.setFileInputFiles',{nodeId:n.nodeId,files:[root+'/alignment_inputs/page4_user_roi.json']});await pause(150);}
async function current(){const id=await js(`document.querySelector('.alignment-result img')?.getAttribute('src')?.split('/alignments/')[1]?.split('/')[0]`);if(!id)throw Error('No run ID');return (await fetch('http://127.0.0.1:8011/api/alignments/'+id)).json();}
const same=(a,b,label)=>{if(JSON.stringify(a)!==JSON.stringify(b))throw Error(label);};
async function tempView(side){return js(`(()=>{const panel=document.querySelector('[aria-label="임시 대응점 지정"] [data-role=${side}]'),v=panel.querySelector('.viewport'),r=v.getBoundingClientRect();return {left:r.left,top:r.top,scale:+v.dataset.scale,x:+v.dataset.offsetX,y:+v.dataset.offsetY};})()`);}
async function pick(side,point){await js(`document.querySelector('[aria-label="임시 대응점 지정"] [data-role=${side}] .viewport').scrollIntoView({block:'center'})`);await pause(80);const v=await tempView(side),x=v.left+v.x+point[0]*v.scale,y=v.top+v.y+point[1]*v.scale;await cdp('Input.dispatchMouseEvent',{type:'mousePressed',x,y,button:'left',clickCount:1});await cdp('Input.dispatchMouseEvent',{type:'mouseReleased',x,y,button:'left',clickCount:1});await pause(100);}
async function previewState(){return js(`(()=>{const v=document.querySelector('.alignment-result .viewport');return {scale:+v.dataset.scale,x:+v.dataset.offsetX,y:+v.dataset.offsetY,polygon:v.querySelector('polygon').getAttribute('points')};})()`);}
try{
 await cdp('Runtime.enable');await cdp('Page.enable');await until(`document.querySelectorAll('input[type=file]').length===2`);
 await upload(0,root+'/source_pdfs/자이사업승인도서.pdf');await until(`document.querySelector('select[aria-label="Ref 페이지"]')?.options.length===6`);
 await upload(1,root+'/source_pdfs/자이실시도서.pdf');await until(`document.querySelector('select[aria-label="Target 페이지"]')?.options.length===6`);
 await select('Ref 페이지',4);await select('Target 페이지',4);await extract();
 if(!await js(`document.querySelector('button[aria-label="정렬 도구 열기"]').disabled`))throw Error('Alignment without ROI allowed');
 await uploadROI();await until(`!document.querySelector('button[aria-label="정렬 도구 열기"]').disabled`);
 same(await js(`JSON.parse(document.querySelector('[data-roi-coordinates]').textContent)`),expectedROI.vertices,'User ROI changed');report.checks.real_pdfs_page4_actual_user_roi=true;
 await click('정렬 도구 열기');await click('자동 정렬 실행');await until(`document.querySelector('[data-alignment-status]')!==null`);
 const initial=await current();if(initial.status!=='PARTIAL'||initial.residuals.check_ref_px.p95>1||initial.review.state!=='NOT_APPROVED')throw Error('Unexpected actual Page4 result');
 if(!initial.matrix||initial.matrix[0][2]===0)throw Error('Measured nonzero transform lost');report.checks.auto_partial_independent_validation_not_identity_assumption=true;
 same(await js(`document.querySelector('[aria-label="선택한 두 도면"] [data-role=Ref] svg').children.length`),0,'Original Ref marked');if(await js(`document.querySelectorAll('a.download').length`)!==2)throw Error('Temporary previews duplicate original PNG download controls');report.checks.central_original_ref_unmarked=true;
 await until(`[...document.querySelectorAll('.alignment-result img')].every(i=>i.complete&&i.naturalWidth===2382)`);
 await js(`document.querySelector('.alignment-result .viewport').scrollIntoView({block:'center'})`);await pause(100);await shot(root+'/phase2b_validation/browser_overlay.png');
 const before=await previewState();await js(`document.querySelector('.alignment-result button[aria-label="Target 확대"]').click()`);await pause(100);const zoom=await previewState();near(zoom.scale,before.scale*1.25,'Preview zoom');same(zoom.polygon,before.polygon,'Zoom changed native ROI');
 await js(`(()=>{const v=document.querySelector('.alignment-result .viewport');v.focus();v.dispatchEvent(new KeyboardEvent('keydown',{key:'ArrowRight',bubbles:true}));})()`);await pause(100);near((await previewState()).x,zoom.x-40,'Preview pan');same((await previewState()).polygon,before.polygon,'Pan changed native ROI');
 const pixelError=await js(`(()=>{const v=document.querySelector('.alignment-result .viewport'),img=v.querySelector('img').getBoundingClientRect(),p=v.querySelector('polygon'),first=p.points.getItem(0),q=new DOMPoint(first.x,first.y).matrixTransform(p.getScreenCTM());return [Math.abs(q.x-img.x-first.x*(+v.dataset.scale)),Math.abs(q.y-img.y-first.y*(+v.dataset.scale))];})()`);if(pixelError.some(x=>x>.01))throw Error('Preview image/ROI drift');report.maximum_overlay_css_error=Math.max(...pixelError);report.checks.preview_zoom_pan_preserves_target_native_geometry=true;
 // Manually select an independent check point and deliberately put it on a wrong location.
 const check=initial.anchors.find(a=>a.role==='check');await js(`[...document.querySelectorAll('.anchor-table tbody tr')].find(r=>r.innerText.includes(${JSON.stringify(check.id)})).querySelector('input[type=radio]').click()`);
 await pick('Target',[check.target[0]+40,check.target[1]]);await until(`document.querySelector('[data-alignment-status]')===null`);report.checks.manual_edit_invalidates_previous_result=true;
 await click('수동 정렬 실행');await until(`document.querySelector('[data-alignment-status]')?.textContent==='FAILED'`);const failed=await current();if(failed.matrix!==null||failed.mapping!==null)throw Error('Failed verification returned mapping');if(!await js(`document.querySelector('button[aria-label="정렬 결과 승인"]').disabled`))throw Error('Failed alignment can approve');report.checks.bad_holdout_fails_without_identity_fallback=true;
 await pick('Target',check.target);await click('수동 정렬 실행');await until(`document.querySelector('[data-alignment-status]')?.textContent==='PARTIAL'`);report.checks.manual_point_correction_and_retry=true;
 await js(`document.querySelector('input[aria-label="정렬 검토 근거"]').focus()`);await cdp('Input.insertText',{text:'TEST ONLY isolated browser review; not an actual user approval.'});
 await js(`document.querySelector('input[aria-label="정렬 안정 구조 최종 확인"]').click()`);await until(`!document.querySelector('button[aria-label="정렬 결과 승인"]').disabled`);await click('정렬 결과 승인');await until(`document.querySelector('[data-alignment-review]')?.textContent==='APPROVED_PARTIAL'`);report.checks.explicit_test_approval_retains_partial_scope=true;
 // Original ROI changes invalidate the transform and approval together.
 await click('Target ROI 초기화');await until(`document.querySelector('[data-alignment-status]')===null`);report.checks.roi_reset_invalidates_mapping_and_approval=true;
 await uploadROI();await until(`document.querySelector('button[aria-label="자동 정렬 실행"]')!==null`);
 await cdp('Fetch.enable',{patterns:[{urlPattern:'*/api/alignments',requestStage:'Request'}]});await click('자동 정렬 실행');for(let i=0;i<100&&!paused.length;i++)await pause(30);if(!paused.length)throw Error('Alignment request not held');
 await click('Target ROI 초기화');for(const requestId of paused)await cdp('Fetch.continueRequest',{requestId}).catch(()=>undefined);await cdp('Fetch.disable');await pause(200);if(await js(`document.querySelector('[data-alignment-status]')!==null`))throw Error('Old alignment restored after ROI reset');report.checks.late_alignment_cannot_restore_invalid_result=true;
 await uploadROI();await click('자동 정렬 실행');await until(`document.querySelector('[data-alignment-status]')?.textContent==='PARTIAL'`);
 await select('Target 페이지',5);await until(`document.querySelector('.alignment-result')===null`);report.checks.page_change_invalidates_alignment=true;
 if(runtimeErrors.length)throw Error(JSON.stringify(runtimeErrors));report.checks.no_runtime_exceptions=true;report.measured_initial={matrix:initial.matrix,residuals:initial.residuals,mapping:initial.mapping};
 fs.writeFileSync(root+'/phase2b_validation/browser.json',JSON.stringify(report,null,2));console.log(JSON.stringify(report));
}finally{socket.close();}
