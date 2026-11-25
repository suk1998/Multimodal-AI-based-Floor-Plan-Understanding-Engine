import {useEffect,useRef,useState} from 'react';
import type {NativeImage} from './api';
import type {TargetROI} from './roi';
import type {ObservationBundle} from './observations';
import type {GeometryBundle} from './geometricUnderstanding';
import {observationInput,resultFrameMatches} from './observations';
import {geometryMatches} from './geometricUnderstanding';
import {spatialMatches,pickSpatialSpace} from './spatialUnderstanding';
import type {SpatialBundle,SpatialRun} from './spatialUnderstanding';
import type {Inspection} from './DrawingViewer';
const states:Record<string,string>={NOT_RUN:'실행 전',QUEUED:'순서 대기',RUNNING:'공간 분석 중',COMPLETED:'후보 생성 완료',PARTIAL:'분석 불완전 · 부분 후보',FAILED:'공간 분석 실패',STALE:'이전 공간 결과 유지 · 재분석 필요'};
const boundaries:Record<string,string>={CLOSED_CANDIDATE:'조건부 폐합 후보',PARTIAL_BOUNDARY:'부분 경계',OPEN_BOUNDARY:'열린 경계',AMBIGUOUS:'분할 불확실',UNRESOLVED:'영역 복원 실패'};
const reasonText=(reason:string)=>({MULTIPLE_OCR_LABELS_SHARE_ONE_COMPONENT_POSSIBLE_MISSING_WALL_OR_UNSUPPORTED_SEPARATOR:'여러 공간명이 같은 영역에 섞였습니다. 누락된 벽이나 미확인 개구부를 검토해야 합니다.',REGION_DEPENDS_ON_GEOMETRY_WITH_CONTRADICTING_EVIDENCE:'반대 근거가 있는 경계에 따라 영역이 달라집니다.',SPACE_REACHES_CONTEXT_EDGE_NO_CLOSURE_INVENTED:'공간이 문맥 이미지 밖으로 이어져 경계를 닫지 않았습니다.',SPACE_EXTENDS_OUTSIDE_CORE_ROI_NOT_CLOSED_AT_ROI_EDGE:'공간이 ROI 밖으로 이어져 부분 경계로 남겼습니다.',POLYGON_RECONSTRUCTION_INVALID_OR_EMPTY:'유효한 공간 Polygon을 복원하지 못했습니다.',NO_LOCAL_BOUNDARY_GEOMETRY_ASSOCIATED:'Seed 주변의 경계 근거가 부족합니다.',NO_WHITELIST_OCR_NAME_UNLABELED_GEOMETRY_SPACE_HYPOTHESIS:'공간명이 없는 기하 영역 후보입니다.'} as Record<string,string>)[reason]||reason;
export function useSpatialUnderstanding(image:NativeImage,roi:TargetROI|null,observation:ObservationBundle|null,geometry:GeometryBundle|null){
 const [bundle,setBundle]=useState<SpatialBundle|null>(null),[phase,setPhase]=useState('NOT_RUN'),[error,setError]=useState(''),[selected,setSelected]=useState<string|null>(null),[pick,setPick]=useState(true);
 const [layers,setLayers]=useState({seeds:true,boundaries:true,regions:true});
 const generation=useRef(0),abort=useRef<AbortController|null>(null),busy=useRef(false);
 const sourceKey=JSON.stringify([image.document.id,image.id]),key=JSON.stringify([sourceKey,roi?.vertices,observation?.run_id,geometry?.run_id]);
 useEffect(()=>{setBundle(null);setPhase('NOT_RUN');setError('');setSelected(null)},[sourceKey]);
 useEffect(()=>{generation.current++;abort.current?.abort();busy.current=false;setPhase('NOT_RUN');setError('');return()=>{generation.current++;abort.current?.abort()}},[key]);
 const current=bundle&&resultFrameMatches(bundle,image)?bundle:null;
 const ready=Boolean(geometry&&observation&&geometryMatches(geometry,observation,image,roi));
 const stale=Boolean(current&&!spatialMatches(current,geometry,observation,image,roi));
 const displayPhase=['QUEUED','RUNNING','FAILED'].includes(phase)?phase:stale?'STALE':current?.status||phase;
 async function run(){
  if(!ready||!geometry||!observation||!roi||busy.current)return;const controller=new AbortController(),own=++generation.current;abort.current?.abort();abort.current=controller;busy.current=true;setError('');setPhase('QUEUED');
  async function request(url:string,options:RequestInit={}):Promise<SpatialRun>{const r=await fetch(url,{...options,signal:controller.signal});if(!r.ok){const data=await r.json().catch(()=>({}));throw Error(typeof data.detail==='string'?data.detail:'공간 요청 실패')}return r.json()}
  try{
   let job=await request('/api/spatials',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({...observationInput(image,roi),observation_run_id:observation.run_id,geometry_run_id:geometry.run_id})});const deadline=Date.now()+760000;
   while(['QUEUED','RUNNING'].includes(job.status)){if(own!==generation.current)return;setPhase(job.status);if(Date.now()>deadline)throw Error('공간 분석 대기 시간이 초과됐습니다.');await new Promise(r=>setTimeout(r,800));job=await request('/api/spatials/'+job.run_id)}
   if(own!==generation.current||controller.signal.aborted)return;if(job.status==='FAILED'||!job.result)throw Error(job.failure?.reason||'공간 분석 실패');if(!spatialMatches(job.result,geometry,observation,image,roi))throw Error('공간 결과가 현재 PDF·ROI·Geometry와 다릅니다.');setBundle(job.result);setSelected(null);setPhase(job.status);setPick(true);
  }catch(e){if(own===generation.current&&!controller.signal.aborted){setError(e instanceof Error?e.message:'공간 분석 실패');setPhase('FAILED')}}finally{if(own===generation.current)busy.current=false;}
 }
 const visible=current?.spaces.slice(0,500)||[],chosen=current?.spaces.find(s=>s.space_id===selected);
 const connections=(current?.relations||[]).filter(r=>r.relation_type==='Connection');
 const spaceName=(id:string)=>current?.spaces.find(s=>s.space_id===id)?.space_name||'공간 미확인';
 const inspection:Inspection|undefined=current&&pick?{panOnDrag:true,selectionKey:selected,onPick:p=>{setSelected(pickSpatialSpace([p.x,p.y],visible)?.space_id||null)}}:undefined;
 function pickAt(p:{x:number;y:number}){if(!inspection)return false;const hit=pickSpatialSpace([p.x,p.y],visible);setSelected(hit?.space_id||null);return !!hit}
 function save(){if(!current)return;const url=URL.createObjectURL(new Blob([JSON.stringify(current)],{type:'application/json'})),a=document.createElement('a');a.href=url;a.download=`target_page_${image.page}_spatial.json`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000)}
 const overlay=current&&<g className="architectural-spatial" data-frame-id={current.frame_id} data-results-stale={stale}>{visible.map(space=>{
  const color=space.space_id===selected?'#ef301c':space.boundary_status==='CLOSED_CANDIDATE'?'#087c58':space.boundary_status==='AMBIGUOUS'?'#a16a00':'#1559ab';
  return <g key={space.space_id} data-space-id={space.space_id} data-space-name={space.space_name} data-boundary-status={space.boundary_status} opacity={space.roi_intersection?1:.4}>
   <title>{space.space_name+' · '+boundaries[space.boundary_status]+' · PROPOSED'}</title>
   {layers.regions&&space.polygon_native&&<path className="spatial-region" d={[...space.polygon_native.outer_rings,...space.polygon_native.holes].map(r=>'M'+r.map(p=>p.join(',')).join('L')+'Z').join(' ')} fill={color} fillOpacity={.13} fillRule="evenodd" stroke={color} strokeWidth={1.5} vectorEffect="non-scaling-stroke"/>}
   {layers.boundaries&&space.boundary_segments_native.map((segment,i)=><polyline className="spatial-boundary" key={i} points={segment.points_native.map(p=>p.join(',')).join(' ')} fill="none" stroke={color} strokeWidth={space.space_id===selected?3:2} strokeDasharray={segment.kind.includes('CONDITIONAL')?'5 4':undefined} vectorEffect="non-scaling-stroke"/>)}
   {layers.seeds&&space.seed_native&&<g className="spatial-seed"><circle cx={space.seed_native[0]} cy={space.seed_native[1]} r={7} fill={color} stroke="white" strokeWidth={1.5} vectorEffect="non-scaling-stroke"/><text x={space.seed_native[0]+9} y={space.seed_native[1]-9} fill={color} className="spatial-label">{space.space_name==='UNLABELED_SPACE'?'이름 미확인':space.space_name}</text></g>}
  </g>;
 })}</g>;
 const tools=<section className="spatial-panel" aria-label="공간 이해 후보"><h3>공간 이해 후보</h3>
  <div className="observation-actions"><button onClick={()=>void run()} disabled={!ready||['QUEUED','RUNNING'].includes(phase)}>Spatial 분석 실행</button>{current&&<button onClick={save}>Spatial JSON 저장</button>}<span role="status" data-spatial-state={displayPhase}>{states[displayPhase]}</span></div>
  {!ready&&<p>현재 ROI의 Observation과 Geometry 분석을 먼저 완료하세요.</p>}{stale&&<p className="analysis-stale-notice" role="status">이전 공간 결과는 원래 위치에 유지합니다. 현재 ROI의 Geometry를 생성한 뒤 Spatial을 다시 실행하세요. 저장 JSON은 이전 분석 범위입니다.</p>}{error&&<p role="alert" className="error">{error}</p>}
  {current&&<>
   <div className="spatial-layers">{Object.entries({seeds:'Space Seed',boundaries:'Space Boundary',regions:'Space Region'}).map(([key,label])=><label key={key}><input type="checkbox" checked={layers[key as keyof typeof layers]} onChange={e=>setLayers(v=>({...v,[key]:e.target.checked}))}/>{label}</label>)}</div>
   {current.ontology&&<div className="spatial-ontology-status"><p>출입 연결 후보 {connections.length}개 · 검토 전 가설</p>{connections.length===0&&<p>문과 양쪽 공간을 연결할 근거가 충분하지 않습니다. 연결 없음으로 판정한 것은 아닙니다.</p>}{connections.map(r=><p key={r.relation_id}>{spaceName(r.source_id)} ↔ {spaceName(r.target_id)} · {r.geometry_status==='SUPPORTED'?'기하 조건 지지 · 의미 검토 필요':'부분 근거 · 불확실'}</p>)}<details><summary>관계 검토 근거</summary><pre>{JSON.stringify({ontology:current.ontology,relations:current.relations,attempts:current.relation_attempts},null,2)}</pre></details></div>}
   <p>공간명 OCR {current.summary.OCR_space_name_candidates} · Seed {current.summary.space_seeds} · Polygon 영역 {current.summary.space_region_polygons}</p>
   {current.summary.space_region_polygons===0&&<p role="status">실제 공간 Polygon을 복원하지 못했습니다. Seed와 부분 경계의 검토만 가능합니다.</p>}
   <p>영역과 공간명은 검토 전 가설(PROPOSED)입니다. 텍스트 박스는 공간 경계가 아닙니다. 도면 오른쪽이 +X, 아래쪽이 +Y이며 실제 방위와 m² 면적은 미확인입니다.</p>
   <label><input type="checkbox" checked={pick} onChange={e=>setPick(e.target.checked)}/>공간 후보 확인</label><p>공간 내부 또는 Seed 클릭: 공간 근거. 공간이 선택되지 않는 위치에서는 기존 기하 후보를 확인합니다. 드래그는 도면 이동입니다.</p>
   <ul className="spatial-query-summary">{current.summary.queries.map(q=><li key={q.space_name}>{q.space_name}: {q.OCR_candidate_count?'문자 '+q.OCR_candidate_count+' · Seed '+q.seed_count+' · Polygon '+q.region_polygon_count:'공간명 OCR 미검출 · 존재 여부 미확정'}</li>)}</ul>
   {chosen&&<article className="spatial-detail" data-spatial-selection={chosen.space_id}><h4>{chosen.space_name==='UNLABELED_SPACE'?'이름 미확인 공간':chosen.space_name} · {boundaries[chosen.boundary_status]}</h4>
    <p>Seed: {chosen.seed_native?.map(v=>v.toFixed(2)).join(', ')||'미확정'} px · 경계 근거 {chosen.boundary_geometry_refs.length}개{chosen.area_native_px2!=null?' · 후보 면적 '+chosen.area_native_px2.toFixed(1)+' px²':''}</p>
    <p>{chosen.boundary_truncated?'ROI 또는 Context 밖으로 이어지는 부분 공간입니다. ':''}{chosen.polygon_native?'조건부 기하 경계에서 폐합했으며 실제 공간 정답을 검증한 것은 아닙니다.':'공간 Polygon 미복원.'}</p>
    <ul>{chosen.uncertainty_reason.filter(r=>r!=='ALL_GEOMETRY_AND_SPATIAL_TYPES_UNVERIFIED_PROPOSED').map(r=><li key={r}>{reasonText(r)}</li>)}</ul>
    <details><summary>상세 공간 근거</summary><pre>{JSON.stringify({space:chosen,evidence:current.evidence.filter(e=>chosen.evidence_refs.includes(e.evidence_id))},null,2)}</pre></details>
   </article>}
  </>}
 </section>;
 return {tools,overlay,inspection,pickAt};
}
