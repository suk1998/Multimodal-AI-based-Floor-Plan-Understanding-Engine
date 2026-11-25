"""Shared final-output eligibility; not a blanket promotion of CHANGED rows."""
from backend.dimension_comparison import is_supported_dimension_change
from integrations.fpue_ontology.detection_contract import is_supported_symbol_change
from integrations.fpue_ontology.opening_change_contract import is_opening_drawing_row,is_supported_opening_change
from backend.door_change_policy import is_door_drawing_row,is_supported_door_change


def is_supported_annotation_change(row):
    return is_supported_dimension_change(row) or is_supported_symbol_change(row)


def is_final_change_kind(row):
    if is_door_drawing_row(row):return is_supported_door_change(row)
    if is_opening_drawing_row(row):return is_supported_opening_change(row)
    return row.get('domain')=='STRUCTURAL' or is_supported_annotation_change(row)
