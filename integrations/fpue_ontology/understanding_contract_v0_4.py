"""Shared v0.4 region/closure vocabulary; no image processing or live switch.

The four product functions and four ontology profiles are different views.
Closure proposals may precede door recognition. A proposed closing edge is not
an observed Wall and residual area is not automatically a LivingRoom.
"""
from copy import deepcopy
import hashlib
import json

from . import understanding_contract as previous

VERSION = 'fpue_region_hypotheses/0.4'
SCHEMA_VERSION = '0.4.0-region-hypotheses'
ONTOLOGY_IRI = 'https://example.org/fpue/understanding/0.4#'
ENTITY_PARENTS = {**previous.ENTITY_PARENTS,
    'SpatialRegion': 'SpatialEntity', 'DrawingRegion': 'SpatialRegion',
    'OccupiedRegion': 'SpatialRegion', 'PhysicalFreeRegion': 'SpatialRegion',
    'PartitionRegion': 'SpatialRegion', 'UnassignedRegion': 'SpatialRegion',
    'FunctionalSpace': 'Space', 'BoundaryFragment': 'Boundary', 'InferredBoundary': 'Boundary',
    'ClosureBoundary': 'InferredBoundary',
}
RELATION_CONTRACTS = deepcopy(previous.RELATION_CONTRACTS)
RELATION_CONTRACTS.update({
    'RegionMembership': {'source': ['Space'], 'target': ['SpatialRegion'],
        'evidence': ['same-frame region association, not an individual space polygon']},
    'PartitionOf': {'source': ['PartitionRegion'], 'target': ['PhysicalFreeRegion'],
        'evidence': ['partition derivation; logical separators are not occupied walls']},
    'RegionBoundary': {'source': ['SpatialRegion'], 'target': ['Boundary'],
        'evidence': ['observed or explicitly inferred segment, with provenance']},
    'BoundarySupport': {'source': ['Boundary'], 'target': ['Boundary'],
        'evidence': ['supporting face/fragment; no physical wall assertion from closure']},
})
RELATION_CONTRACTS['Connection']['evidence'] = ['distinct space hypotheses', 'own source frame', 'mode-specific evidence']
RELATION_CONTRACTS['Connection']['mode_evidence'] = {
    'Door': ['door/opening and both space sides'],
    'OpenPassage': ['actual passage and both space sides; missing door pixels are insufficient'],
    'OpenPlan': ['shared partition and functional grounding; no mandatory single opening'],
    'Unknown': ['retain existence and mode as separate unresolved claims'],
}
CLAIM_STATES = previous.CLAIM_STATES
GROUNDING_STATES = previous.GROUNDING_STATES
REGION_KINDS = ('PARTITIONED', 'SHARED', 'UNASSIGNED', 'UNKNOWN')
GEOMETRY_STATES = ('POLYGON_AVAILABLE', 'PARTIAL_BOUNDARY', 'UNRESOLVED')
BOUNDARY_ORIGINS = ('OBSERVED_FACE', 'COLLINEAR_INTERPOLATION',
    'ORTHOGONAL_INTERSECTION', 'INFERRED_CLOSURE', 'SUPPORTED_THRESHOLD', 'UNKNOWN')
GAP_INTERPRETATIONS = ('UNKNOWN', 'DOOR_CANDIDATE', 'OPEN_PASSAGE_CANDIDATE',
    'MISSING_WALL_CANDIDATE', 'OPEN_PLAN_CANDIDATE')
