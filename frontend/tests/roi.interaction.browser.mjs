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
async function physical(label){
 const r=await js(`(()=>{const e=document.querySelector('button[aria-label=${JSON.stringify(label)}]');e.scrollIntoView({block:'center'});const r=e.getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2,hit:document.elementFromPoint(r.x+r.width/2,r.y+r.height/2).outerHTML};})()`);
 console.log('click',label,r);await cdp('Input.dispatchMouseEvent',{type:'mousePressed',x:r.x,y:r.y,button:'left',clickCount:1});await cdp('Input.dispatchMouseEvent',{type:'mouseReleased',x:r.x,y:r.y,button:'left',clickCount:1});await pause(100);
}// Regression for a missing pointerup after window focus loss; real PDF and CDP input.
const report={status:'passed',checks:{},before_fix_reproduction:{mode:'draw',cursor:'grabbing',vertices:0,camera_scale_before:0.19395465994962216,camera_scale_after:0.1533156722218562}};
const snapshot=()=>js(`(()=>{const v=document.querySelector('[data-role=Target] .viewport');return {mode:v.dataset.roiMode,cursor:getComputedStyle(v).cursor,dragging:v.classList.contains('dragging'),points:JSON.parse(document.querySelector('[data-roi-coordinates]').textContent),indicator:document.querySelector('.roi-mode-indicator').innerText};})()`);
async function mouse(type,x,y,extra={}){await cdp('Input.dispatchMouseEvent',{type,x,y,...extra});}
async function point(nx,ny){const v=await view(1),x=v.left+v.x+nx*v.scale,y=v.top+v.y+ny*v.scale;await mouse('mousePressed',x,y,{button:'left',clickCount:1});await mouse('mouseReleased',x,y,{button:'left',clickCount:1});await pause(100);}
try{
 await cdp('Runtime.enable');await cdp('Page.enable');
 await until(`document.querySelectorAll('input[type=file]').length===2`);
 await upload(0,root+'/source_pdfs/자이사업승인도서.pdf');await until(`document.querySelector('select[aria-label="Ref 페이지"]')?.options.length===6`);
 await upload(1,root+'/source_pdfs/자이실시도서.pdf');await until(`document.querySelector('select[aria-label="Target 페이지"]')?.options.length===6`);
 await select('Ref 페이지',4);await select('Target 페이지',4);await extract();
 // Start a navigation pointer without sending its pointerup; simulate window blur.
 const initial=await view(1),x=initial.left+initial.width/2,y=initial.top+initial.height/2;
 await cdp('Emulation.setTouchEmulationEnabled',{enabled:true,maxTouchPoints:2});
 await cdp('Input.dispatchTouchEvent',{type:'touchStart',touchPoints:[{x,y,id:1}]});
 if(!(await snapshot()).dragging)throw Error('Navigation precondition missing');
 await js(`window.dispatchEvent(new Event('blur'))`);
 if((await snapshot()).dragging)throw Error('Focus loss left navigation dragging');
 report.checks.focus_loss_clears_dragging=true;
 await physical('Target ROI 그리기');
 const active=await snapshot();if(active.mode!=='draw'||active.cursor!=='crosshair'||!active.indicator.includes('ROI 그리기'))throw Error('Draw mode not visible or active');
 report.checks.real_button_activates_visible_draw_mode=true;
 const v=await view(1),xx=v.left+v.x+900*v.scale,yy=v.top+v.y+700*v.scale;
 await mouse('mousePressed',xx,yy,{button:'left',clickCount:1});await mouse('mouseMoved',xx+15,yy+10,{buttons:1,button:'left'});await mouse('mouseReleased',xx+15,yy+10,{button:'left',clickCount:1});await pause(100);
 const after=await view(1);near(after.scale,v.scale,'Draw mode changed scale');near(after.x,v.x,'Draw mode panned x');near(after.y,v.y,'Draw mode panned y');
 if((await snapshot()).dragging)throw Error('Ghost touch kept hand cursor');report.checks.stale_touch_cannot_turn_draw_into_pan_or_pinch=true;
 await cdp('Input.dispatchTouchEvent',{type:'touchEnd',touchPoints:[]});await cdp('Emulation.setTouchEmulationEnabled',{enabled:false});
 for(const p of [[650,500],[1500,500],[1500,1150]])await point(...p);
 if((await snapshot()).points.length!==3)throw Error('Real mouse clicks did not add vertices');
 await physical('Target ROI 완료');if(!await js(`!!document.querySelector('.roi-overlay polygon')`))throw Error('Cannot finish real clicked polygon');report.checks.physical_clicks_create_and_finish_polygon=true;
 // Switching modes cancels any unfinished pan, without requiring a blur.
 await physical('Target ROI 이동 모드');await js(`document.querySelector('[data-role=Target] .viewport').scrollIntoView({block:'center'})`);await pause(100);
 const pan=await view(1),px=pan.left+pan.width/2,py=pan.top+pan.height/2;
 await mouse('mousePressed',px,py,{button:'left',clickCount:1});
 await click('Target ROI 그리기');
 if((await snapshot()).dragging||((await snapshot()).cursor!=='crosshair'))throw Error('Mode switch left old dragging state');
 await mouse('mouseReleased',px,py,{button:'left',clickCount:1});
 if((await snapshot()).points.length)throw Error('Old pan release created an ROI vertex');
 report.checks.mode_switch_cancels_old_pan_without_adding_point=true;
 await point(800,600);if((await snapshot()).points.length!==1)throw Error('Cannot draw after mode switch');
 if(await js(`document.querySelector('[data-role=Ref] svg').children.length`))throw Error('Ref changed');
 if(runtimeErrors.length)throw Error(JSON.stringify(runtimeErrors));report.checks.ref_unchanged_and_no_runtime_errors=true;
 await shot(root+'/phase2a_validation/roi_interaction_fix.png');
 fs.writeFileSync(root+'/phase2a_validation/roi_interaction_fix.json',JSON.stringify(report,null,2));console.log(JSON.stringify(report));
}finally{socket.close();}
