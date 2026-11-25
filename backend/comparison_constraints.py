"""Actual comparison search decisions, separate from architectural claims.

Records existing gates without changing search, scores or ontology states.
Missing observations before the catalog remain covered by upstream journals.
"""
from collections import Counter

VERSION='comparison_search_constraints/1'

class SearchAudit:
    def __init__(self,journals):
        self.journals=journals;self.objects={}

    def _item(self,role,obj):
        return self.objects.setdefault((role,obj['id']),dict(source_role=role,source_id=obj['id'],
            entity_type=obj['kind'],attempt_count=0,eligible_count=0,rejection_counts=Counter()))

    def attempt(self,a,b,reasons):
        for index,(role,obj,other) in enumerate([('Ref',a,b),('Target',b,a)]):
            item=self._item(role,obj);item['attempt_count']+=1
            item['eligible_count']+=int(not reasons);item['rejection_counts'].update(reasons)
            self.journals[index].record(obj['id'],obj['kind'],
                'PAIR_SEARCH_REJECTED' if reasons else 'PAIR_SEARCH_ELIGIBLE',
                reasons[0] if reasons else 'EXISTING_SEARCH_CONDITIONS_PASSED',
                input_ids=[obj['id']],candidate_other_source_id=other['id'],
                additional_reason_codes=list(reasons[1:]),
                search_scope='COMPARISON_SEARCH_NOT_ARCHITECTURAL_TRUTH')

    def snapshot(self,role,obj):
        item=self._item(role,obj)
        return {**item,'rejection_counts':dict(item['rejection_counts'])}

    def finish(self):
        return dict(schema_version=VERSION,objects=[self.snapshot(role,{'id':oid,'kind':item['entity_type']})
            for (role,oid),item in sorted(self.objects.items())],
            scope='Existing catalog pair search only; earlier observation/filter gaps are not inferred')

def unsupported_source_reasons(objects):
    reasons=[]
    for o in objects:
        if o['supported']:continue
        if o['kind']=='Space':
            code='SPACE_BOUNDARY_INCOMPLETE' if not o['closed'] else 'INFERRED_SPACE_BOUNDARY_NOT_VERIFIED'
        elif o['kind']=='Opening':code='OPENING_GEOMETRY_ADMISSION_INCOMPLETE'
        elif o['kind'] in ['Door','Window','SlidingDoor','WindowOrSlidingDoor','BuildingElement']:
            code='TYPE_OPENING_OR_INSTALLATION_ADMISSION_INCOMPLETE'
        else:code='SOURCE_GEOMETRY_NOT_SUPPORTED'
        if code not in reasons:reasons.append(code)
    return reasons
