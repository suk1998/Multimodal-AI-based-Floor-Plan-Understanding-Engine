import type {Vertex} from './roi';
import type {FocusRegion} from './DrawingViewer';

interface Region {
 status:string;
 polygon_native:{outer_rings:Vertex[][];holes:Vertex[][]}|null;
 boundary_runs:{points_native:Vertex[];owner:number}[];
}
interface NamedRegion extends Region {
 observation_id:string;name:string;seed_native:Vertex;
}
export interface SpaceClosure {
 frame_id:string;status:string;regions:NamedRegion[];unlabelled_regions:Region[];
}
export function PartitionOverlay({result}:{result:SpaceClosure}){
 return <g data-space-closure={result.frame_id} pointerEvents="none">
  {[...result.regions,...result.unlabelled_regions].map((r,i)=><g key={i} data-partition-state={r.status}>
   <title>{'name' in r?String(r.name):'무라벨 영역 후보'}</title>
   {r.polygon_native&&<path fill={r.status==='UNLABELLED_REGION_PROPOSED'?'#75829522':'#19806022'} fillRule="evenodd"
    d={[...r.polygon_native.outer_rings,...r.polygon_native.holes].map(ring=>'M'+ring.map(p=>p.join(',')).join(' L')+' Z').join(' ')}/>}
   {r.boundary_runs.map((run,j)=><polyline key={j} points={run.points_native.map(p=>p.join(',')).join(' ')}
     stroke={run.owner<0?'#ac4f00':r.status==='PARTIAL_BOUNDARY'?'#66519a':'#087464'} strokeDasharray={run.owner<0?'5 3':undefined}
     fill="none" strokeWidth={2} vectorEffect="non-scaling-stroke"/>)}
  </g>)}
 </g>
}
export function PartitionReview({result,onFocus}:{result:SpaceClosure;onFocus:(f:FocusRegion)=>void}){
 const count=result.regions.filter(r=>r.polygon_native).length;
 return <section aria-label="분절 폐합 결과">
  <h3>공간 복원 · 미검증 후보</h3>
  {result.status==='NOT_COMPUTED_RESOURCE_BUDGET'?<p>분석량 한도를 초과했습니다. 더 작은 도면 범위를 선택하세요.</p>:<>
   <p>이름이 있는 영역 {count}개 · 부분 경계 또는 미해결 {result.regions.length-count}개 · 무라벨 영역 후보 {result.unlabelled_regions.length}개</p>
   <p>청록은 관찰로 지지된 경계, 주황 점선은 추론으로 연결한 구간, 보라는 열린 경계입니다.</p>
   <div className="reference-space-list">{result.regions.map(r=><button key={r.observation_id} onClick={()=>{
    const points=[r.seed_native,...r.boundary_runs.flatMap(b=>b.points_native)];
    const xs=points.map(p=>p[0]),ys=points.map(p=>p[1]);
    onFocus({key:r.observation_id+Date.now(),bounds:[Math.min(...xs)-32,Math.min(...ys)-32,Math.max(...xs)+32,Math.max(...ys)+32]});
   }}>{r.name} · {r.polygon_native?'폐합 후보':r.boundary_runs.length?'부분 경계':'미해결'}</button>)}</div>
  </>}
 </section>
}
