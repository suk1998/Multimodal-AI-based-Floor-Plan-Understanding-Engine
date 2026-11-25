import type {NativeImage} from './api';
import type {TargetROI,Vertex} from './roi.ts';
import {isRectangle,rectangleBounds} from './rectangleROI.ts';
import {readROI} from './roi.ts';
export interface Observation {observation_id:string;source_role:'Target';frame_id:string;observation_type:string;geometry_native:{type:string;points_native:Vertex[];trace_closed:boolean};source_evidence:Record<string,unknown>;measurements:Record<string,unknown>;confidence:number|null;core_roi_intersection:boolean;boundary_truncated:boolean;claim_status:string}
export interface ObservationBundle {schema_version:string;run_id:string;frame_id:string;status:string;source_binding:{document_id:string;image_id:string;core_bbox_native:number[]};analysis_scope:{core_roi:{bbox_native:number[]};context:{bbox_native:number[];requested_margin_px:number}};components:Record<string,{status:string;reason?:string;regions?:number}>;observations:Observation[];coverage:{observation_counts:Record<string,number>;omitted_due_to_limits:Record<string,number>};policy:Record<string,boolean>}
export interface ObservationRun {run_id:string;document_id:string;status:string;result:ObservationBundle|null;failure:{reason:string}|null}
export function observationInput(image:NativeImage,roi:TargetROI){
 readROI(JSON.stringify(roi),image);
 if(!isRectangle(roi.vertices))throw Error('직사각형 보정을 선택한 뒤 분석하세요.');
 return {document_id:image.document.id,source_role:'Target',page:image.page,extraction_zoom:image.zoom,roi_bbox_native:rectangleBounds(roi.vertices),context_margin_px:48,
   image:{source_sha256:image.document.sha256,image_id:image.id,pdf_frame_id:`pdf:${image.document.sha256}:page:${image.page}:pymupdf_unrotated_local`,frame_id:'Target:'+image.id,width:image.width,height:image.height,extraction_zoom:image.zoom}};
}
// A completed snapshot remains drawable on its original image after ROI edits.
// Exact ROI matching is a separate requirement for running downstream analysis.
export function resultFrameMatches(bundle:Pick<ObservationBundle,'frame_id'|'source_binding'>,image:NativeImage){
 return bundle.frame_id==='Target:'+image.id&&bundle.source_binding.document_id===image.document.id&&bundle.source_binding.image_id===image.id;
}
export function bundleMatches(bundle:ObservationBundle,image:NativeImage,roi:TargetROI|null){
 if(!roi||!isRectangle(roi.vertices))return false;try{readROI(JSON.stringify(roi),image)}catch{return false}
 return resultFrameMatches(bundle,image)&&JSON.stringify(bundle.analysis_scope.core_roi.bbox_native)===JSON.stringify(rectangleBounds(roi.vertices));
}
export function observationLayer(type:string):string {
 if(type==='TextDetection')return 'detections';if(type==='PdfText')return 'pdftext';if(type==='TextRegion')return 'text';if(type==='ParallelBoundaries')return 'parallel';if(['PdfCurve','RasterContour'].includes(type))return 'curves';if(['Endpoint','BandContact'].includes(type))return 'points';if(['PdfPath','PdfRectangle'].includes(type))return 'paths';return 'lines';
}
