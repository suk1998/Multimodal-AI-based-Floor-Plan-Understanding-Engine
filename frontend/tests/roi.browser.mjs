// Real browser regression. Requires API 8010, Vite 5173 and test Chrome CDP 9223.
import fs from "node:fs";
import {fileURLToPath} from "node:url";
const root=fileURLToPath(new URL("../../",import.meta.url)).replace(/\/$/,"");
const page=await(await fetch("http://127.0.0.1:9223/json/new?http://127.0.0.1:5173",{method:"PUT"})).json();
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
// Explicit automated UI test inputs only. No production ROI/default is created.
async function uploadROI(path){const d=await cdp('DOM.getDocument');const n=await cdp('DOM.querySelector',{nodeId:d.root.nodeId,selector:'input[aria-label="Target ROI 불러오기"]'});await cdp('DOM.setFileInputFiles',{nodeId:n.nodeId,files:[path]});await pause(180);}
async function points(){return js(`JSON.parse(document.querySelector('[data-roi-coordinates]').textContent)`);}
async function mouse(type,x,y,more={}){await cdp('Input.dispatchMouseEvent',{type,x,y,...more});}
async function nativeClick(x,y){const v=await view(1);await mouse('mousePressed',v.left+v.x+x*v.scale,v.top+v.y+y*v.scale,{button:'left',clickCount:1});await mouse('mouseReleased',v.left+v.x+x*v.scale,v.top+v.y+y*v.scale,{button:'left',clickCount:1});await pause(80);}
async function dragNative(index,dx,dy){const p=(await points())[index],v=await view(1),x=v.left+v.x+p[0]*v.scale,y=v.top+v.y+p[1]*v.scale;
  await mouse('mousePressed',x,y,{button:'left',clickCount:1});await mouse('mouseMoved',x+dx,y+dy,{button:'left',buttons:1});await mouse('mouseReleased',x+dx,y+dy,{button:'left',clickCount:1});await pause(120);}
