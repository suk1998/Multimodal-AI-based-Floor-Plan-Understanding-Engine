import type {DrawingRegionSelection} from './observationAudit.ts';
import type {NativeImage} from './api.ts';
import type {Vertex} from './roi.ts';
export interface ReferenceCase {
 id:string;label:{name:string;seed:Vertex;observation:{polygon_native:Vertex[]}};
 after:{status:string;polygon_native:Vertex[]|null;bbox_native?:number[];reason?:string;edges?:{points_native?:Vertex[];side:string;observed_fraction:number;axis:"H"|"V";normal:number;observed_intervals:[number,number][];inferred_intervals:[number,number][]}[]};
 full_space_extent_verified:boolean;
}
export interface ReferenceBundle {
 schema_version:'reference_understanding_bundle/1'|'target_understanding_bundle/1';run_id:string;source_role:'Ref'|'Target';frame_id:string;status:string;
 source_binding:{document_id:string;image_id:string;source_role:string;source_sha256:string;page:number;extraction_zoom:number;width:number;height:number};
 observation_bundle?:{run_id:string;frame_id:string;source_binding:{core_bbox_native:number[]}};
 cases:ReferenceCase[];summary:Record<string,number>;coverage:Record<string,unknown>;
 components:Record<string,{status:string;reason?:string|null;regions?:number}>;
 architectural_gate_passed:boolean;
}
export interface ReferenceRun {run_id:string;status:string;result:ReferenceBundle|null;failure:{reason:string}|null}
export function referenceInput(image:NativeImage,selection?:DrawingRegionSelection,role:'Ref'|'Target'='Ref'){
 return {...(selection?{drawing_region_selection:selection}:{}),document_id:image.document.id,source_role:role,page:image.page,extraction_zoom:image.zoom,
 image:{source_sha256:image.document.sha256,image_id:image.id,frame_id:role+':'+image.id,
 pdf_frame_id:`pdf:${image.document.sha256}:page:${image.page}:pymupdf_unrotated_local`,width:image.width,height:image.height,extraction_zoom:image.zoom}};
}
export function referenceMatches(bundle:ReferenceBundle,image:NativeImage,role:'Ref'|'Target'='Ref'){
 const b=bundle.source_binding;
 return bundle.schema_version===(role==='Ref'?'reference_understanding_bundle/1':'target_understanding_bundle/1')&&bundle.source_role===role&&b.source_role===role&&bundle.frame_id===role+':'+image.id&&
 b.document_id===image.document.id&&b.image_id===image.id&&b.source_sha256===image.document.sha256&&b.page===image.page&&b.extraction_zoom===image.zoom&&b.width===image.width&&b.height===image.height;
}

// These intervals describe observation support versus inferred closure, not Wall truth.
export function referenceBoundarySegments(candidate:ReferenceCase){
 return (candidate.after.edges||[]).flatMap(edge=>(['observed_intervals','inferred_intervals'] as const).flatMap(kind=>edge[kind].map(interval=>({
  inferred:kind==='inferred_intervals',points:(edge.axis==='H'?[[interval[0],edge.normal],[interval[1],edge.normal]]:[[edge.normal,interval[0]],[edge.normal,interval[1]]]) as Vertex[]
 }))));
}
export function referenceFocus(candidate:ReferenceCase,image:Pick<NativeImage,'width'|'height'>):[number,number,number,number]{
 const polygon=candidate.after.polygon_native||candidate.label.observation.polygon_native;
 const xs=polygon.map(p=>p[0]),ys=polygon.map(p=>p[1]),margin=40;
 return [Math.max(0,Math.min(...xs)-margin),Math.max(0,Math.min(...ys)-margin),Math.min(image.width,Math.max(...xs)+margin),Math.min(image.height,Math.max(...ys)+margin)];
}
