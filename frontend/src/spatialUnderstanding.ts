import type {NativeImage} from './api';
import type {TargetROI,Vertex} from './roi';
import type {ObservationBundle} from './observations';
import type {GeometryBundle} from './geometricUnderstanding';
import {geometryMatches} from './geometricUnderstanding.ts';
import {pickSpace} from './studioApi.ts';
import type {Space} from './studioApi';
export interface SpatialSpace {space_id:string;source_role:'Target';frame_id:string;space_name:string;seed_native:Vertex|null;polygon_native:{outer_rings:Vertex[][];holes:Vertex[][]}|null;bbox_native:[number,number,number,number]|null;centroid_native:Vertex|null;area_native_px2:number|null;boundary_geometry_refs:string[];boundary_status:'CLOSED_CANDIDATE'|'PARTIAL_BOUNDARY'|'OPEN_BOUNDARY'|'AMBIGUOUS'|'UNRESOLVED';boundary_segments_native:{geometry_id:string;points_native:Vertex[];kind:string}[];partial_boundary_extent_native:[number,number,number,number]|null;evidence_refs:string[];name_evidence_refs:string[];name_observation:{ocr_bbox_native:number[];text_center_native:Vertex;ocr_confidence:number|null;raw_text:string}|null;uncertainty_reason:string[];claim_status:string;roi_intersection:boolean;boundary_truncated:boolean}
export interface SpatialRelation {relation_id:string;relation_type:string;source_id:string;target_id:string;via_element_id?:string;mode?:string;geometry_status:string;claim_status:string;evidence_refs:string[];uncertainty_reasons?:string[]}
export interface SpatialBundle {schema_version:string;run_id:string;frame_id:string;source_binding:ObservationBundle['source_binding'];analysis_scope:ObservationBundle['analysis_scope'];parent_geometry_run_id:string;parent_observation_run_id:string;status:string;spaces:SpatialSpace[];relations?:SpatialRelation[];ontology?:{rule_version:string;rule_sha256:string;counts:Record<string,number>};relation_attempts?:{kind:string;status:string;reasons?:string[]}[];summary:{OCR_space_name_candidates:number;space_seeds:number;space_region_polygons:number;boundary_status_counts:Record<string,number>;unlabeled_space_candidates:number;queries:{space_name:string;OCR_candidate_count:number;seed_count:number;region_polygon_count:number;boundary_statuses:string[]}[]};evidence:{evidence_id:string;[key:string]:unknown}[];coverage:Record<string,unknown>;diagnostics:Record<string,unknown>;policy:Record<string,boolean>}
export interface SpatialRun {run_id:string;status:string;result:SpatialBundle|null;failure:{reason:string}|null}
export function spatialMatches(s:SpatialBundle,g:GeometryBundle|null,o:ObservationBundle|null,image:NativeImage,roi:TargetROI|null){return Boolean(g&&o&&geometryMatches(g,o,image,roi)&&s.parent_geometry_run_id===g.run_id&&s.parent_observation_run_id===o.run_id&&s.frame_id===g.frame_id&&s.source_binding.document_id===image.document.id&&JSON.stringify(s.analysis_scope.core_roi.bbox_native)===JSON.stringify(g.analysis_scope.core_roi.bbox_native))}
export function pickSpatialSpace(point:Vertex,spaces:SpatialSpace[]):SpatialSpace|null{
 // Existing polygon/hole hit testing. Partial spaces are selectable near their
 // seed only, never by a text/camera bounding rectangle or a 130px default radius.
 const eligible=spaces.filter(s=>s.polygon_native||(s.seed_native&&Math.hypot(s.seed_native[0]-point[0],s.seed_native[1]-point[1])<=9));
 const adapted=eligible.map(s=>({id:s.space_id,role:s.source_role,name:s.space_name,seed:s.seed_native,focus_bounds:s.bbox_native||s.partial_boundary_extent_native||(s.seed_native?[s.seed_native[0]-1,s.seed_native[1]-1,s.seed_native[0]+1,s.seed_native[1]+1]:null),polygon:s.polygon_native,status:s.boundary_status} as Space));
 const selected=pickSpace(point,adapted);return spaces.find(s=>s.space_id===selected?.id)||null;
}
