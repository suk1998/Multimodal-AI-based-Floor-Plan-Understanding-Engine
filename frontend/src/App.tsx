import {lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState} from "react";
import {fetchPage, listPdfs, openWorkspace, removePdf, uploadPdf} from "./api";
import type {DocumentLibrary, DrawingDocument, NativeImage} from "./api";
import {reviewPath, reviewRequest, type ComparisonListing, type SavedComparison} from './useObjectComparison';
import {useComparisonRun} from './useComparisonRun';
import {useSpatialAlignment} from "./useSpatialAlignment";
import {ComparisonWorkflow} from "./ComparisonWorkflow";
import {makeROI, type TargetROI} from "./roi";
import {DrawingViewer} from "./DrawingViewer";
import {TargetViewer} from "./TargetViewer";
import {ReferenceUnderstandingViewer} from "./ReferenceUnderstandingViewer";

const StructuralAnchorPanel=lazy(()=>import("./StructuralAnchorPanel").then(module=>({default:module.StructuralAnchorPanel})));
const AlignmentPanel=lazy(()=>import("./AlignmentPanel").then(module=>({default:module.AlignmentPanel})));

type Role = "Ref" | "Target";
interface Selection {document: DrawingDocument | null; page: number; uploading: boolean; error: string | null}
const blank = (): Selection => ({document:null,page:1,uploading:false,error:null});
const message = (error: unknown) => error instanceof Error ? error.message : "요청을 완료하지 못했습니다.";

