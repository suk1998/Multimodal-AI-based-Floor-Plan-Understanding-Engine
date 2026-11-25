// Real browser regression. Requires API 8010, Vite 5173 and test Chrome CDP 9223.
import fs from "node:fs";
import {fileURLToPath} from "node:url";
const root=fileURLToPath(new URL("../../",import.meta.url)).replace(/\/$/,"");
const page=await(await fetch("http://127.0.0.1:9223/json/new?http://127.0.0.1:5173",{method:"PUT"})).json();
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
const out=root+'/phase2b2_validation',downloads=fs.mkdtempSync('/tmp/floorplan-structural-anchors-');fs.mkdirSync(out,{recursive:true});
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
try{
 await cdp('Runtime.enable');await cdp('Page.enable');await cdp('Network.enable');await until(`document.querySelectorAll('input[type=file]').length===2`);
 await upload(0,root+'/source_pdfs/자이사업승인도서.pdf');await until(`document.querySelector('select[aria-label="Ref 페이지"]')?.options.length===6`);
 await upload(1,root+'/source_pdfs/자이실시도서.pdf');await until(`document.querySelector('select[aria-label="Target 페이지"]')?.options.length===6`);
 await select('Ref 페이지',4);await select('Target 페이지',4);await extract();
 await uploadJSON('Target ROI 불러오기',root+'/alignment_inputs/page4_user_roi.json');const roiBefore=await js(`document.querySelector('[data-roi-coordinates]').textContent`);
 await physical('button[aria-label="구조 Anchor 도구 열기"]');
 if(await count()!==0)throw Error('Anchors pre-populated');report.checks.no_automatic_anchor_or_sift_import=true;
 await button('구조 Anchor 새 Pair');const firstId=(await selected()).id;
 await nativePoint('Ref',596.4,965);const pending=await js(`document.querySelector('.structural-anchor-comparison [data-role=Ref] [data-structural-id="${firstId}"] polyline').getAttribute('points')`);
 await button('구조 Anchor 이동 모드');same(await js(`document.querySelector('.structural-anchor-comparison [data-role=Ref] [data-structural-id="${firstId}"] polyline').getAttribute('points')`),pending,'Pan discarded pending analysis point');
 await button('구조 Anchor Ref 선택');await nativePoint('Ref',596.4,1100);const one=await selected();if(!one.ref||one.target!==null)throw Error('Ref selection inferred Target');report.checks.ref_two_points_and_pan_pause_preserve_draft=true;
 await button('구조 Anchor Target 선택');await nativePoint('Target',596.4,965);await nativePoint('Target',596.4,1100);
 await fill('구조 Anchor Parent ID','left_bed_wall');await setSelect('구조 Anchor 경계 종류','OUTER');await setSelect('구조 Anchor 용도','FIT');
 await fill('구조 Anchor 검토 근거','TEST ONLY · 좌측 외벽 국소 구간 대응 검토 시뮬레이션, 실제 사용자 승인 아님');
 if(!(await selected()).review.includes('UNREVIEWED'))throw Error('Selection auto-reviewed');await checkReview();report.checks.explicit_user_review_is_correspondence_only=true;
 await button('구조 Anchor 구간 보기');const original=await selected();
 // Actual pointer dragging edits a selected interval boundary, not a Landmark.
 await button('구조 Anchor 지정점 수정');await expose('Ref');const v=await anchorView('Ref'),p=original.ref[0],x=v.left+v.x+p[0]*v.scale,y=v.top+v.y+p[1]*v.scale;
 await mouse('mousePressed',x,y,{button:'left',clickCount:1});await mouse('mouseMoved',x+18,y+10,{button:'left',buttons:1});await mouse('mouseMoved',x+30,y+14,{button:'left',buttons:1});await mouse('mouseReleased',x+30,y+14,{button:'left',clickCount:1});await pause(120);
 const edited=await selected();if(edited.ref[0][0]===original.ref[0][0]||!edited.review.includes('INVALIDATED'))throw Error('Edit did not invalidate review');same(edited.target,original.target,'Editing Ref moved Target');report.checks.endpoint_drag_invalidates_review_and_keeps_other_side=true;
 // Zoom, pan and DPR changes leave coordinates fixed.
 const coords=await selected(),other=await anchorView('Target');await physical('.structural-anchor-comparison [data-role=Ref] button[aria-label="Ref 확대"]');same((await selected()).ref,coords.ref,'Zoom changed native coordinates');near((await anchorView('Target')).scale,other.scale,'Independent Target camera');
 await expose('Ref');const nav=await anchorView('Ref'),cx=nav.left+nav.width/2,cy=nav.top+nav.height/2;
 await mouse('mousePressed',cx,cy,{button:'left',clickCount:1,modifiers:8});await mouse('mouseMoved',cx+32,cy+20,{button:'left',buttons:1,modifiers:8});await mouse('mouseReleased',cx+32,cy+20,{button:'left',clickCount:1,modifiers:8});await pause(100);same((await selected()).ref,coords.ref,'Shift-pan edited geometry');
 await cdp('Emulation.setDeviceMetricsOverride',{width:1550,height:1350,deviceScaleFactor:2,mobile:false});await pause(120);same((await selected()).ref,coords.ref,'DPR changed coordinates');await cdp('Emulation.clearDeviceMetricsOverride');await pause(120);
 report.checks.zoom_pan_dpr2_keep_native_coordinates=true;
 await button('구조 Anchor Ref 선택');await nativePoint('Ref',596.4,965);await nativePoint('Ref',596.4,1100);await checkReview();
 // Reference import requires explicit action and starts HOLD / UNREVIEWED.
 await js(`document.querySelector('.anchor-references').open=true`);await button('참고 S02 초안 추가');const s2Id=(await selected()).id;
 if(!(await selected()).review.includes('UNREVIEWED')||await js(`document.querySelector('select[aria-label="구조 Anchor 용도"]').value`)!=='HOLD')throw Error('Reference auto-approved');report.checks.s02_reference_is_unreviewed_hold=true;
 await fill('구조 Anchor Parent ID',' LEFT_BED_WALL ');await setSelect('구조 Anchor 용도','VALIDATION');
 if(!await js(`!!document.querySelector('.anchor-conflicts')`))throw Error('Shared Parent independence conflict missing');
 await setSelect('구조 Anchor 용도','EXCLUDE');if(await js(`!!document.querySelector('.anchor-conflicts')`))throw Error('EXCLUDE remained in validation conflict');
 await fill('구조 Anchor Parent ID','right_exterior');await setSelect('구조 Anchor 용도','VALIDATION');if(await js(`!!document.querySelector('.anchor-conflicts')`))throw Error('Distinct parents conflict');report.checks.parent_fit_validation_conflict_and_exclusion=true;
 await fill('구조 Anchor 검토 근거','TEST ONLY · 우측 외벽 별도 구간의 대응 검토 시뮬레이션, 정렬 성공 판정 아님');await checkReview();
 await js(`document.querySelector('.structural-anchor-comparison').scrollIntoView({block:'start'})`);await pause(100);await shot(out+'/manual_selection.png');
 await js(`document.querySelector('.structural-anchor-toolbar').scrollIntoView({block:'start'})`);await pause(100);await shot(out+'/manual_review.png');
 await button('참고 S03 초안 추가');const s3Id=(await selected()).id,s3=await selected();if(s3.ref[1][0]!==1120||s3.target[1][0]!==1120||Math.max(...s3.ref.map(p=>p[0]))>=1161)throw Error('S03 includes M01 changed end');report.checks.s03_reference_excludes_m01_changed_endpoint=true;
 // M04 needs independent manual Ref input, never automatic wall movement pairing.
 await button('구조 Anchor 새 Pair');const m4Id=(await selected()).id;await button('구조 Anchor Target 선택');await nativePoint('Target',442.56,700);await nativePoint('Target',442.56,790);
 if((await selected()).ref!==null)throw Error('M04 inferred existing Ref wall');await button('구조 Anchor Ref 선택');await nativePoint('Ref',476.64,705);await nativePoint('Ref',476.64,805);
 await fill('구조 Anchor Parent ID','ambiguous-left-bath');await setSelect('구조 Anchor 용도','EXCLUDE');await fill('구조 Anchor 검토 근거','TEST ONLY · 기존 벽과 새 평행선의 동일성이 미확정이므로 제외 기록');if(!(await selected()).review.includes('UNREVIEWED'))throw Error('M04 declared stable');report.checks.m04_not_automatically_matched_or_stable=true;
 same(await js(`document.querySelector('[data-roi-coordinates]').textContent`),roiBefore,'Anchor tool changed Target ROI');if(await js(`document.querySelector('[aria-label="선택한 두 도면"] [data-role=Ref] svg').children.length`))throw Error('Original Ref annotated');report.checks.original_roi_and_central_ref_preserved=true;
 await cdp('Browser.setDownloadBehavior',{behavior:'allow',downloadPath:downloads});await button('구조 Anchor JSON 저장');
 for(let i=0;i<100&&!fs.readdirSync(downloads).some(p=>p.endsWith('.json'));i++)await pause(50);
 const name=fs.readdirSync(downloads).find(p=>p.endsWith('.json'));if(!name)throw Error('Anchor download failed');const filePath=downloads+'/'+name,file=JSON.parse(fs.readFileSync(filePath,'utf8'));
 if(file.kind!=='STRUCTURAL_ANCHOR_SET'||file.landmark_correspondence!==false||file.pairs.length!==4||file.pairs.some(p=>p.landmark_correspondence!==false)||'matrix' in file)throw Error('Anchor schema conflates inputs and alignment');
 fs.writeFileSync(out+'/test_only_structural_anchors.json',JSON.stringify(file,null,2));report.checks.anchor_json_has_bindings_intervals_review_history=true;
 await button('구조 Anchor Pair 삭제');if(await count()!==3)throw Error('Pair delete failed');await uploadJSON('구조 Anchor JSON 불러오기',filePath);if(await count()!==4)throw Error('Reload failed');
 for(const p of file.pairs){await selectPair(p.id);const loaded=await selected();same(loaded.ref,p.ref_segment,'Ref roundtrip');same(loaded.target,p.target_segment,'Target roundtrip');if(p.review.status==='REVIEWED'&&!loaded.review.includes('INVALIDATED'))throw Error('Imported review auto-applied');}
 report.checks.json_roundtrip_exact_geometry_and_review_invalidation=true;
 const bad={...file,ref_image:{...file.ref_image,page:5}},badPath=downloads+'/wrong_input.json';fs.writeFileSync(badPath,JSON.stringify(bad));await uploadJSON('구조 Anchor JSON 불러오기',badPath);await until(`document.querySelector('.structural-anchor-panel .error')!==null`);if(await count()!==4)throw Error('Failed load erased current records');report.checks.mismatched_json_rejected_without_overwriting=true;
 // Late file reads cannot restore a deleted record.
 await js(`window.__originalAnchorText=File.prototype.text;File.prototype.text=function(){return new Promise(resolve=>{window.__finishAnchorRead=()=>window.__originalAnchorText.call(this).then(resolve);});}`);
 await uploadJSON('구조 Anchor JSON 불러오기',filePath);await until(`typeof window.__finishAnchorRead==='function'`);await button('구조 Anchor Pair 삭제');await js(`window.__finishAnchorRead();File.prototype.text=window.__originalAnchorText`);await pause(200);if(await count()!==3)throw Error('Late import restored deleted record');report.checks.stale_file_read_cannot_restore_deleted_pair=true;
 await select('Target 페이지',5);await extract();await physical('button[aria-label="구조 Anchor 도구 열기"]');if(await count()!==0)throw Error('Page change carried reviews');await uploadJSON('구조 Anchor JSON 불러오기',filePath);await until(`document.querySelector('.structural-anchor-panel .error')!==null`);if(await count()!==0)throw Error('Old page input applied');report.checks.page_change_clears_reviews_and_old_file_rejected=true;
 await select('Target 페이지',4);await select('이미지 추출 배율',1);await extract();await physical('button[aria-label="구조 Anchor 도구 열기"]');await uploadJSON('구조 Anchor JSON 불러오기',filePath);await until(`document.querySelector('.structural-anchor-panel .error')!==null`);if(await count()!==0)throw Error('Extraction scale reused old anchors');report.checks.zoom_change_rejects_old_anchor_coordinates=true;
 await select('이미지 추출 배율',2);await upload(1,root+'/source_pdfs/자이사업승인도서.pdf');await until(`document.querySelectorAll('.file-summary span')[1]?.textContent==='자이사업승인도서.pdf'`);await select('Target 페이지',4);await extract();await physical('button[aria-label="구조 Anchor 도구 열기"]');await uploadJSON('구조 Anchor JSON 불러오기',filePath);await until(`document.querySelector('.structural-anchor-panel .error')!==null`);if(await count()!==0)throw Error('Different PDF reused anchors');report.checks.pdf_hash_change_rejects_old_reviewed_anchors=true;
 if(alignmentRequests!==0)throw Error('Manual segments sent to current alignment algorithm');if(runtimeErrors.length)throw Error(JSON.stringify(runtimeErrors));report.checks.no_alignment_calls_or_runtime_exceptions=true;
 report.test_pair_ids={firstId,s2Id,s3Id,m4Id};report.download_path=filePath;fs.writeFileSync(out+'/browser.json',JSON.stringify(report,null,2));console.log(JSON.stringify(report));
}finally{socket.close();}
