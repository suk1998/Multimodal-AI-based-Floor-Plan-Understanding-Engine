"""Output contract for measured door DRAWING differences; no OWL state changes.

This contract never establishes physical product, hosting or connection identity.
Geometry correspondence must already pass the independent source/position and
competitor checks; changing width, swing or leaf shape is measured afterwards.
"""
import math

VERSION='door_drawing_change_policy/1'
CONFIRMATION='DOOR_DRAWING_WITH_INDEPENDENT_STRUCTURE'
FACETS={'OBSERVED_FRAME_INTERVAL','OBSERVED_SWING_RADIUS','OBSERVED_SWING_DIRECTION','OBSERVED_LEAF_PROFILE'}

def is_door_drawing_row(row):
    return 'door_geometry_evidence' in row or row.get('automatic_confirmation')==CONFIRMATION

def _box(value):
    return (isinstance(value,(list,tuple)) and len(value)==4
        and all(isinstance(v,(int,float)) and math.isfinite(v) for v in value)
        and value[0]<value[2] and value[1]<value[3])

def supported_drawing_evidence(row):
    """Check a narrow claim, independent of uncompleted installation relations."""
    p=row.get('door_geometry_evidence') or {};registration=p.get('registration') or {}
    competitors=p.get('competitor_assessment') or {};geometry=p.get('geometry_evidence') or {}
    if not (row.get('entity_type')=='Door' and row.get('domain')=='STRUCTURAL'
        and row.get('identity_status')=='SUPPORTED'
        and row.get('geometric_correspondence_status')=='SUPPORTED_GEOMETRIC_CORRESPONDENCE'
        and p.get('status')=='SUPPORTED_GEOMETRIC_CORRESPONDENCE'
        and p.get('claim_scope')=='OBSERVED_DOOR_DRAWING_GEOMETRY'
        and p.get('absolute_geometry_support') is True and p.get('absolute_support') is True
        and geometry.get('sufficient') is True and not p.get('reason_codes')
        and competitors.get('status')=='DISTINGUISHABLE' and competitors.get('reciprocal_best') is True
        and not competitors.get('competing_candidates')
        and registration.get('status')=='SUPPORTED_LOCAL_COORDINATE_CORRESPONDENCE'
        and registration.get('core_used_for_fit') is False
        and registration.get('core_image_agreement_required') is False
        and p.get('relation_assessment',{}).get('status') not in ['CONFLICT','REVIEW']
        and row.get('physical_change_inferred') is False
        and row.get('physical_installation_identity_status')=='UNKNOWN'
        and not row.get('alignment_fit_parent')
        and _box(row.get('verified_target_bbox_native'))):return False
    for role in ['Ref','Target']:
        source=p.get(role) or {};ids=row.get('ref_ids' if role=='Ref' else 'target_ids',[])
        ink=source.get('source_stile_ink_support',[])
        if not (len(ids)==1 and source.get('id')==ids[0]
            and source.get('source_role')==role and source.get('frame_id','').startswith(role+':')
            and source.get('id','').startswith(source['frame_id']+':')
            and source.get('strong') is True and not source.get('source_geometry_contradiction')
            and not source.get('source_geometry_incomplete')
            and len(ink)==2 and all(isinstance(v,(float,int)) and math.isfinite(v) and v>=.85 for v in ink)
            and source.get('evidence_refs')):return False
    return True

def is_supported_door_change(row):
    if not supported_drawing_evidence(row):return False
    if row.get('judgment')!='CHANGED' or row.get('automatic_confirmation')!=CONFIRMATION:return False
    changes=row.get('change_types',[]);measurements=row['door_geometry_evidence'].get('measured_facets',[])
    if not changes or not set(changes)<=FACETS or len(set(changes))!=len(changes):return False
    for name in changes:
        facts=[f for f in measurements if f.get('facet')==name]
        if len(facts)!=1 or facts[0].get('status')!='SUPPORTED' or facts[0].get('changed') is not True:return False
        f=facts[0]
        metric=('difference_degrees' if name=='OBSERVED_SWING_DIRECTION' else
                'maximum_profile_distance_ref_px' if name=='OBSERVED_LEAF_PROFILE' else 'delta_ref_px')
        value=f.get(metric)
        if not isinstance(value,(int,float)) or not math.isfinite(value):return False
        if name=='OBSERVED_FRAME_INTERVAL':
            if len(f.get('source_semantics',[]))!=2 or len(set(f['source_semantics']))!=1:return False
            uncertainty=row['door_geometry_evidence'].get('tolerance_ref_px')
        else:uncertainty=f.get('uncertainty_degrees' if name=='OBSERVED_SWING_DIRECTION' else 'uncertainty_ref_px')
        if not isinstance(uncertainty,(int,float)) or not math.isfinite(uncertainty) or uncertainty<0 or abs(value)<=uncertainty:return False
        if name=='OBSERVED_LEAF_PROFILE':
            for role in ['Ref','Target']:
                profiles=row['door_geometry_evidence'].get('leaf_profiles',{}).get(role,[])
                if len(profiles)!=2 or len({p.get('outline_id') for p in profiles})!=1:return False
                if any(len(p.get('end_cap_source_refs',[]))!=2 for p in profiles):return False
    return True
