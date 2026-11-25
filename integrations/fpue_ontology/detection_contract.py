"""RDF-independent use of existing ontology meanings in detection decisions.

A printed type designation can change while physical assignment/specification is
unknown. These policies add no OWL classes and do not complete a drawing graph.
"""
import hashlib,json
from . import understanding_contract_v0_5 as ontology
from .vocabulary import BASE_ELEMENT_CODES

VERSION='fpue_printed_type_decision/1'
CONFIRMATION='REGISTERED_SYMBOL_TYPE_TEXT_CHANGE'
CHANGE_TYPE='TYPE_CODE_TEXT'
CLAIM_SCOPE='PRINTED_TYPE_DESIGNATION'


def glossary_digest():
    return hashlib.sha256(json.dumps(BASE_ELEMENT_CODES,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()


def interpret_code(code):
    family,number=ontology.split_code(code)
    entry=next((d for d in BASE_ELEMENT_CODES if d[0]==code),None)
    family_definition=next((d for d in BASE_ELEMENT_CODES if d[0].split('/')[0]==family),None)
    return dict(canonical_code=code,family=family,type_number=number,catalog_scope='INITIAL_USER_VOCAB',
        definition_origin='USER_INITIAL_GLOSSARY',definition_status='INITIAL_GLOSSARY' if entry else 'UNRESOLVED',
        declared_type=entry[1] if entry else None,declared_material=entry[3] if entry else None,
        family_interpretation=(dict(element_category=family_definition[1],material=family_definition[3],function=family_definition[4]) if family_definition else None),
        interpretation_status='PROPOSED',physical_assignment_status='UNBOUND',physical_change_inferred=False,
        ontology_version=ontology.VERSION,ontology_contract_sha256=ontology.contract_digest(),glossary_sha256=glossary_digest())


def is_supported_symbol_change(row):
    proof=row.get('symbol_evidence') or {}
    readings=[row.get('before'),row.get('after')]
    try:
        for reading in readings:ontology.split_code(reading)
    except ValueError:return False
    return (row.get('domain')=='ANNOTATION' and row.get('entity_type')=='CodeMarker'
        and row.get('automatic_confirmation')==CONFIRMATION and row.get('change_types')==[CHANGE_TYPE]
        and row.get('physical_change_inferred') is False and readings[0]!=readings[1]
        and proof.get('validation_status')=='SUPPORTED' and proof.get('claim_scope')==CLAIM_SCOPE
        and proof.get('policy_version')==VERSION and proof.get('registration_supported') is True
        and proof.get('complete_readings') is True and proof.get('unique_correspondence') is True)