EXISTENCE_VALUES = ('PRESENT', 'ABSENT', 'UNKNOWN')
OPERATIONS = ('FRAGMENT_CLOSURE', 'FREE_DOMAIN', 'RESIDUAL_DIFFERENCE', 'REGION_INTERPRETATION')
ENGINE_PROFILES = deepcopy(previous.ENGINE_PROFILES)
ENGINE_PROFILES['GEOMETRIC']['guard'] = 'A closure edge is a boundary hypothesis, not a new observed Wall or physical endpoint.'
ENGINE_PROFILES['SPATIAL']['inputs'] += ['InferredBoundary', 'PhysicalFreeRegion', 'PartitionRegion']
ENGINE_PROFILES['SPATIAL']['outputs'] += ['PartitionRegion', 'UnassignedRegion', 'RegionMembership']
ENGINE_PROFILES['SPATIAL']['questions'] += ['REVIEW_FRAGMENT_CLOSURE', 'CHECK_RESIDUAL_COVERAGE']
ENGINE_PROFILES['SPATIAL']['guard'] = 'Permit region proposals before all four walls/doors are known. Do not label residuals as rooms automatically.'
ENGINE_PROFILES['SEMANTIC']['questions'] += ['INTERPRET_CLOSURE_GAP']
ENGINE_PROFILES['RELATIONAL']['guard'] = 'Separate connection existence/mode. Shared-region membership does not imply all-pairs direct connections.'
FUNCTION_PROFILE_MAPPING = {
    'Observation': {'supplies': ['raw observations'], 'profiles': ['SEMANTIC']},
    'ArchitecturalGeometry': {'profiles': ['GEOMETRIC', 'SEMANTIC']},
    'SpatialUnderstanding': {'profiles': ['SPATIAL', 'SEMANTIC']},
    'RelationalStructuralUnderstanding': {'profiles': ['RELATIONAL', 'SEMANTIC']},
}
OPERATION_CONTRACTS = {
    'FRAGMENT_CLOSURE': {'outputs': ['PartitionRegion', 'ClosureBoundary'],
        'requires_confirmed_door': False, 'requires_four_observed_walls': False,
        'forbidden_outputs': ['Wall', 'Door', 'Opening'],
        'meaning': 'A proposed closed region and inferred closing edges from own fragments.'},
    'FREE_DOMAIN': {'operands': {'domain': 'DrawingRegion', 'occupied': 'OccupiedRegion', 'cores': 'Space'},
        'outputs': ['PhysicalFreeRegion'], 'formula': 'F = U - union(W, C)',
        'meaning': 'No logical threshold separator is a physical occupied wall.'},
    'RESIDUAL_DIFFERENCE': {'operands': {'free_region': 'PhysicalFreeRegion', 'partitions': 'PartitionRegion'},
        'outputs': ['UnassignedRegion'], 'formula': 'R_unassigned = F - union(P_i)',
        'meaning': 'Missing rooms remain unassigned; never automatically LivingRoom/OpenPlan.'},
    'REGION_INTERPRETATION': {'outputs': ['FunctionalSpace'],
        'meaning': 'Separate semantic proposal supported by own labels/context; no region computation implied.'},
}


def is_a(kind, parent):
    while kind is not None:
        if kind == parent:
            return True
        kind = ENTITY_PARENTS.get(kind)
    return False


def validate_relation_signature(kind, source_kind, target_kind):
    rule = RELATION_CONTRACTS.get(kind)
    if not rule or not any(is_a(source_kind, p) for p in rule['source']) or not any(is_a(target_kind, p) for p in rule['target']):
        raise ValueError(f'{kind}: invalid v0.4 signature {source_kind} -> {target_kind}')


def contract_spec():
    return {'version': VERSION, 'schema_version': SCHEMA_VERSION, 'ontology_iri': ONTOLOGY_IRI,
        'entity_parents': ENTITY_PARENTS, 'relation_contracts': RELATION_CONTRACTS,
        'claim_states': CLAIM_STATES, 'grounding_states': GROUNDING_STATES,
        'region_kinds': REGION_KINDS, 'geometry_states': GEOMETRY_STATES,
        'boundary_origins': BOUNDARY_ORIGINS, 'gap_interpretations': GAP_INTERPRETATIONS,
        'existence_values': EXISTENCE_VALUES, 'operation_contracts': OPERATION_CONTRACTS,
        'engine_profiles': ENGINE_PROFILES, 'product_function_mapping': FUNCTION_PROFILE_MAPPING,
        'scope': 'region-stage hypothesis contract; not a live architectural_spatial_bundle/1 adapter'}


def contract_digest():
    return hashlib.sha256(json.dumps(contract_spec(), sort_keys=True, separators=(',', ':')).encode()).hexdigest()
