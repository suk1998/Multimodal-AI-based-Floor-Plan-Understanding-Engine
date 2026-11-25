"""Shared ontology constraints for RDF export and geometry-backed understanding.

These are closed-world application rules, not OWL entailment or detection. The
OWL schema embeds this exact specification; the runtime needs no RDF dependency.
"""
import hashlib
import json

ONTOLOGY_IRI = 'https://example.org/fpue/ontology#'
RULE_VERSION = 'fpue_spatial_rules/1'
RULES = {
    'LabelAssociation': {'sources': ['TextRegion', 'Symbol'], 'targets': ['Space', 'Symbol'], 'requires': ['observed label location', 'own frame', 'interior seed or explicit symbol association']},
    'BoundaryAssociation': {'sources': ['Space'], 'targets': ['Wall', 'Door', 'FireDoor', 'Opening', 'Window'], 'requires': ['observed band face or logical threshold', 'geometry evidence', 'own frame']},
    'HostingRelation': {'sources': ['Wall'], 'targets': ['Door', 'FireDoor', 'Opening', 'Window', 'Grille'], 'requires': ['endpoint to band geometry', 'resolved evidence', 'own frame']},
    'Adjacency': {'sources': ['Space'], 'targets': ['Space'], 'requires': ['opposite sides of same supported wall', 'not a passage assertion']},
    'Connection': {'sources': ['Space'], 'targets': ['Space'], 'via': ['Door', 'FireDoor', 'Opening'], 'requires': ['distinct spatial owners on opposite sides', 'visible supported opening', 'host and jamb geometry', 'physical passage samples', 'not window or unclassified track', 'own frame']},
    'Containment': {'sources': ['Space'], 'targets': ['Wall', 'Door', 'FireDoor', 'Opening', 'Window', 'Grille', 'PipeShaft', 'UnclassifiedElement'], 'requires': ['observed containment']},
    'ElementAssociation': {'sources': ['Space'], 'targets': ['Wall', 'Door', 'FireDoor', 'Opening', 'Window', 'Grille', 'PipeShaft', 'UnclassifiedElement'], 'requires': ['explicit observation']},
    'ReferenceAssociation': {'sources': ['Symbol'], 'targets': ['Wall', 'Door', 'FireDoor', 'Opening', 'Window', 'Grille', 'PipeShaft', 'UnclassifiedElement'], 'requires': ['observed reference target; proximity is insufficient']},
}


def rule_spec():
    return {'ontology_iri': ONTOLOGY_IRI, 'version': RULE_VERSION, 'relations': RULES}


def rule_digest():
    return hashlib.sha256(json.dumps(rule_spec(), sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def validate_signature(kind, source_type, target_type):
    spec = RULES.get(kind)
    if not spec or source_type not in spec['sources'] or target_type not in spec['targets']:
        raise ValueError(f'{kind}: invalid ontology signature {source_type} -> {target_type}')
