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
try{
  await cdp('Runtime.enable');await cdp('Page.enable');
  await until(`document.querySelectorAll('input[type=file]').length===2`);
  await upload(0,root+'/source_pdfs/자이사업승인도서.pdf');
  await until(`document.querySelector('select[aria-label="Ref 페이지"]')?.options.length===6`);
  await upload(1,root+'/source_pdfs/자이실시도서.pdf');
  await until(`document.querySelector('select[aria-label="Target 페이지"]')?.options.length===6`);
  await select('Ref 페이지',4);await select('Target 페이지',5);await extract();
  const report={status:'passed',ref_page:4,target_page:5,checks:{},initial:[await view(0),await view(1)]};
  if(await js(`document.querySelectorAll('img').length`)!==2)throw Error('Duplicate drawings');
  if(report.initial.some(v=>v.imageWidth!==2382||v.imageHeight!==1684||v.overlay!=='0 0 2382 1684'))throw Error('Native coordinate metadata mismatch');
  report.checks.real_upload_independent_pages_and_single_display=true;
  await shot('/tmp/floorplan_react_fit.png');
  await click('Ref 확대');near((await view(0)).scale,report.initial[0].scale*1.25,'zoom in');near((await view(1)).scale,report.initial[1].scale,'independent Target');
  await click('Ref 축소');near((await view(0)).scale,report.initial[0].scale,'zoom out');report.checks.zoom_buttons_independent=true;
  const before=await view(0),point={x:before.width*.63,y:before.height*.42};
  await js(inside(0,`panel.querySelector('.viewport').addEventListener('wheel',e=>{const r=e.currentTarget.getBoundingClientRect();window.__wheelPoint={x:e.clientX-r.left,y:e.clientY-r.top};},{once:true,capture:true});`));
  await cdp('Input.dispatchMouseEvent',{type:'mouseWheel',x:before.left+point.x,y:before.top+point.y,deltaX:0,deltaY:-160});await pause(150);
  const delivered=await js('window.__wheelPoint'),wheel=await view(0);
  if(wheel.scale<=before.scale)throw Error('Wheel zoom failed');
  near((delivered.x-before.x)/before.scale,(delivered.x-wheel.x)/wheel.scale,'cursor native x');near((delivered.y-before.y)/before.scale,(delivered.y-wheel.y)/wheel.scale,'cursor native y');
  report.checks.wheel_native_anchor=true;
  const drag=await view(0),x=drag.left+drag.width/2,y=drag.top+drag.height*.55;
  await cdp('Input.dispatchMouseEvent',{type:'mouseMoved',x,y});await cdp('Input.dispatchMouseEvent',{type:'mousePressed',x,y,button:'left',clickCount:1});
  await cdp('Input.dispatchMouseEvent',{type:'mouseMoved',x:x+80,y:y+45,buttons:1,button:'left'});await cdp('Input.dispatchMouseEvent',{type:'mouseReleased',x:x+80,y:y+45,button:'left',clickCount:1});await pause(100);
  const moved=await view(0);near(moved.x,drag.x+80,'pan x');near(moved.y,drag.y+45,'pan y');report.checks.drag_pan=true;
  await shot('/tmp/floorplan_react_zoom_pan.png');
  await cdp('Input.dispatchKeyEvent',{type:'keyDown',key:'ArrowRight',code:'ArrowRight',windowsVirtualKeyCode:39});await cdp('Input.dispatchKeyEvent',{type:'keyUp',key:'ArrowRight',code:'ArrowRight',windowsVirtualKeyCode:39});await pause(100);near((await view(0)).x,moved.x-40,'keyboard');report.checks.keyboard_pan=true;
  await click('Ref 100%');near((await view(0)).scale,1,'100 percent');
  const old=await view(0),center={x:(old.width/2-old.x)/old.scale,y:(old.height/2-old.y)/old.scale};
  await cdp('Emulation.setDeviceMetricsOverride',{width:1550,height:1180,deviceScaleFactor:2,mobile:false});await pause(150);
  const resized=await view(0);near(resized.scale,old.scale,'resize scale');near((resized.width/2-resized.x)/resized.scale,center.x,'resize x');near((resized.height/2-resized.y)/resized.scale,center.y,'resize y');
  report.checks.resize_high_dpi_native_coordinates=true;await click('Ref 화면 맞춤');const fit=await view(0);near(fit.x,(fit.width-fit.imageWidth*fit.scale)/2,'fit x');near(fit.y,(fit.height-fit.imageHeight*fit.scale)/2,'fit y');report.checks.fit_and_actual_size=true;
  await cdp('Emulation.clearDeviceMetricsOverride');await pause(150);
  const touch=await view(0),cx=Math.round(touch.left+touch.width/2),cy=Math.round(touch.top+touch.height/2);
  await cdp('Emulation.setTouchEmulationEnabled',{enabled:true,maxTouchPoints:2});
  await cdp('Input.dispatchTouchEvent',{type:'touchStart',touchPoints:[{x:cx-60,y:cy,id:1},{x:cx+60,y:cy,id:2}]});await cdp('Input.dispatchTouchEvent',{type:'touchMove',touchPoints:[{x:cx-90,y:cy,id:1},{x:cx+90,y:cy,id:2}]});await cdp('Input.dispatchTouchEvent',{type:'touchEnd',touchPoints:[]});await pause(150);
  near((await view(0)).scale,touch.scale*1.5,'pinch');await cdp('Emulation.setTouchEmulationEnabled',{enabled:false});report.checks.emulated_touch_pinch=true;
  const downloads=fs.mkdtempSync('/tmp/floorplan-react-downloads-');await cdp('Browser.setDownloadBehavior',{behavior:'allow',downloadPath:downloads});await js(`document.querySelectorAll('a.download').forEach(a=>a.click())`);
  for(let i=0;i<100;i++){if(fs.readdirSync(downloads).filter(p=>p.endsWith('.png')).length===2)break;await pause(100);}
  report.downloads=fs.readdirSync(downloads).filter(p=>p.endsWith('.png')).map(p=>`${downloads}/${p}`);if(report.downloads.length!==2)throw Error('PNG download failed');report.checks.downloads_after_navigation=true;
  // Hold old image requests, change selection, and then release them: no stale pair.
  await select('Target 페이지',4);
  await cdp('Fetch.enable',{patterns:[{urlPattern:'*/api/documents/*/pages/*/image*',requestStage:'Request'}]});
  await js(`document.querySelector('button.primary').click()`);
  for(let i=0;i<100&&paused.length<2;i++)await pause(50);
  if(paused.length<2)throw Error('Requests not intercepted');
  await select('Target 페이지',6);
  for(const requestId of paused)await cdp('Fetch.continueRequest',{requestId}).catch(()=>undefined);
  await cdp('Fetch.disable');await pause(200);
  if(await js(`document.querySelectorAll('.drawing-panel').length`)!==0)throw Error('Stale extraction restored old pair');
  await extract();if(!(await view(1)).imageId.includes(':6:'))throw Error('New page incorrect');report.checks.stale_response_cancel_and_page_reset=true;
  await select('이미지 추출 배율',1);await extract();if((await view(0)).imageWidth!==1191)throw Error('Extraction resolution failed');report.checks.extraction_resolution_change=true;
  fs.writeFileSync('/tmp/floorplan-react-invalid.pdf','not a PDF');await upload(0,'/tmp/floorplan-react-invalid.pdf');
  await until(`document.querySelector('.input-section [role=alert]')!==null`);
  if(await js(`document.querySelectorAll('.drawing-panel').length`)!==0)throw Error('Invalid PDF left previous pair');
  if(!(await js(`document.querySelector('button.primary').disabled`)))throw Error('Invalid PDF enables extraction');report.checks.invalid_upload_clears_pair=true;
  report.screenshots=['/tmp/floorplan_react_fit.png','/tmp/floorplan_react_zoom_pan.png'];
  if(runtimeErrors.length)throw Error(JSON.stringify(runtimeErrors));
  report.checks.no_browser_runtime_exceptions=true;
  fs.writeFileSync('/tmp/floorplan_react_browser_result.json',JSON.stringify(report,null,2));console.log(JSON.stringify(report));
} finally {socket.close();}
