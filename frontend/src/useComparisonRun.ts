import {useEffect, useRef, useState} from 'react';
import type {NativeImage} from './api';
import type {TargetROI} from './roi';
import {referenceInput} from './referenceUnderstanding';
import {reviewRequest, type SavedComparison} from './useObjectComparison';

interface Job {
  run_id: string;
  status: string;
  binding: {frame_id: string; document_id: string; source_role:string; source_sha256:string; image_id:string; page:number; extraction_zoom:number; width:number; height:number};
  observed_space_names?:string[];
  failure?: {reason: string};
}
interface Source {document_id:string; frame_id:string; geometry_run_id:string}
interface ReferenceMemory {key:string; source:Source; labels:string[]; observation_run_id:string}
const imageKey = (image:NativeImage|null) => JSON.stringify(image && [
  image.document.id,image.document.sha256,image.id,image.page,image.zoom,image.width,image.height
]);

async function requestJob(endpoint: string, input: object, signal: AbortSignal): Promise<Job> {
  let job: Job = await (await reviewRequest(endpoint, {
    method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(input), signal
  })).json();
  const deadline = Date.now() + 760_000;
  while (job.status === 'QUEUED' || job.status === 'RUNNING') {
    if (Date.now() > deadline) throw Error('분석 대기 시간이 초과됐습니다.');
    await new Promise(resolve=>setTimeout(resolve,1100));
    signal.throwIfAborted();
    job = await (await reviewRequest(endpoint+'/'+job.run_id+'?summary=1',{signal})).json();
  }
  if (!['PARTIAL','COMPLETED'].includes(job.status))
    throw Error(job.failure?.reason ?? '분석 작업이 실패했습니다.');
  return job;
}
async function analyze(role:'Ref'|'Target', image:NativeImage, signal:AbortSignal) {
  const input = referenceInput(image,undefined,role), reference = role === 'Ref';
  const endpoint = reference?'/api/reference-understandings':'/api/target-understandings';
  const observation = await requestJob(endpoint,input,signal);
  const geometry = await requestJob('/api/geometries',{
    ...input,[reference?'reference_run_id':'target_run_id']:observation.run_id
  },signal);
  if (geometry.binding.frame_id !== role+':'+image.id || geometry.binding.document_id !== image.document.id)
    throw Error('업로드한 원본과 분석 좌표가 일치하지 않습니다.');
  return {source:{document_id:image.document.id,frame_id:geometry.binding.frame_id,geometry_run_id:geometry.run_id},
    observation_run_id:observation.run_id};
}

// The memory points to the existing server-side geometry/understanding model; it is
// scoped to this exact Reference frame, never to the Target page or its ROI.
export function useComparisonRun(ref: NativeImage|null, target: NativeImage|null, roi: TargetROI|null) {
  const refKey = imageKey(ref), key = JSON.stringify([refKey,imageKey(target),roi?.vertices]);
  const [memory,setMemory] = useState<ReferenceMemory|null>(null);
  const [referencePhase,setReferencePhase] = useState('');
  const [referenceError,setReferenceError] = useState('');
  const [result,setResult] = useState<{key:string; snapshot:SavedComparison}|null>(null);
  const [phase,setPhase] = useState('');
  const [error,setError] = useState('');
  const referenceRequest = useRef<AbortController|null>(null);
  const controller = useRef<AbortController|null>(null);
  const currentMemory = memory?.key===refKey?memory:null;
  useEffect(()=>{
    setMemory(null);setReferencePhase('');setReferenceError('');
    return ()=>{referenceRequest.current?.abort();referenceRequest.current=null;};
  },[refKey]);
  useEffect(()=>{
    setResult(null);setPhase('');setError('');
    return ()=>{controller.current?.abort();controller.current=null;};
  },[key]);

  async function understandReference() {
    if (!ref || referenceRequest.current) return;
    controller.current?.abort();controller.current=null;setPhase('');setResult(null);setError('');
    const request=new AbortController();referenceRequest.current=request;
    setMemory(null);setReferenceError('');setReferencePhase('기준 설계도를 분석하고 있습니다…');
    try {
      const analyzed=await analyze('Ref',ref,request.signal);
      const observation:Job = await (await reviewRequest(
        '/api/reference-understandings/'+analyzed.observation_run_id+'?summary=1',{signal:request.signal})).json();
      const binding=observation.binding;
      if (binding.document_id!==ref.document.id||binding.source_role!=='Ref'||
          Object.entries(referenceInput(ref).image).some(([key,value])=>binding[key as keyof typeof binding]!==value))
        throw Error('기준 도면의 원본과 분석 결과가 일치하지 않습니다.');
      const labels=[...new Set(observation.observed_space_names??[])];
      if(!request.signal.aborted)setMemory({key:refKey,...analyzed,labels});
    } catch(e) {
      if(!request.signal.aborted)setReferenceError(e instanceof Error?e.message:'기준 설계도를 분석하지 못했습니다.');
    } finally {
      if(referenceRequest.current===request){referenceRequest.current=null;setReferencePhase('');}
    }
  }
  async function run() {
    if (!ref || !target || !roi || !currentMemory || controller.current || referenceRequest.current) return;
    const request = new AbortController();controller.current = request;
    const {signal} = request;
    setError('');setResult(null);setPhase('변경 사항을 확인하고 있습니다…');
    try {
      // Job caches expire. A stale memory must not silently re-run Reference or be
      // used as if its evidence still exists.
      const cached=await fetch('/api/geometries/'+currentMemory.source.geometry_run_id+'?summary=1',{signal});
      if(cached.status===404||cached.status===410){setMemory(null);throw Error('기준 설계도의 분석 보관 시간이 지났습니다. Reference 이해하기를 다시 눌러 주세요.');}
      if(!cached.ok)throw Error('기준 설계도의 분석을 확인하지 못했습니다. 잠시 후 다시 시도해 주세요.');
      const verified:Job=await cached.json();
      if(verified.binding.frame_id!==currentMemory.source.frame_id||verified.binding.document_id!==ref.document.id)
        throw Error('기준 설계도의 원본과 보관된 분석이 일치하지 않습니다.');
      const Ref = currentMemory.source;
      const {source:Target} = await analyze('Target',target,signal);
      const response = await reviewRequest('/api/structural-comparisons?workflow=auto',{
        method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({Ref,Target,target_roi:roi}), signal
      });
      const comparison: SavedComparison['comparison'] = await response.json();
      if (comparison.sources[0]?.frame_id !== Ref.frame_id || comparison.sources[1]?.frame_id !== Target.frame_id
          || JSON.stringify(comparison.target_roi.vertices) !== JSON.stringify(roi.vertices))
        throw Error('비교 결과의 도면이나 범위가 변경됐습니다.');
      if (!signal.aborted) setResult({key,snapshot:{comparison,documents:[ref.document,target.document],
        revision:comparison.review_revision ?? 0,decisions:comparison.review_decisions ?? {}}});
    } catch (e) {
      if (!signal.aborted) setError(e instanceof Error?e.message:'비교를 완료하지 못했습니다.');
    } finally {
      if (controller.current === request) {controller.current=null;setPhase('');}
    }
  }
  return {run,phase,error,understandReference,referencePhase,referenceError,reference:currentMemory,
    snapshot:result?.key===key?result.snapshot:null,clear:()=>setResult(null)};
}
