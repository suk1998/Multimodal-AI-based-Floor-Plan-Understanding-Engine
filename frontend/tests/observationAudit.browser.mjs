// Actual Chrome + uploaded Ref PDF. No cached OCR/space fixture is injected.
import {spawn} from 'node:child_process';
import {mkdirSync,mkdtempSync,writeFileSync,existsSync,readFileSync} from 'node:fs';
import {resolve,join} from 'node:path';
import {fileURLToPath} from 'node:url';
const root=fileURLToPath(new URL('../../',import.meta.url)),base=process.argv[2]||'http://127.0.0.1:8021',out=resolve(process.argv[3]||join(root,'implementation_validation/2026-11-20/step2/browser'));
if(!/^http:\/\/127\.0\.0\.1:802[01]$|^https:\/\/[a-z0-9-]+\.trycloudflare\.com$/.test(base))throw Error('Expected approved FPUE test origin');
mkdirSync(out,{recursive:true});const temp=mkdtempSync('/tmp/fpue-reference-browser-'),port=9343;
const chrome=spawn('/usr/bin/google-chrome',['--headless=new','--no-sandbox','--disable-gpu','--disable-dev-shm-usage','--no-first-run','--remote-debugging-port='+port,'--user-data-dir='+temp,'about:blank'],{stdio:'ignore'});
const pause=ms=>new Promise(r=>setTimeout(r,ms)),checks=[],errors=[];let ws,sequence=0,jobId;
function check(name,ok,evidence){checks.push({name,passed:!!ok,evidence});if(!ok)throw Error(name+': '+JSON.stringify(evidence));}
try{
 let target;for(let i=0;i<100;i++){try{target=(await(await fetch(`http://127.0.0.1:${port}/json`)).json())[0];if(target)break}catch{}await pause(100)}
 ws=new WebSocket(target.webSocketDebuggerUrl);await new Promise((r,j)=>{ws.onopen=r;ws.onerror=j});const pending=new Map();
 ws.onmessage=({data})=>{const m=JSON.parse(data);if(m.id){const p=pending.get(m.id);if(p){pending.delete(m.id);clearTimeout(p.timer);m.error?p.reject(Error(JSON.stringify(m.error))):p.resolve(m.result)}}else if(m.method==='Runtime.exceptionThrown')errors.push(m.params.exceptionDetails)};
 const send=(method,params={})=>new Promise((resolve,reject)=>{const id=++sequence,timer=setTimeout(()=>{pending.delete(id);reject(Error('CDP timeout '+method))},30000);pending.set(id,{resolve,reject,timer});ws.send(JSON.stringify({id,method,params}))});
 const js=async expression=>{const r=await send('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true,userGesture:true});if(r.exceptionDetails)throw Error(JSON.stringify(r.exceptionDetails));return r.result.value};
 async function until(expression,timeout=30000){const end=Date.now()+timeout;while(Date.now()<end){if(await js(expression))return;await pause(350)}throw Error('Timed out '+expression+'\n'+await js('document.body.innerText'))}
 async function button(text){await js(`(()=>{const b=[...document.querySelectorAll('button')].find(b=>b.textContent.trim()===${JSON.stringify(text)}||b.getAttribute('aria-label')===${JSON.stringify(text)});if(!b||b.disabled)throw Error('Button unavailable: '+${JSON.stringify(text)});b.click()})()`);await pause(100)}
 async function upload(role,path){const doc=await send('DOM.getDocument'),n=await send('DOM.querySelector',{nodeId:doc.root.nodeId,selector:`input[aria-label="${role} PDF 업로드"]`});await send('DOM.setFileInputFiles',{nodeId:n.nodeId,files:[join(root,path)]});await until(`!!document.querySelector('select[aria-label="${role} 페이지"]')`)}
 async function page(role,value){await js(`(()=>{const s=document.querySelector('select[aria-label="${role} 페이지"]');s.value='${value}';s.dispatchEvent(new Event('change',{bubbles:true}))})()`);await pause(100)}
 async function shot(name){const s=await send('Page.captureScreenshot',{format:'png',captureBeyondViewport:false});writeFileSync(join(out,name),Buffer.from(s.data,'base64'))}
 await send('Browser.setDownloadBehavior',{behavior:'allow',downloadPath:out});await send('Runtime.enable');await send('Page.enable');await send('Emulation.setDeviceMetricsOverride',{width:1536,height:1100,deviceScaleFactor:1,mobile:false});await send('Page.navigate',{url:base});
 await until(`!!document.querySelector('input[aria-label="Ref PDF 업로드"]:not(:disabled)')`);
 check('empty start without fixed Page 4',await js(`document.querySelectorAll('.drawing-panel').length===0`));
 await upload('Ref','source_pdfs/자이사업승인도서.pdf');await page('Ref',4);await button('선택한 페이지 추출');
 await until(`!!document.querySelector('.drawing-panel[data-role="Ref"] img')`);
 check('Ref-only page extraction, exactly one source image',await js(`document.querySelectorAll('.drawing-panel img').length===1&&!document.querySelector('.drawing-panel[data-role="Target"]')`));
 await shot('01_ref_only.png');await button('Reference 이해 실행');
 await until(`['PARTIAL','COMPLETED','FAILED'].includes(document.querySelector('[data-reference-state]')?.dataset.referenceState)`,260000);
 const phase=await js(`document.querySelector('[data-reference-state]').dataset.referenceState`);check('live Ref job returns candidates',phase==='PARTIAL'||phase==='COMPLETED',await js('document.body.innerText'));
 const resources=await js(`performance.getEntriesByType('resource').map(e=>e.name).filter(n=>n.includes('/api/reference-understandings/'))`);jobId=resources.at(-1)?.split('/').at(-1);check('job polled over owning API',!!jobId);
 const job=await js(`fetch('/api/reference-understandings/${jobId}').then(r=>r.json())`);writeFileSync(join(out,'actual_reference_job.json'),JSON.stringify(job,null,2));
 check('new OCR and independent Ref binding',job.result.coverage.new_OCR_run&&job.result.source_role==='Ref'&&!job.result.coverage.target_input_used,job.result.components);
 check('no architectural verification fabricated',!job.result.architectural_gate_passed&&job.result.summary.verified_architectural_spaces===0);
 check('live label candidates include bedroom pantry dressing', ['침실1','팬트리','드레스룸'].every(n=>job.result.cases.some(c=>c.label.name===n)),job.result.cases.map(c=>c.label.name));
 await until(`document.querySelectorAll('.reference-understanding polyline').length>0`);
 await js(`(()=>{const label=[...document.querySelectorAll('.drawing-panel[data-role="Ref"] label')].find(l=>l.textContent.includes('공간명 OCR'));label.querySelector('input').click()})()`);await pause(100);
 check('space-label OCR layer uses real text polygons',await js(`document.querySelectorAll('.reference-understanding polygon').length`)===job.result.cases.length);
 await js(`document.querySelector('.drawing-panel[data-role="Ref"] details').open=true`);await button('Ref 이해 JSON 저장');
 const downloaded=join(out,'ref_page_4_understanding.json');for(let i=0;i<100&&!existsSync(downloaded);i++)await pause(100);
 check('JSON download retains source and PROPOSED state',existsSync(downloaded)&&JSON.parse(readFileSync(downloaded,'utf8')).run_id===job.run_id);

 const snapshot=await js(`document.querySelector('.reference-understanding').outerHTML`);await shot('02_actual_ref_candidates.png');
 await button('침실1 · 후보');await pause(150);const viewport=await js(`(()=>{const e=document.querySelector('.drawing-panel[data-role="Ref"] .viewport'),r=e.getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2}})()`);
 await send('Input.dispatchMouseEvent',{type:'mouseWheel',x:viewport.x,y:viewport.y,deltaX:0,deltaY:-180});
 await send('Input.dispatchMouseEvent',{type:'mousePressed',x:viewport.x,y:viewport.y,button:'left',clickCount:1});await send('Input.dispatchMouseEvent',{type:'mouseMoved',x:viewport.x+70,y:viewport.y+45,button:'left',buttons:1});await send('Input.dispatchMouseEvent',{type:'mouseReleased',x:viewport.x+70,y:viewport.y+45,button:'left',clickCount:1});await pause(150);
 check('Zoom/Pan retains original native candidate geometry',snapshot===await js(`document.querySelector('.reference-understanding').outerHTML`));await shot('03_bedroom_zoom_pan.png');
 await upload('Target','source_pdfs/자이실시도서.pdf');await page('Target',4);await button('선택한 페이지 추출·나란히 보기');await until(`document.querySelectorAll('.drawing-panel img').length===2`);
 check('adding Target preserves completed Ref result',snapshot===await js(`document.querySelector('.reference-understanding').outerHTML`));await shot('04_target_added_ref_retained.png');

 // Stage 2: inspect actual audits, then select a server-issued layout candidate.
 const full=job.result;
 check('actual detection and recognition stages completed separately',full.components.OCR.stages.detection.status==='COMPLETED'&&full.components.OCR.stages.recognition.status==='COMPLETED',full.components.OCR.stages);
 const coverage=full.observation_bundle.coverage.spatial_audit;
 check('cap omissions have native spatial records and no invented recall',coverage.cells.some(c=>Object.values(c.counts_by_type).some(v=>v.omitted_due_to_limit>0))&&Object.values(coverage.counts_by_type).every(v=>v.encountered===(v.retained||0)+(v.omitted_due_to_limit||0))&&!coverage.empty_cell_is_confirmed_empty,coverage.counts_by_type);
 check('OCR raw and normalized strings preserved',full.observation_bundle.observations.filter(o=>o.observation_type==='TextRegion').every(o=>typeof o.source_evidence.text==='string'&&typeof o.source_evidence.normalized_text==='string'&&o.source_evidence.raw_recognition));
 check('reobservation is not question verification',full.reobservation_audit.tiles.length>0&&full.reobservation_audit.tiles.every(t=>!t.architectural_truth_verified&&['UNRESOLVED','PARTIAL_GEOMETRY_ONLY'].includes(t.resolution_status)));
 check('overlapping original primitive evidence is tracked',full.reobservation_audit.tiles.some(t=>t.overlaps.length>0)&&full.reobservation_audit.duplicate_source_support.length>0);
 await js(`document.querySelector('.drawing-panel[data-role="Ref"] details').open=true`);
 await js(`document.querySelector('input[aria-label="Ref 관찰 누락 위치"]').click();document.querySelector('input[aria-label="Ref DrawingRegion 후보 표시"]').click()`);await pause(150);
 check('coverage and layout overlays shown on original frame',await js(`document.querySelectorAll('[data-coverage-cell]').length>0&&document.querySelectorAll('[data-drawing-region]').length>0`));await shot('05_coverage_layout.png');
 const candidate=full.drawing_regions.candidates.filter(r=>r.kind==='FLOORPLAN_CANDIDATE').sort((a,b)=>(a.bbox_native[2]-a.bbox_native[0])*(a.bbox_native[3]-a.bbox_native[1])-(b.bbox_native[2]-b.bbox_native[0])*(b.bbox_native[3]-b.bbox_native[1]))[0]||full.drawing_regions.candidates[0];
 check('real layout candidate available but unverified',candidate&&!candidate.is_physical_space_polygon&&candidate.status==='PROPOSED',full.drawing_regions);
 const nativeBefore=await js(`JSON.stringify([...document.querySelectorAll('[data-reference-case]')].map(e=>e.outerHTML))`);
 check('drawings are chosen by collapsed native-image previews',await js(`(()=>{
  const picker=document.querySelector('.drawing-region-picker');
  return !picker.open && !!picker.querySelector('button[data-region-choice] image[href^="blob:"]') &&
         !document.querySelector('select[aria-label="Ref DrawingRegion 선택"]');
 })()`));
 await js(`(()=>{
  const card=[...document.querySelectorAll('button[data-region-choice]')].find(b=>b.dataset.regionChoice===${JSON.stringify(candidate.region_id)});
  for(let parent=card.parentElement;parent;parent=parent.parentElement)if(parent.tagName==='DETAILS')parent.open=true;
  card.click();
 })()`);await pause(100);
 check('chosen preview identifies the drawing on the native image',await js(`[...document.querySelectorAll('button[aria-pressed=true]')].some(b=>b.dataset.regionChoice===${JSON.stringify(candidate.region_id)})`));
 check('selecting DrawingRegion retains previous native result until reanalysis',nativeBefore===await js(`JSON.stringify([...document.querySelectorAll('[data-reference-case]')].map(e=>e.outerHTML))`));
 const invalid=await js(`fetch('/api/reference-understandings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({...${JSON.stringify({document_id:job.document_id,source_role:'Ref',page:4,extraction_zoom:2,image:{source_sha256:full.source_binding.source_sha256,image_id:full.source_binding.image_id,frame_id:full.frame_id,width:full.source_binding.width,height:full.source_binding.height,extraction_zoom:2}})},drawing_region_selection:{run_id:${JSON.stringify(jobId)},region_id:'NOT_A_REAL_REGION'}})}).then(r=>r.status)`);
 check('invented layout ID rejected by server',invalid===422,invalid);
 await button('Reference 이해 실행');await until(`['PARTIAL','COMPLETED','FAILED'].includes(document.querySelector('[data-reference-state]')?.dataset.referenceState)`,260000);
 check('selected-region live analysis completed',await js(`['PARTIAL','COMPLETED'].includes(document.querySelector('[data-reference-state]').dataset.referenceState)`));
 const urls=await js(`performance.getEntriesByType('resource').map(e=>e.name).filter(n=>n.includes('/api/reference-understandings/'))`);const scopedId=urls.at(-1).split('/').at(-1);
 const scoped=await js(`fetch('/api/reference-understandings/${scopedId}').then(r=>r.json())`);writeFileSync(join(out,'selected_region_job.json'),JSON.stringify(scoped,null,2));
 check('selected scope and original native frame remain separate',JSON.stringify(scoped.result.source_binding.core_bbox_native)===JSON.stringify(candidate.bbox_native)&&scoped.result.frame_id===full.frame_id&&scoped.result.source_binding.width===2382&&scoped.result.source_binding.height===1684);
 check('only requested native scope feeds local reobservation',scoped.result.reobservation_requests.every(r=>r.native_context[0]>=Math.floor(candidate.bbox_native[0])&&r.native_context[1]>=Math.floor(candidate.bbox_native[1])&&r.native_context[2]<=Math.ceil(candidate.bbox_native[2])&&r.native_context[3]<=Math.ceil(candidate.bbox_native[3])));
 check('scope lineage records parent but never approves architecture',scoped.result.source_binding.drawing_region_selection.run_id===jobId&&!scoped.result.architectural_gate_passed);
 await shot('06_selected_scope.png');
 await page('Ref',5);check('changing Ref page removes previous Ref result only',await js(`!document.querySelector('.reference-understanding')&&!!document.querySelector('.drawing-panel[data-role="Target"] img')`));
 check('no browser JS exceptions',errors.length===0,errors);
}catch(error){checks.push({name:'execution',passed:false,evidence:String(error)});process.exitCode=1}
finally{writeFileSync(join(out,'checks.json'),JSON.stringify({base,jobId,checks,errors},null,2));ws?.close();chrome.kill()}
console.log(JSON.stringify(checks.map(c=>({name:c.name,passed:c.passed})),null,2));