// DOMRect/CSS matrix serialization rounds subpixels; native JSON comparisons remain exact.
async function aligned(){const delta=await js(inside(1,`const img=panel.querySelector('img').getBoundingClientRect(),v=panel.querySelector('.viewport');return [...panel.querySelectorAll('[data-vertex]')].map(c=>{const r=c.getBoundingClientRect();return [Math.abs(r.x+r.width/2-img.x-(+c.getAttribute('cx'))*(+v.dataset.scale)),Math.abs(r.y+r.height/2-img.y-(+c.getAttribute('cy'))*(+v.dataset.scale))];});`));report.maximum_overlay_error_css_px=Math.max(report.maximum_overlay_error_css_px||0,...delta.flat());if(delta.some(p=>p.some(n=>n>.01)))throw Error('Overlay/native image mapping mismatch: '+JSON.stringify(delta));}
const same=(a,b,label)=>{if(JSON.stringify(a)!==JSON.stringify(b))throw Error(label);};
const out=root+'/phase2a_validation',temporary=fs.mkdtempSync('/tmp/floorplan-roi-browser-');fs.mkdirSync(out,{recursive:true});
const report={status:'passed',test_only_roi:true,target_page:4,checks:{}};
try{
  await cdp('Runtime.enable');await cdp('Page.enable');
  await until(`document.querySelectorAll('input[type=file]').length===2`);
  await upload(0,root+'/source_pdfs/자이사업승인도서.pdf');await until(`document.querySelector('select[aria-label="Ref 페이지"]')?.options.length===6`);
  await upload(1,root+'/source_pdfs/자이실시도서.pdf');await until(`document.querySelector('select[aria-label="Target 페이지"]')?.options.length===6`);
  await select('Ref 페이지',4);await select('Target 페이지',4);await extract();
  if((await points()).length)throw Error('Automatic default ROI');
  if(await js(`document.querySelector('[data-role=Ref] svg').children.length`))throw Error('Ref annotated');
  report.checks.no_default_roi_and_ref_untouched=true;
  await click('Target ROI 그리기');
  // Four real pointer clicks on the actual Target page, independent of historical ROI.
  for(const [x,y] of [[620,480],[1510,430],[1580,1150],[700,1200]])await nativeClick(x,y);
  await click('Target ROI 완료');await until(`document.querySelector('.roi-overlay polygon')!==null`);
  const drawn=await points();if(drawn.length!==4)throw Error('Polygon drawing failed');await aligned();
  await shot(out+'/roi_drawn.png');report.checks.draw_on_real_target_page4=true;
  await dragNative(0,35,22);const edited=await points();if(edited[0][0]===drawn[0][0])throw Error('Vertex edit failed');await aligned();
  await click('Target ROI 선택 취소');if(await js(`document.querySelector('.roi-overlay circle.selected')!==null`))throw Error('Deselect failed');report.checks.vertex_drag_and_deselect=true;
  await click('Target ROI 그리기');await nativeClick(900,700);await click('Target ROI 마지막 점 취소');if((await points()).length)throw Error('Undo last point failed');
  await nativeClick(900,700);await click('Target ROI 그리기 취소');same(await points(),edited,'Cancel drawing did not restore existing ROI');report.checks.undo_and_cancel_restore_polygon=true;
  await click('Target ROI 수정');
  const before=await points(),v=await view(1),cx=v.left+v.width/2,cy=v.top+v.height/2;
  await mouse('mouseWheel',cx,cy,{deltaX:0,deltaY:-220});await pause(120);
  await mouse('mousePressed',cx,cy,{button:'left',clickCount:1,modifiers:8});await mouse('mouseMoved',cx+55,cy+35,{button:'left',buttons:1,modifiers:8});await mouse('mouseReleased',cx+55,cy+35,{button:'left',clickCount:1,modifiers:8});await pause(120);
  if((await view(1)).scale<=v.scale)throw Error('ROI mode wheel zoom failed');same(await points(),before,'Navigation changed vertices');await aligned();await shot(out+'/roi_zoom_pan.png');report.checks.zoom_shift_pan_preserves_native_coordinates=true;
  await click('Target 100%');near((await view(1)).scale,1,'Target 100%');await aligned();
  await cdp('Emulation.setDeviceMetricsOverride',{width:1550,height:1180,deviceScaleFactor:2,mobile:false});await pause(120);same(await points(),before,'DPR resized ROI');await aligned();
  await cdp('Emulation.clearDeviceMetricsOverride');await pause(120);await click('Target 화면 맞춤');await aligned();report.checks.fit_actual_resize_dpr2_preserve_overlay=true;
  const touch=await view(1),tx=Math.round(touch.left+touch.width/2),ty=Math.round(touch.top+touch.height/2);
  await cdp('Emulation.setTouchEmulationEnabled',{enabled:true,maxTouchPoints:2});
  await cdp('Input.dispatchTouchEvent',{type:'touchStart',touchPoints:[{x:tx-60,y:ty,id:1},{x:tx+60,y:ty,id:2}]});await cdp('Input.dispatchTouchEvent',{type:'touchMove',touchPoints:[{x:tx-90,y:ty,id:1},{x:tx+90,y:ty,id:2}]});await cdp('Input.dispatchTouchEvent',{type:'touchEnd',touchPoints:[]});await pause(120);
  await cdp('Emulation.setTouchEmulationEnabled',{enabled:false});same(await points(),before,'Pinch changed ROI');await aligned();report.checks.roi_edit_mode_touch_pinch=true;
  await cdp('Browser.setDownloadBehavior',{behavior:'allow',downloadPath:temporary});await click('Target ROI 저장');
  for(let i=0;i<100&&!fs.readdirSync(temporary).some(p=>p.endsWith('.json'));i++)await pause(50);
  const name=fs.readdirSync(temporary).find(p=>p.endsWith('.json'));if(!name)throw Error('ROI JSON download failed');const savedPath=temporary+'/'+name,saved=JSON.parse(fs.readFileSync(savedPath,'utf8'));
  same(saved.vertices,before,'Saved native coords changed');if(saved.role!=='Target'||saved.image.page!==4||saved.image.extraction_zoom!==2||saved.image.width!==2382||saved.image.height!==1684)throw Error('Missing image binding');
  fs.writeFileSync(out+'/test_only_roi.json',JSON.stringify(saved,null,2));
  await click('Target ROI 초기화');same(await points(),[],'Reset failed');await uploadROI(savedPath);same(await points(),before,'ROI reload changed vertices');await aligned();await shot(out+'/roi_reloaded.png');report.checks.json_save_reset_reload_exact_native_coordinates=true;
  // A pending old file read must not override a subsequent reset.
  await js(`window.__originalFileText=File.prototype.text;File.prototype.text=function(){return new Promise(resolve=>{window.__finishROIRead=()=>window.__originalFileText.call(this).then(resolve);});}`);
  await uploadROI(savedPath);await until(`typeof window.__finishROIRead==='function'`);await click('Target ROI 초기화');await js(`window.__finishROIRead();File.prototype.text=window.__originalFileText`);await pause(160);same(await points(),[],'Late file read restored reset ROI');report.checks.late_file_read_cannot_restore_reset_roi=true;
  await uploadROI(savedPath);same(await points(),before,'Repeated file load failed');
  for(const [key,value] of [['source_sha256','wrong-pdf'],['page',5],['extraction_zoom',1],['width',1191],['height',842],['image_id','wrong-image']]){
    const bad=structuredClone(saved);bad.image[key]=value;const path=temporary+'/bad-'+key+'.json';fs.writeFileSync(path,JSON.stringify(bad));await uploadROI(path);
    await until(`document.querySelector('.roi-tools [role=alert]')!==null`);same(await points(),before,'Mismatched load changed existing ROI');
  }
  report.checks.reject_mismatched_source_page_zoom_size_and_image_id=true;
  await select('Target 페이지',5);await extract();same(await points(),[],'Page change retained ROI');await uploadROI(savedPath);same(await points(),[],'Wrong page applied ROI');report.checks.page_change_invalidates_roi=true;
  await select('Target 페이지',4);await select('이미지 추출 배율',1);await extract();same(await points(),[],'Scale change retained ROI');await uploadROI(savedPath);same(await points(),[],'Wrong extraction zoom applied ROI');report.checks.extraction_zoom_change_invalidates_roi=true;
  await upload(1,root+'/source_pdfs/자이사업승인도서.pdf');await until(`document.querySelectorAll('.file-summary span')[1]?.textContent==='자이사업승인도서.pdf'`);await select('Target 페이지',4);await select('이미지 추출 배율',2);await extract();await uploadROI(savedPath);same(await points(),[],'Another PDF applied ROI');report.checks.pdf_replacement_invalidates_roi=true;
  await upload(1,root+'/source_pdfs/자이실시도서.pdf');await until(`document.querySelectorAll('.file-summary span')[1]?.textContent==='자이실시도서.pdf'`);await select('Target 페이지',4);await extract();await uploadROI(savedPath);same(await points(),before,'Same source re-upload cannot restore ROI');await aligned();report.checks.same_source_new_session_can_reload_roi=true;
  await js(`document.querySelectorAll('a.download').forEach(a=>a.click())`);
  for(let i=0;i<100&&fs.readdirSync(temporary).filter(p=>p.endsWith('.png')).length<2;i++)await pause(50);
  report.png_downloads=fs.readdirSync(temporary).filter(p=>p.endsWith('.png')).map(p=>temporary+'/'+p);if(report.png_downloads.length!==2)throw Error('PNG downloads lost');
  if(await js(`document.querySelectorAll('.drawing-panel img').length`)!==2||await js(`document.querySelector('[data-role=Ref] svg').children.length`)!==0)throw Error('Drawings duplicated or Ref marked');report.checks.single_drawings_ref_unmarked_png_downloads=true;
  if(runtimeErrors.length)throw Error(JSON.stringify(runtimeErrors));report.checks.no_browser_runtime_exceptions=true;
  report.vertices=before;fs.writeFileSync(out+'/roi_browser.json',JSON.stringify(report,null,2));console.log(JSON.stringify(report));
}finally{socket.close();}
