"""Catalog-scoped opening types over the preserved v0.4 hypothesis contract.

No image inference, automatic physical typing, identity matching or live switch.
"""
from copy import deepcopy
import hashlib
import json
import re
from . import understanding_contract_v0_4 as previous

VERSION = 'fpue_opening_type_hypotheses/0.5'
SCHEMA_VERSION = '0.5.0-opening-type-hypotheses'
ONTOLOGY_IRI = 'https://example.org/fpue/understanding/0.5#'
ENTITY_PARENTS = previous.ENTITY_PARENTS
RELATION_CONTRACTS = previous.RELATION_CONTRACTS
CLAIM_STATES = previous.CLAIM_STATES
GROUNDING_STATES = previous.GROUNDING_STATES
GEOMETRY_STATES = previous.GEOMETRY_STATES
REGION_KINDS = previous.REGION_KINDS
BOUNDARY_ORIGINS = previous.BOUNDARY_ORIGINS
GAP_INTERPRETATIONS = previous.GAP_INTERPRETATIONS
EXISTENCE_VALUES = previous.EXISTENCE_VALUES
OPERATIONS = previous.OPERATIONS
OPERATION_CONTRACTS = previous.OPERATION_CONTRACTS
FUNCTION_PROFILE_MAPPING = previous.FUNCTION_PROFILE_MAPPING
is_a = previous.is_a
validate_relation_signature = previous.validate_relation_signature
CANDIDATE_STATES = ('PROPOSED', 'SUPPORTED', 'CONFLICTED', 'REJECTED', 'STALE')
READING_STATES = ('COMPLETE', 'PARTIAL', 'AMBIGUOUS')
CATALOG_ORIGINS = ('USER_INITIAL_GLOSSARY', 'PROJECT_SCHEDULE', 'PROJECT_LEGEND')
DEFINITION_STATES = ('INITIAL_GLOSSARY', 'PROJECT_DECLARED', 'UNRESOLVED')
ELEMENT_CATEGORIES = ('Door', 'FireDoor', 'Window', 'Grille')
REFERENCE_METHODS = {'TRACED_LEADER': 'LeaderLine', 'EXPLICIT_INSTANCE_REFERENCE': 'InstanceReference'}
EXTENSION_GROUPS = ('catalogs', 'catalog_entries', 'type_designations', 'type_assignments')
ENGINE_PROFILES = deepcopy(previous.ENGINE_PROFILES)
ENGINE_PROFILES['SEMANTIC']['questions'] += ['READ_MARKER_FAMILY_AND_NUMBER', 'RESOLVE_OWN_CATALOG_ENTRY']
ENGINE_PROFILES['RELATIONAL']['questions'] += ['GROUND_TYPE_ASSIGNMENT_REFERENCE']
ENGINE_PROFILES['SEMANTIC']['guard'] += ' A glossary is not a project schedule; a type number is not an element instance ID.'


def split_code(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Z]{1,8}/[0-9]{1,3}', value):
        raise ValueError('canonical family/number code required; do not infer a missing number')
    return tuple(value.split('/'))


def contract_spec():
    return {**previous.contract_spec(), 'version': VERSION, 'schema_version': SCHEMA_VERSION,
        'ontology_iri': ONTOLOGY_IRI, 'engine_profiles': ENGINE_PROFILES,
        'opening_type_contract': {'groups': EXTENSION_GROUPS, 'candidate_states': CANDIDATE_STATES,
            'reading_states': READING_STATES, 'catalog_origins': CATALOG_ORIGINS,
            'definition_states': DEFINITION_STATES, 'element_categories': ELEMENT_CATEGORIES,
            'supported_reference_methods': REFERENCE_METHODS,
            'guards': ['No code-based physical identity or sameAs',
                'A missing number is not type 1', 'Catalog definitions remain source scoped',
                'No physical material, dimensions, fire rating or passage from a code alone',
                'Supported instance assignment needs own geometric reference and project definition']},
        'scope': 'versioned ontology and candidate validation; no automatic live engine migration'}


def contract_digest():
    return hashlib.sha256(json.dumps(contract_spec(),sort_keys=True,separators=(',',':')).encode()).hexdigest()
