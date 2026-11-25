import type {NativeImage} from './api';
import type {Vertex,TargetROI} from './roi';
import type {ObservationBundle} from './observations';
import {bundleMatches} from './observations.ts';
import {pickCandidate} from './studioApi.ts';
import type {Candidate} from './studioApi';
export interface GeometricEntity {
 entity_id:string;source_role:'Ref'|'Target';frame_id:string;element_type:'Wall'|'Door'|'Window'|'Opening'|'Unknown';
 geometry_native:{bbox_native:[number,number,number,number];polygons_native?:Vertex[][];polylines_native?:Vertex[][];points_native?:Vertex[]};
 geometry_properties:{architectural_admissibility?:{decision:string};classification:string;orientation?:string;thickness_estimate_px?:number|null;thickness_interval_px?:number[];opening_width_px?:number;window_track_count?:number;physical_endpoints_status:string;[key:string]:unknown};
 observation_refs:string[];evidence_refs:string[];confidence:number|null;claim_status:string;evidence_status:string;uncertainty_reason:string[];roi_intersection:boolean;boundary_truncated:boolean;
}
interface StructuralAssessment {
 structural_parents:{parent_structure_id:string;fragments:{decision:string;reasons:string[];boundary_faces_native:Vertex[][];centerline_native:Vertex[]}[]}[];
 openings:{source_geometry_entity_id:string;reasons:string[];decision:string}[];
 summary:{fragment_decisions:Record<string,number>;opening_decisions:Record<string,number>};
}
export interface GeometryBundle {target_model?:import('./ReferenceModel').ReferenceModel;target_relations?:import('./ReferenceRelations').ReferenceRelations;target_domains?:import('./ReferenceDomains').ReferenceDomains;reference_model?:import('./ReferenceModel').ReferenceModel;reference_relations?:import('./ReferenceRelations').ReferenceRelations;reference_domains?:import('./ReferenceDomains').ReferenceDomains;space_closure?:import('./SpaceClosure').SpaceClosure;architectural_assessment?:StructuralAssessment;schema_version:string;run_id:string;parent_observation_run_id:string;frame_id:string;source_binding:ObservationBundle['source_binding'];analysis_scope:ObservationBundle['analysis_scope'];status:string;entities:GeometricEntity[];observations:{observation_id:string}[];evidence:{evidence_id:string;source_observation_refs:string[];supporting:Record<string,unknown>[];contradicting:Record<string,unknown>[];unresolved_source_ids:string[]}[];coverage:{source_observations_reextracted_from_uploaded_PDF:boolean;parent_display_caps_used_for_geometry:boolean;context_geometry_input_complete:boolean;reasons:string[];source_counts:Record<string,number>};audit:{candidate_counts:Record<string,number>};runtime_seconds:number}
export interface GeometryRun {run_id:string;status:string;result:GeometryBundle|null;failure:{reason:string}|null}
export function geometryMatches(b:GeometryBundle,parent:ObservationBundle,image:NativeImage,roi:TargetROI|null){return b.parent_observation_run_id===parent.run_id&&bundleMatches(parent,image,roi)&&b.frame_id===parent.frame_id&&b.source_binding.document_id===image.document.id&&JSON.stringify(b.analysis_scope.core_roi.bbox_native)===JSON.stringify(parent.analysis_scope.core_roi.bbox_native)}
export function geometricLayer(entity:GeometricEntity):string {return entity.element_type==='Window'||entity.geometry_properties.classification==='WINDOW_OR_SLIDING_TRACK'?'windows':['Door','Opening'].includes(entity.element_type)?'doors':'walls'}
export function pickGeometricEntity(point:Vertex,entities:GeometricEntity[],tolerance=6){
 const adapted=entities.map(e=>({candidate_key:e.entity_id,candidate_id:e.entity_id,element_type:e.element_type,role:e.source_role,frame_id:e.frame_id,source_version:'current_upload_geometry',included_in_evaluation:false,display_polygons_native:e.geometry_native.polygons_native||[],display_segments_native:e.geometry_native.polylines_native||[],bbox_native:e.geometry_native.bbox_native} as Candidate));
 const hit=pickCandidate(point,adapted,tolerance);return entities.find(e=>e.entity_id===hit?.candidate_key)||null;
}
