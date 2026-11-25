// Keep the current screen/export flow. This narrowly adds geometrically
// verified printed annotation changes; other candidates stay in review.
export interface FinalChangeKind {
  domain:string;
  entity_type:string;
  automatic_confirmation?:string;
  change_types?:string[];
  dimension_evidence?:{validation_status:string};
  symbol_evidence?:{validation_status:string;claim_scope:string;policy_version:string;
    registration_supported:boolean;complete_readings:boolean;unique_correspondence:boolean};
  opening_geometry_evidence?:{validation_status:string;claim_scope:string;policy_version:string;
    registration_supported:boolean;unique_correspondence:boolean;core_used_for_fit:boolean;
    independent_global_holdout_supported:boolean;external_witness_axes:number[];physical_assignment_status:string;
    jamb_difference_supported:boolean;profile_difference_supported:boolean};
  physical_change_inferred?:boolean;
  before?:string;
  after?:string;
}
export function isFinalChangeKind(row:FinalChangeKind) {
  const openingTypes=['OPENING_DRAWN_JAMB_POSITION','OPENING_DRAWN_PROFILE'];
  if(row.automatic_confirmation==='OPENING_DRAWING_GEOMETRY_WITH_EXTERNAL_WALL_SUPPORT'||row.opening_geometry_evidence
    ||row.change_types?.some(t=>openingTypes.includes(t))){
    const p=row.opening_geometry_evidence;
    return row.domain==='STRUCTURAL'&&row.entity_type==='Opening'
      &&row.automatic_confirmation==='OPENING_DRAWING_GEOMETRY_WITH_EXTERNAL_WALL_SUPPORT'
      &&!!row.change_types?.length&&row.change_types.every(t=>openingTypes.includes(t))
      &&row.physical_change_inferred===false&&p?.physical_assignment_status==='UNBOUND'
      &&p.policy_version==='fpue_opening_drawing_decision/1'&&p.claim_scope==='OBSERVED_OPENING_DRAWING_GEOMETRY'
      &&p.validation_status==='SUPPORTED'&&p.registration_supported===true&&p.unique_correspondence===true
      &&p.core_used_for_fit===false&&p.independent_global_holdout_supported===true
      &&JSON.stringify(p.external_witness_axes)==='[0,1]'
      &&row.change_types.every(t=>t==='OPENING_DRAWN_JAMB_POSITION'?p.jamb_difference_supported===true:p.profile_difference_supported===true);
  }
  const dimension=row.entity_type==='DimensionText'
    &&row.automatic_confirmation==='DIMENSION_TEXT_WITH_PAIRED_LINE_GEOMETRY'
    &&row.change_types?.length===1&&row.change_types[0]==='DIMENSION_TEXT_VALUE'
    &&row.dimension_evidence?.validation_status==='SUPPORTED'
    &&row.physical_change_inferred===false
    &&typeof row.before==='string'&&typeof row.after==='string'&&row.before!==row.after;
  const symbol=row.entity_type==='CodeMarker'
    &&row.automatic_confirmation==='REGISTERED_SYMBOL_TYPE_TEXT_CHANGE'
    &&row.change_types?.length===1&&row.change_types[0]==='TYPE_CODE_TEXT'
    &&row.symbol_evidence?.validation_status==='SUPPORTED'
    &&row.symbol_evidence.claim_scope==='PRINTED_TYPE_DESIGNATION'
    &&row.symbol_evidence.policy_version==='fpue_printed_type_decision/1'
    &&row.symbol_evidence.registration_supported===true
    &&row.symbol_evidence.complete_readings===true&&row.symbol_evidence.unique_correspondence===true
    &&row.physical_change_inferred===false
    &&typeof row.before==='string'&&/^[A-Z]{1,8}\/[0-9]{1,3}$/.test(row.before)
    &&typeof row.after==='string'&&/^[A-Z]{1,8}\/[0-9]{1,3}$/.test(row.after)&&row.before!==row.after;
  return row.domain==='STRUCTURAL'||(row.domain==='ANNOTATION'&&(dimension||symbol));
}