export default function App({researchTools=true,publicUpload=false}:{researchTools?:boolean;publicUpload?:boolean}) {
  const [library,setLibrary] = useState<DocumentLibrary|null>(null);
  const [libraryError,setLibraryError] = useState('');
  const [deleting,setDeleting] = useState<string|null>(null);
  const libraryGeneration=useRef(0);
  async function refreshLibrary() {
    const version=++libraryGeneration.current;
    try {
      const listing=await listPdfs();
      if(version===libraryGeneration.current){setLibrary(listing);setLibraryError('');}
    } catch(e) {if(version===libraryGeneration.current)setLibraryError(message(e));}
  }

  const [selections,setSelections] = useState<Record<Role,Selection>>({Ref:blank(),Target:blank()});
  const [restored,setRestored] = useState<SavedComparison|null>(null);
  const [savedList,setSavedList] = useState<ComparisonListing[]|null>(null);
  const [restoreVersion,setRestoreVersion] = useState(0);
  const [targetROI,setTargetROI]=useState<TargetROI|null>(null);
  const [advancedOpen,setAdvancedOpen]=useState(false);
  const [zoom,setZoom] = useState(2), [extracted,setExtracted] = useState<Record<Role,NativeImage|null>>({Ref:null,Target:null});
  const [extracting,setExtracting] = useState(false), [error,setError] = useState<string | null>(null);
  const [workspaceReady,setWorkspaceReady]=useState(!publicUpload);
  const [observationState,setObservationState]=useState('NOT_RUN');
  useEffect(()=>{if(workspaceReady)void refreshLibrary();},[workspaceReady]);
  const uploadGeneration = useRef({Ref:0,Target:0}), extractionGeneration = useRef(0);
  const activeRequest = useRef<AbortController | null>(null);
  const imageUrls = useRef(new Set<string>());
  const currentSelections = useRef(selections);currentSelections.current=selections;

  useEffect(()=>{
    if(!publicUpload)return;
    let active=true;
    openWorkspace().then(()=>{if(active)setWorkspaceReady(true)}).catch(error=>{if(active)setError(message(error));});
    return()=>{active=false};
  },[publicUpload]);

  useEffect(() => () => {
    activeRequest.current?.abort();
    for(const url of imageUrls.current)URL.revokeObjectURL(url);
    imageUrls.current.clear();
  },[]);

  const [refRun,setRefRun]=useState<string|null>(null),[targetRun,setTargetRun]=useState<string|null>(null);
  useEffect(()=>{
    if (restored && (refRun || targetRun)) setRestored(null);
  },[restored,refRun,targetRun]);
  // No user ROI is invented or written. Whole-page is an explicit fallback
  // comparison scope until automatic plan selection has been validated.
  const comparisonROI=useMemo(()=>targetROI ?? (publicUpload&&extracted.Target
    ?makeROI(extracted.Target,[[0,0],[extracted.Target.width,0],[extracted.Target.width,extracted.Target.height],[0,extracted.Target.height]])
    :null),[publicUpload,extracted.Target,targetROI]);
  const comparison=useComparisonRun(extracted.Ref,extracted.Target,comparisonROI);
  function runComparison() {
    setRestored(null);
    void comparison.run();
  }
  const alignment=useSpatialAlignment(extracted.Ref,extracted.Target,comparisonROI,refRun,targetRun,
    restored ?? comparison.snapshot,runComparison,comparison.phase);
  const comparing=Boolean(comparison.phase||comparison.referencePhase);
  const currentImages=useRef(extracted);currentImages.current=extracted;
  const images=extracted.Ref&&extracted.Target?[extracted.Ref,extracted.Target]:null;
  function invalidate(role?:Role) {
    extractionGeneration.current++;activeRequest.current?.abort();activeRequest.current=null;
    const roles:Role[]=role?[role]:['Ref','Target'];
    for(const changed of roles){const old=currentImages.current[changed];if(old){URL.revokeObjectURL(old.url);imageUrls.current.delete(old.url);}}
    setExtracted(previous=>({...previous,...Object.fromEntries(roles.map(changed=>[changed,null]))}));
    setRestored(null);
    if(!role||role==='Ref')setRefRun(null);
    if(!role||role==='Target')setTargetRun(null);
    if(!role||role==='Target'){setTargetROI(null);setObservationState('NOT_RUN');}
    setExtracting(false);setError(null);setAdvancedOpen(false);
  }
  function chooseExisting(role:Role, document:DrawingDocument|null) {
    ++uploadGeneration.current[role];
    if(document?.id===currentSelections.current[role].document?.id)return;
    invalidate(role);
    setSelections(previous=>({...previous,[role]:{...blank(),document}}));
  }
  async function chooseFile(role:Role, file:File|null) {
    if(!file){chooseExisting(role,null);return;}
    const generation=++uploadGeneration.current[role];
    setSelections(previous=>({...previous,[role]:{...previous[role],uploading:true,error:null}}));
    try {
      const document=await uploadPdf(file);
      void refreshLibrary();
      if(generation!==uploadGeneration.current[role])return;
      if(document.id!==currentSelections.current[role].document?.id) {
        invalidate(role);
        setSelections(previous=>({...previous,[role]:{...blank(),document}}));
      } else {
        setSelections(previous=>({...previous,[role]:{...previous[role],uploading:false}}));
      }
    } catch(e) {
      if(generation===uploadGeneration.current[role])setSelections(previous=>({
        ...previous,[role]:{...previous[role],uploading:false,error:message(e)}
      }));
      void refreshLibrary();
    }
  }
  async function deleteStored(document:DrawingDocument) {
    if(!window.confirm(document.filename+'을 이 작업공간에서 삭제할까요? 이 PDF를 사용하는 저장된 검토도 다시 열 수 없게 됩니다.'))return;
    setDeleting(document.id);
    setLibraryError('');
    try {
      await removePdf(document);
      for(const role of ['Ref','Target'] as Role[]) {
        if(currentSelections.current[role].document?.id===document.id)chooseExisting(role,null);
      }
      setSavedList(null);
      await refreshLibrary();
    } catch(e) {setLibraryError(message(e));}
    finally {setDeleting(null);}
  }
  function choosePage(role: Role, page: number) {
    invalidate(role);setSelections(previous=>({...previous,[role]:{...previous[role],page}}));
  }
  async function extract() {
    const roles=(['Ref','Target'] as Role[]).filter(role=>selections[role].document);
    if(!roles.length)return;
    activeRequest.current?.abort();const generation=++extractionGeneration.current;
    const controller=new AbortController();activeRequest.current=controller;setExtracting(true);setError(null);
    try {
      const loaded=await Promise.all(roles.map(async role=>{
        const selected=selections[role],old=currentImages.current[role];
        if(old&&old.document.id===selected.document!.id&&old.page===selected.page&&old.zoom===zoom)return [role,old] as const;
        return [role,await fetchPage(selected.document!,selected.page,zoom,controller.signal)] as const;
      }));
      if(generation!==extractionGeneration.current)return;
      const next={...currentImages.current};
      for(const [role,image] of loaded){
        if(image===next[role])continue;
        if(next[role]){URL.revokeObjectURL(next[role]!.url);imageUrls.current.delete(next[role]!.url);}
        const url=URL.createObjectURL(image.blob);imageUrls.current.add(url);next[role]={...image,url};
      }
      setExtracted(next);
    } catch(error) {
      if(generation===extractionGeneration.current){controller.abort();setError(message(error));}
    } finally {
      if(generation===extractionGeneration.current){setExtracting(false);activeRequest.current=null;}
    }
  }
  const onROIChange = useCallback((value:TargetROI|null) => {
    setTargetROI(value);
    setRestored(previous => previous && JSON.stringify(previous.comparison.target_roi.vertices)===JSON.stringify(value?.vertices)
      ? previous : null);
  },[]);
  async function listSaved() {
    try {
      setError(null);
      setSavedList(await (await reviewRequest(reviewPath)).json());
    } catch (e) {setError(message(e));}
  }
  async function openSaved(runId:string) {
    activeRequest.current?.abort();
    const generation = ++extractionGeneration.current;
    const controller = new AbortController();
    activeRequest.current = controller;
    setExtracting(true);
    setError(null);
    try {
      const saved: SavedComparison = await (await reviewRequest(reviewPath+'/'+runId,{signal:controller.signal})).json();
      const loaded = await Promise.all(saved.comparison.sources.map(async (source,i) => {
        const frame = source.image;
        const page = await fetchPage(saved.documents[i],frame.page,frame.extraction_zoom,controller.signal);
        if (page.id !== frame.image_id || page.width !== frame.width || page.height !== frame.height)
          throw Error('저장한 원본 이미지와 일치하지 않습니다.');
        return page;
      }));
      if (generation !== extractionGeneration.current) return;
      for (const old of Object.values(currentImages.current)) {
        if (old) {URL.revokeObjectURL(old.url); imageUrls.current.delete(old.url);}
      }
      const [Ref,Target] = loaded.map(image => {
        const url = URL.createObjectURL(image.blob);
        imageUrls.current.add(url);
        return {...image,url};
      });
      setExtracted({Ref,Target});
      setSelections({Ref:{...blank(),document:Ref.document,page:Ref.page},
                     Target:{...blank(),document:Target.document,page:Target.page}});
      setRefRun(null);
      setTargetRun(null);
      setZoom(Target.zoom);
      setTargetROI(saved.comparison.target_roi);
      setRestored(saved);
      setRestoreVersion(v=>v+1);
      setSavedList(null);
    } catch (e) {if (generation === extractionGeneration.current) setError(message(e));}
    finally {
      if (generation === extractionGeneration.current) {setExtracting(false); activeRequest.current=null;}
    }
  }
  const ready=Boolean((selections.Ref.document||selections.Target.document)&&!selections.Ref.uploading&&!selections.Target.uploading);
  useEffect(()=>{
    if(publicUpload&&workspaceReady&&ready)void extract();
  },[publicUpload,workspaceReady,selections.Ref.document?.id,selections.Target.document?.id,
    selections.Ref.page,selections.Target.page,zoom,ready]);
  const referenceAction=<section className="simple-action" aria-label="기준 설계도 준비">
    <button className="primary" disabled={comparing||extracting} onClick={()=>{setRestored(null);void comparison.understandReference();}}>
      {comparison.referencePhase?'기준 도면 이해 중…':comparison.reference?'Reference 다시 이해하기':'Reference 이해하기'}
    </button>
    <p role="status" data-reference-ready={Boolean(comparison.reference)}>
      {comparison.referencePhase || (comparison.reference?'기준 설계도 준비 완료':'기준 도면을 먼저 분석해 주세요.')}
    </p>
    {comparison.reference&&<p className="hint">{comparison.reference.labels.length
      ?'판독한 공간명: '+comparison.reference.labels.join(' · '):'판독한 공간명이 없습니다.'} 공간 경계와 연결 관계 전체가 확인된 것은 아닙니다.</p>}
    {comparison.referenceError&&<p role="alert" className="error">{comparison.referenceError}</p>}
  </section>;
  const comparisonAction = images && <section className="simple-action" aria-label="도면 비교 실행" data-analysis-state="on-demand" data-target-observation-state={observationState}>
    <button className="primary" onClick={runComparison} disabled={!comparison.reference||comparing||extracting}>
      {comparison.phase?'변경 사항 확인 중…':'차이점 찾기'}
    </button>
    <p className="hint">{targetROI?'선택한 영역을 비교합니다.':'범위 미지정 시 페이지 전체를 비교합니다. 여러 평면이 있으면 영역을 지정하세요.'}</p>
    {!comparison.reference&&!comparison.referencePhase&&<p className="hint">왼쪽의 Reference 이해하기를 먼저 눌러 주세요.</p>}
    {comparison.phase && <p role="status">{comparison.phase}</p>}
    {comparison.error && <p className="error" role="alert">{comparison.error}</p>}
  </section>;
  return <div className={publicUpload?'app-shell simple-user':'app-shell'}>
    {publicUpload&&<header className="product-header"><h1>FPUE · 설계도 변경 비교</h1>
      <p>기준 도면을 이해한 뒤 변경 위치와 설명을 두 도면에서 확인하세요.</p></header>}
    <aside className="inputs">
      {!publicUpload&&<h2>PDF 및 페이지 선택</h2>}
      {(["Ref","Target"] as Role[]).map(role=>{
        const selection=selections[role];
        return <section className="input-section" key={role}>
          <h3>{role==="Ref"?"사업승인도":"실시설계도"} · {role}</h3>
          {library&&library.documents.length>0&&<label className="stored-pdf">업로드한 PDF 선택
            <select aria-label={role+' 업로드한 PDF 선택'} value={selection.document?.id??''}
              disabled={!workspaceReady||Boolean(deleting)||selection.uploading}
              onChange={e=>chooseExisting(role,library.documents.find(d=>d.id===e.target.value)??null)}>
              <option value="">PDF를 선택하세요</option>
              {library.documents.map(d=><option key={d.id} value={d.id}>{d.filename} · {d.page_count}쪽</option>)}
            </select>
          </label>}
          <label className="upload-label">{role} PDF 업로드
            <span className="file-picker"><span>PDF 파일 선택</span>
            <input aria-label={`${role} PDF 업로드`} type="file" accept="application/pdf,.pdf" disabled={!workspaceReady}
              onChange={event=>{const file=event.target.files?.[0]??null;event.target.value="";void chooseFile(role,file);}}/>
            </span>
          </label>
          {selection.uploading&&<p role="status">PDF를 읽고 있습니다…</p>}
          {selection.error&&<p role="alert" className="error">{selection.error}</p>}
          {selection.document&&<>
            <div className="file-summary"><span>{selection.document.filename}</span>
              <button title="현재 도면 선택 해제" aria-label={`${role} PDF 제거`} onClick={()=>void chooseFile(role,null)}>×</button></div>
            <p className="muted">총 {selection.document.page_count}페이지</p>
            <label className="page-selector">{role} 페이지
              <select aria-label={`${role} 페이지`} value={selection.page} onChange={event=>choosePage(role,Number(event.target.value))}>
                {Array.from({length:selection.document.page_count},(_,index)=><option key={index+1} value={index+1}>{index+1} 페이지</option>)}
              </select>
            </label>
          </>}
        </section>;
      })}
      {workspaceReady&&<details className="pdf-library">
        <summary>업로드한 PDF 관리{library?' · '+library.usage.documents+'개':''}</summary>
        <button onClick={()=>void refreshLibrary()}>목록 새로고침</button>
        {library&&<p>{library.usage.documents}/{library.usage.max_documents}개 · {(library.usage.bytes/1024/1024).toFixed(1)} / {(library.usage.max_bytes/1024/1024).toFixed(0)} MB</p>}
        {library?.documents.length===0&&<p>업로드한 PDF가 없습니다.</p>}
        {libraryError&&<p role="alert">{libraryError}</p>}
        <ul>{library?.documents.map(d=><li key={d.id}>
          <span>{d.filename} · {(d.size_bytes/1024/1024).toFixed(1)} MB</span>
          <button disabled={Boolean(deleting)||comparing||extracting||selections.Ref.uploading||selections.Target.uploading}
            aria-label={d.filename+' 저장 PDF 삭제'} onClick={()=>void deleteStored(d)}>{deleting===d.id?'삭제 중…':'삭제'}</button>
        </li>)}</ul>
      </details>}
      {!publicUpload&&<details className="image-quality-settings"><summary>이미지 품질 설정</summary>
      <label>이미지 추출 배율
        <select aria-label="이미지 추출 배율" value={zoom} onChange={event=>{invalidate();setZoom(Number(event.target.value));}}>
          {[.5,1,1.5,2,2.5,3].map(value=><option value={value} key={value}>{value}배</option>)}
        </select>
      </label>
      <p className="hint">화면 확대와 별도의 이미지 품질입니다. 파일·페이지를 바꾸면 해당 도면의 작업이 초기화됩니다. 품질 변경은 두 도면 모두에 적용됩니다.</p>
      </details>}
      {!publicUpload&&<button className="primary" disabled={!ready||extracting} onClick={()=>void extract()}>
        {extracting?"페이지 추출 중…":selections.Ref.document&&selections.Target.document?"선택한 페이지 추출·나란히 보기":"선택한 페이지 추출"}
      </button>}
      {!publicUpload&&workspaceReady&&<div className="input-section">
        <button disabled={extracting || selections.Ref.uploading || selections.Target.uploading}
          onClick={()=>void listSaved()}>저장한 검토 열기</button>
        {savedList?.length===0&&<p role="status">이 작업공간에 복원할 저장본이 없습니다.</p>}
        {!!savedList?.length&&<ul>{savedList.map(saved=><li key={saved.run_id}>
          <button disabled={extracting} onClick={()=>void openSaved(saved.run_id)}>
            {saved.documents.map((d,i)=>d.filename+' '+saved.pages[i]+'쪽').join(' ↔ ')}
          </button>
        </li>)}</ul>}
      </div>}
    </aside>
    <main>
      {!publicUpload&&<><h1>FPUE 도면 비교</h1>
      <p className="intro">두 PDF의 페이지를 열고, 실시설계도(Target)에서 비교할 범위를 선택하세요.</p></>}
      {extracting&&<p role="status">선택한 페이지를 열고 있습니다…</p>}
      {!publicUpload&&(selections.Target.document||images)&&<ComparisonWorkflow pagesReady={Boolean(images)} roi={targetROI}/>}
      {error&&<p className="error notice" role="alert">{error}</p>}
      {(extracted.Ref||extracted.Target)?<><div id="fpue-drawings" className={images?"comparison":"single-drawing"} data-count={images?2:1} aria-label="선택한 도면">
        {extracted.Ref&&(publicUpload?<DrawingViewer
          key={`Ref:${extracted.Ref.document.id}:${extracted.Ref.id}:${restoreVersion}`} image={extracted.Ref} role="Ref"
          tools={referenceAction} overlay={alignment.refOverlay} focusRegion={alignment.refFocus} showDownload={false} technicalDetails={false}/>
          :<ReferenceUnderstandingViewer key={`Ref:${extracted.Ref.document.id}:${extracted.Ref.id}:${restoreVersion}`} image={extracted.Ref} onGeometryRun={setRefRun} alignmentOverlay={alignment.refOverlay} comparisonFocus={alignment.refFocus} technicalDetails={advancedOpen}/>)}
        {extracted.Target&&<TargetViewer key={`Target:${extracted.Target.document.id}:${extracted.Target.id}:${restoreVersion}`}
          image={extracted.Target} compact={publicUpload} simple={publicUpload} comparisonAction={publicUpload?comparisonAction:undefined}
          onROIChange={onROIChange} initialROI={restored?.comparison.target_roi} technicalDetails={advancedOpen}
          observationOverlay={alignment.targetOverlay} focusRegion={alignment.targetFocus}/>}
      </div>
      {publicUpload&&images&&alignment.summary}
      {!publicUpload&&restored&&<button onClick={()=>{setRestored(null);comparison.clear();}}>저장본 닫고 새 비교 준비</button>}
      {researchTools&&images&&<Suspense fallback={<p>고급 도구를 불러오고 있습니다…</p>}><details className="developer-tools" open={advancedOpen} onToggle={e=>setAdvancedOpen(e.currentTarget.open)}>
        <summary>고급 도구(개발자용)</summary>
        <div className="developer-tools-content">
          {!publicUpload&&alignment.panel}
          <p className="developer-tools-note">수동 구조 구간 입력·파일 저장, 구간 기반 정렬 검증과 기존 점 기반 정렬 실험을 위한 도구입니다. 자동 안정 구조 선정이 완성된 사용자 기능을 뜻하지 않습니다. 접기는 입력을 보존하며, 도면·비교 영역 변경에 따른 검증 무효화는 계속 적용됩니다.</p>
          <StructuralAnchorPanel key={`structural:${images[0].id}:${images[1].id}`} refImage={images[0]} targetImage={images[1]} roi={targetROI}/>
          <AlignmentPanel key={`${images[0].id}:${images[1].id}`} refImage={images[0]} targetImage={images[1]} roi={targetROI}/>
        </div>
      </details></Suspense>}</>:<div className="empty" role="status">
        <h2>{extracting?"선택한 두 페이지를 추출하고 있습니다":"설계도와 페이지를 선택하세요"}</h2>
        <p>PDF를 업로드하면 선택한 페이지가 바로 표시됩니다. Target을 추가하면 나란히 비교할 수 있습니다.</p>
      </div>}
    </main>
  </div>;
}
