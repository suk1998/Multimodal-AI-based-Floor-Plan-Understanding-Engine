"""Scoped use of existing Wall/Opening meanings; no new OWL classes.

Drawn boundary/profile difference is independent of physical installation
identity, Window/Grille classification, material and room-graph completion.
"""
from . import understanding_contract_v0_5 as ontology

VERSION='fpue_opening_drawing_decision/1'
CONFIRMATION='OPENING_DRAWING_GEOMETRY_WITH_EXTERNAL_WALL_SUPPORT'
CLAIM_SCOPE='OBSERVED_OPENING_DRAWING_GEOMETRY'
CHANGE_TYPES={'OPENING_DRAWN_JAMB_POSITION','OPENING_DRAWN_PROFILE'}

def is_opening_drawing_row(row):
    return (row.get('automatic_confirmation')==CONFIRMATION or 'opening_geometry_evidence' in row
            or bool(set(row.get('change_types',[]))&CHANGE_TYPES))

def is_supported_opening_change(row):
    p=row.get('opening_geometry_evidence') or {}
    types=row.get('change_types',[])
    return (row.get('domain')=='STRUCTURAL' and row.get('entity_type')=='Opening'
        and row.get('automatic_confirmation')==CONFIRMATION and bool(types) and set(types)<=CHANGE_TYPES
        and row.get('physical_change_inferred') is False and p.get('physical_assignment_status')=='UNBOUND'
        and p.get('policy_version')==VERSION and p.get('claim_scope')==CLAIM_SCOPE
        and p.get('validation_status')=='SUPPORTED' and p.get('registration_supported') is True
        and p.get('unique_correspondence') is True and p.get('core_used_for_fit') is False
        and p.get('independent_global_holdout_supported') is True and p.get('external_witness_axes')==[0,1]
        and all(p.get('jamb_difference_supported') is True if t=='OPENING_DRAWN_JAMB_POSITION'
                else p.get('profile_difference_supported') is True for t in types))
