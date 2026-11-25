import type {NativeImage} from './api.ts';
export interface DrawingRegion {region_id:string;frame_id:string;source_role:string;bbox_native:[number,number,number,number];kind:string;status:string;is_physical_space_polygon:boolean}
export interface CoverageCell {cell_id:string;bbox_native:[number,number,number,number];counts_by_type:Record<string,{retained?:number;omitted_due_to_limit?:number}>}
export interface ObservationAudit {
 drawing_regions:{candidates:DrawingRegion[];automatic_semantic_partition_verified:boolean};
 observation_bundle:{coverage:{spatial_audit:{cells:CoverageCell[];counts_by_type:Record<string,{encountered:number;retained?:number;omitted_due_to_limit?:number}>}}};
 reobservation_audit:{tiles:{tile_id:string;execution_status:string;resolution_status:string;requested_bbox_native:number[];overlaps:unknown[];raster_size?:number[];raster_to_native?:number[][]}[];skipped:unknown[];cache:{reused:boolean}};
 components:Record<string,{status:string;reason?:string|null;stages?:Record<string,{status:string;failed?:number;completed?:number}>}>;
}
export interface DrawingRegionSelection {run_id:string;region_id:string}
export function omittedInCell(cell:CoverageCell){return Object.values(cell.counts_by_type).reduce((n,c)=>n+(c.omitted_due_to_limit||0),0)}
export function validDrawingRegion(region:DrawingRegion,image:NativeImage,role:'Ref'|'Target'='Ref'){const b=region.bbox_native;return region.frame_id===role+':'+image.id&&region.source_role===role&&b.length===4&&b.every(Number.isFinite)&&b[0]>=0&&b[1]>=0&&b[0]<b[2]&&b[1]<b[3]&&b[2]<=image.width&&b[3]<=image.height}
