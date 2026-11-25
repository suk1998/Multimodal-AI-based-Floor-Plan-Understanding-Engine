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
 if(!r.hit.includes('aria-label='+JSON.stringify(label)))throw Error('Button is covered: '+label+' '+r.hit);await cdp('Input.dispatchMouseEvent',{type:'mousePressed',x:r.x,y:r.y,button:'left',clickCount:1});await cdp('Input.dispatchMouseEvent',{type:'mouseReleased',x:r.x,y:r.y,button:'left',clickCount:1});await pause(100);
}// Each physical button is checked for its intended effect and unintended effects.
const report={status:'passed',test_only_roi:true,checks:{}};
const same=(a,b,label)=>{if(JSON.stringify(a)!==JSON.stringify(b))throw Error(label);};
const state=()=>js(`(()=>{const v=document.querySelector('[data-role=Target] .viewport');return {mode:v.dataset.roiMode,points:JSON.parse(document.querySelector('[data-roi-coordinates]').textContent),selected:document.querySelector('.roi-overlay .selected')?.getAttribute('data-vertex')??null,closed:document.querySelector('.roi-overlay').dataset.closed==='true'};})()`);
const disabled=label=>js(`document.querySelector('button[aria-label=${JSON.stringify(label)}]').disabled`);
async function mouse(type,x,y,extra={}){await cdp('Input.dispatchMouseEvent',{type,x,y,...extra});}
async function expose(){await js(`document.querySelector('[data-role=Target] .viewport').scrollIntoView({block:'center'})`);await pause(60);}
async function point(nx,ny){await expose();const v=await view(1),x=v.left+v.x+nx*v.scale,y=v.top+v.y+ny*v.scale;await mouse('mousePressed',x,y,{button:'left',clickCount:1});await mouse('mouseReleased',x,y,{button:'left',clickCount:1});await pause(80);}
async function drag(nx,ny,dx,dy){await expose();const v=await view(1),x=v.left+v.x+nx*v.scale,y=v.top+v.y+ny*v.scale;await mouse('mousePressed',x,y,{button:'left',clickCount:1});await mouse('mouseMoved',x+dx,y+dy,{button:'left',buttons:1});await mouse('mouseReleased',x+dx,y+dy,{button:'left',clickCount:1});await pause(100);}
async function uploadROI(path){const d=await cdp('DOM.getDocument');const n=await cdp('DOM.querySelector',{nodeId:d.root.nodeId,selector:'input[aria-label="Target ROI 불러오기"]'});await cdp('DOM.setFileInputFiles',{nodeId:n.nodeId,files:[path]});await pause(150);}
function sameCamera(a,b,label){for(const key of ['scale','x','y'])near(a[key],b[key],label+' '+key);}
const downloads=fs.mkdtempSync('/tmp/floorplan-roi-buttons-');
try{
 await cdp('Runtime.enable');await cdp('Page.enable');await until(`document.querySelectorAll('input[type=file]').length===2`);
 await upload(0,root+'/source_pdfs/자이사업승인도서.pdf');await until(`document.querySelector('select[aria-label="Ref 페이지"]')?.options.length===6`);
 await upload(1,root+'/source_pdfs/자이실시도서.pdf');await until(`document.querySelector('select[aria-label="Target 페이지"]')?.options.length===6`);
 await select('Ref 페이지',4);await select('Target 페이지',4);await extract();
 for(const label of ['Target ROI 완료','Target ROI 수정','Target ROI 선택 취소','Target ROI 마지막 점 취소','Target ROI 그리기 취소','Target ROI 저장'])if(!await disabled(label))throw Error('Unavailable action enabled: '+label);
 report.checks.initial_button_availability=true;
 await physical('Target ROI 그리기');await point(650,500);await point(1500,500);const draft=(await state()).points;
 if(!await disabled('Target ROI 완료'))throw Error('Two-point polygon can finish');
 await physical('Target ROI 이동 모드');same((await state()).points,draft,'Pan discarded draft');if((await state()).mode!=='pan')throw Error('Pan not active');
 await drag(1000,800,32,18);same((await state()).points,draft,'Pan modified vertices');
 await physical('Target ROI 그리기');same((await state()).points,draft,'Resume cleared vertices');await physical('Target ROI 그리기');same((await state()).points,draft,'Repeated draw cleared vertices');
 report.checks.pan_preserves_draft_and_draw_resumes=true;
 await point(1500,1150);await point(700,1150);const four=(await state()).points;
 await physical('Target ROI 마지막 점 취소');same((await state()).points,four.slice(0,-1),'Undo did not remove just one point');await point(700,1150);report.checks.undo_only_removes_last_point=true;
 const camera=await view(1);await physical('Target ROI 완료');const completed=await state();if(!completed.closed||completed.points.length!==4)throw Error('Finish failed');sameCamera(await view(1),camera,'Finish moved camera');report.checks.finish_closes_polygon_without_moving_camera=true;
 await physical('Target ROI 수정');const beforeEdit=await state(),cameraBeforeEdit=await view(1);await drag(...beforeEdit.points[0],24,16);const edited=await state();if(edited.points[0][0]===beforeEdit.points[0][0])throw Error('Edit did not move vertex');same(edited.points.slice(1),beforeEdit.points.slice(1),'Edit changed other vertices');sameCamera(await view(1),cameraBeforeEdit,'Edit moved camera');report.checks.edit_only_moves_dragged_vertex=true;
 await physical('Target ROI 선택 취소');const deselected=await state();same(deselected.points,edited.points,'Deselect deleted geometry');if(deselected.selected!==null||deselected.mode!=='edit'||!deselected.closed)throw Error('Deselect changed mode or polygon');report.checks.deselect_only_clears_vertex_selection=true;
 await physical('Target ROI 이동 모드');const beforePan=await view(1);await drag(...edited.points[0],28,17);same((await state()).points,edited.points,'Pan dragged vertex');if((await view(1)).x===beforePan.x)throw Error('Pan failed');report.checks.pan_does_not_edit_vertices=true;
 const geometryBefore=await state();
 for(const role of ['Ref','Target']){
  const other=await view(role==='Ref'?1:0),before=await view(role==='Ref'?0:1);
  await physical(role+' 확대');near((await view(role==='Ref'?0:1)).scale,before.scale*1.25,'Independent zoom in');sameCamera(await view(role==='Ref'?1:0),other,'Other viewport changed');
  await physical(role+' 축소');near((await view(role==='Ref'?0:1)).scale,before.scale,'Zoom out');
  await physical(role+' 100%');near((await view(role==='Ref'?0:1)).scale,1,'100%');
  await physical(role+' 화면 맞춤');same(await state(),geometryBefore,'Viewport button changed ROI state');
 }
 report.checks.all_eight_viewport_buttons_keep_roi_and_other_view_independent=true;
 await cdp('Browser.setDownloadBehavior',{behavior:'allow',downloadPath:downloads});const beforeSave=await state();await physical('Target ROI 저장');
 for(let i=0;i<100&&!fs.readdirSync(downloads).some(p=>p.endsWith('.json'));i++)await pause(60);
 const filename=fs.readdirSync(downloads).find(p=>p.endsWith('.json'));if(!filename)throw Error('Save did not download JSON');const savedPath=downloads+'/'+filename,saved=JSON.parse(fs.readFileSync(savedPath,'utf8'));same(saved.vertices,beforeSave.points,'Save changed native coordinates');same(await state(),beforeSave,'Save modified editor state');report.checks.save_downloads_without_changing_editor=true;
 await physical('Target ROI 그리기');await point(900,600);await physical('Target ROI 이동 모드');const partial=await state();await physical('Target ROI 마지막 점 취소');if((await state()).points.length!==partial.points.length-1||(await state()).mode!=='pan')throw Error('Undo while paused changed mode');
 await physical('Target ROI 그리기 취소');same((await state()).points,beforeSave.points,'Explicit cancel failed to restore ROI');if(!(await state()).closed)throw Error('Cancel lost previous polygon');report.checks.explicit_cancel_restores_backup_only=true;
 const resetCamera=await view(1);await physical('Target ROI 초기화');same((await state()).points,[],'Reset did not clear geometry');if((await state()).mode!=='pan'||(await state()).closed)throw Error('Reset state incomplete');sameCamera(await view(1),resetCamera,'Reset moved camera');report.checks.reset_only_clears_roi=true;
 await uploadROI(savedPath);same((await state()).points,beforeSave.points,'Reload lost saved coordinates');if(!(await state()).closed)throw Error('Reload incomplete');report.checks.load_restores_saved_roi=true;
 // Verify deselect cannot cancel an unfinished polygon, including keyboard route.
 await physical('Target ROI 그리기');await point(950,650);const one=(await state()).points;
 if(!await disabled('Target ROI 선택 취소'))throw Error('No-selection deselect enabled');
 await physical('Target ROI 이동 모드');same((await state()).points,one,'Paused one-point draft lost');await physical('Target ROI 그리기');same((await state()).points,one,'Draft lost on resume');report.checks.selection_cancel_and_drawing_cancel_are_separate=true;
 if(await js(`document.querySelectorAll('.drawing-panel img').length`)!==2||await js(`document.querySelector('[data-role=Ref] svg').children.length`)!==0)throw Error('Duplicated drawings or Ref annotations');if(runtimeErrors.length)throw Error(JSON.stringify(runtimeErrors));report.checks.ref_unmarked_and_no_browser_errors=true;
 await shot(root+'/phase2a_validation/roi_buttons_after.png');fs.writeFileSync(root+'/phase2a_validation/roi_buttons_after.json',JSON.stringify(report,null,2));console.log(JSON.stringify(report));
}finally{await click('Ref PDF 제거').catch(()=>{});await click('Target PDF 제거').catch(()=>{});socket.close();}
