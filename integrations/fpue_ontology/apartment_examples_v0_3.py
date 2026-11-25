"""Read the user's apartment MD into six partial semantic example graphs.

This is a constrained document adapter, not an OCR/VLM engine or GT builder.
Every assertion retains its source line; unsupported/ambiguous text is retained.
"""
import argparse
import base64
import copy
import hashlib
import json
import re
from pathlib import Path
from collections import Counter
from rdflib import Graph
from rdflib.compare import isomorphic

from .schema import BASE_ELEMENT_CODES
from .understanding_contract import VERSION, contract_digest, plan_evidence_requests
from .graph_v0_3 import validate_graph, to_rdf, json_text
from .schema_v0_3 import build_understanding_schema
from .vocabulary import normalize_label


class Builder:
    def __init__(self,path,sha,variant,role,embedded_sha=None):
        graph_id=f'apartment-note:{sha[:16]}:{variant or "standalone"}:{role}'
        self.graph={'schema_version':VERSION,'graph_id':graph_id,
            'context':{'variant':variant,'source_role':role,'source_document':{'path':str(path.resolve()),'sha256':sha,'kind':'USER_AUTHORED_SEMANTIC_EXAMPLE'},
                       'source_binding':None,'inventory_complete':False,'geometry_grounding_performed':False,
                       'example_only':True,'runtime_engine_input':False,'metrics_computed':False,
                       'contract_sha256':contract_digest(),'embedded_glossary_sha256':embedded_sha},
            'catalog':{'id':'user-apartment-note:'+sha,'scope':'USER_DOCUMENT_GLOSSARY_NOT_NEW_PROJECT_DEFAULT','entries':[
                {'code':code,'category':cat,'name':name,'material':material,'function':function,'definition_status':'USER_PROVIDED_GLOSSARY'} for code,cat,name,material,function in BASE_ELEMENT_CODES]},
            'entities':[],'relations':[],'claims':[],'source_statements':[],'evidence_requests':[],'issues':[]}
        self.spaces={};self.elements={};self.propositions={};self.claims={}

    def identity(self,kind,count):return self.graph['graph_id']+f':{kind}:{count:04d}'

    def statement(self,line,raw):
        record={'id':self.identity('statement',len(self.graph['source_statements'])),'line':line,'raw_text':raw,
                'origin':'USER_DOCUMENT','evidence_dependency_group':self.graph['context']['source_document']['sha256']}
        self.graph['source_statements'].append(record);return record

    def claim(self,subject,predicate,value,statement):
        key=(subject,predicate,json_text(value))
        if key in self.claims:
            claim=self.claims[key]
            if statement['id'] not in claim['statement_ids']:claim['statement_ids'].append(statement['id'])
            return claim
        claim={'id':self.identity('claim',len(self.graph['claims'])),'subject_id':subject,'predicate':predicate,'value':value,
               'status':'PROPOSED','origin':'USER_DOCUMENT_ASSERTION','scope':'manual semantic description; native grounding and independent verification not performed',
               'statement_ids':[statement['id']]}
        self.graph['claims'].append(claim);self.claims[key]=claim;return claim

    def entity(self,label,statement):
        label=normalize_label(label)
        # PS can denote multiple shafts. Each textual reference is unresolved;
        # separate records do not assert either sameness or physical difference.
        key=label if label!='PS' else ('PS',statement['id'])
        if key not in self.spaces:
            record={'id':self.identity('entity',len(self.graph['entities'])),'expected_type':'PipeShaft' if label=='PS' else 'Space','label':label,
                    'grounding_status':'UNBOUND','referent_status':'UNRESOLVED' if label=='PS' else 'AUTHOR_NAMED_WITHIN_SNAPSHOT',
                    'geometry':None,'statement_ids':[]}
            self.graph['entities'].append(record);self.spaces[key]=record
        record=self.spaces[key]
        if statement['id'] not in record['statement_ids']:record['statement_ids'].append(statement['id'])
        self.claim(record['id'],'label_hypothesis',label,statement)
        return record

    def element(self,raw_code,key,statement,connection=False):
        raw_code=raw_code.strip();canonical=raw_code
        m=re.fullmatch(r'([A-Z]+)(\d+)',raw_code)
        families={e['code'].split('/')[0] for e in self.graph['catalog']['entries']}
        if m and m.group(1) in families:canonical=m.group(1)+'/'+m.group(2)
        entries={e['code']:e for e in self.graph['catalog']['entries']}
        if canonical not in entries:
            self.graph['catalog']['entries'].append({'code':canonical,'category':None,'name':None,'material':None,'function':None,'definition_status':'UNREGISTERED'})
        entry=next(e for e in self.graph['catalog']['entries'] if e['code']==canonical)
        key=(key,canonical)
        if key not in self.elements:
            kind=entry['category'] or ('Door' if connection else 'UnknownBuildingElement')
            record={'id':self.identity('entity',len(self.graph['entities'])),'expected_type':kind,'label':None,
                    'catalog_code':canonical,'raw_type_codes':[],'catalog_status':entry['definition_status'],
                    'grounding_status':'UNBOUND','referent_status':'UNRESOLVED','physical_instance_count':None,'geometry':None,'statement_ids':[]}
            self.graph['entities'].append(record);self.elements[key]=record
        record=self.elements[key]
        if raw_code not in record['raw_type_codes']:record['raw_type_codes'].append(raw_code)
        if statement['id'] not in record['statement_ids']:record['statement_ids'].append(statement['id'])
        self.claim(record['id'],'catalog_code_hypothesis',{'raw':raw_code,'canonical_candidate':canonical,'catalog_scope':self.graph['catalog']['id']},statement)
        return record

    def relation(self,kind,a,b,statement,**extras):
        signature=(kind,tuple(sorted((a['id'],b['id']))) if kind in {'Connection','Adjacency'} else (a['id'],b['id']),json_text(extras))
        if signature in self.propositions:
            record=self.propositions[signature];record['statement_ids'].append(statement['id'])
            self.graph['issues'].append({'kind':'REPEATED_ASSERTION','relation_id':record['id'],'statement_ids':list(record['statement_ids']),'meaning':'same written proposition; repeated wording is not independent evidence or element count'})
        else:
            record={'id':self.identity('relation',len(self.graph['relations'])),'kind':kind,'source_id':a['id'],'target_id':b['id'],
                    'grounding_status':'UNBOUND','statement_ids':[statement['id']],**extras}
            self.graph['relations'].append(record);self.propositions[signature]=record
        self.claim(record['id'],'relation_hypothesis',{'kind':kind,'source_id':a['id'],'target_id':b['id'],**extras},statement)
        return record

    def add_line(self,line,raw):
        statement=self.statement(line,raw);text=' '.join(raw.split())
        connection=re.fullmatch(r'(.+?) is connected (?:to|with) (.+?)(?: with ([A-Z]+/?\d+))?',text)
        if connection:
            left,right,code=connection.groups();a=self.entity(left,statement);b=self.entity(right,statement)
            via=self.element(code,('connection',tuple(sorted((a['id'],b['id'])))),statement,True) if code else None
            self.relation('Connection',a,b,statement,mode='Door' if code else 'Unknown',via_entity_id=via['id'] if via else None)
            statement['interpretation']='DIRECT_CONNECTION_ASSERTION';return
        adjacent=re.fullmatch(r'(.+?) is (?:adjacent or next to|next to) (.+)',text)
        if adjacent:
            a=self.entity(adjacent[1],statement);b=self.entity(adjacent[2],statement);self.relation('Adjacency',a,b,statement)
            statement['interpretation']='ADJACENCY_ASSERTION';return
        association=re.fullmatch(r'(.+?) (contains or has|has) ([A-Z]+/?\d+)',text)
        if association:
            label,verb,code=association.groups();a=self.entity(label,statement);b=self.element(code,('association',a['id']),statement)
            semantics='HAS_OR_CONTAINS_UNRESOLVED' if verb=='contains or has' else 'GENERAL_ASSOCIATION_ONLY'
            self.relation('ElementAssociation',a,b,statement,association_semantics=semantics)
            statement['interpretation']='ELEMENT_ASSOCIATION_ASSERTION';return
        if 'the space is still there' in text and text.startswith('팬트리 '):
            pantry=self.entity('팬트리',statement);statement.update(interpretation='AMBIGUOUS_CHANGE_NARRATIVE',subject_id=pantry['id'],direction_terms=['South','West'],direction_reference_frame='UNKNOWN')
            self.claim(pantry['id'],'author_stated_space_preserved',True,statement)
            self.claim(pantry['id'],'unresolved_change_narrative',{'raw_text':raw,'changed_property':'UNKNOWN','direction_reference_frame':'UNKNOWN'},statement)
            self.graph['issues'].append({'kind':'AMBIGUOUS_CHANGE_NARRATIVE','statement_id':statement['id'],'subject_id':pantry['id'],
                'questions':['What does removed from refer to: association, label, geometry or access?', 'Which source establishes the former connection?', 'Are South/West drawing-local or geographic directions?'],
                'space_removal_inferred':False,'label_removal_inferred':False,'access_mapping_performed':False});return
        statement['interpretation']='UNPARSED_RETAINED'
        self.graph['issues'].append({'kind':'UNPARSED_RETAINED','statement_id':statement['id']})

    def finish(self):
        self.graph['evidence_requests']=plan_evidence_requests(self.graph)
        self.graph['coverage']={'source_statements':len(self.graph['source_statements']),
            'parsed_positive_assertions':sum(s.get('interpretation') not in {'UNPARSED_RETAINED','AMBIGUOUS_CHANGE_NARRATIVE'} for s in self.graph['source_statements']),
            'retained_narratives':sum(s.get('interpretation')=='AMBIGUOUS_CHANGE_NARRATIVE' for s in self.graph['source_statements']),
            'inventory_complete':False,'negative_facts_inferred':0,'geometry_created':0,'physical_identity_matches_created':0}
        return validate_graph(self.graph)


def read_apartment_document(path):
    path=Path(path);raw_bytes=path.read_bytes();text=raw_bytes.decode('utf-8');sha=hashlib.sha256(raw_bytes).hexdigest()
    embedded=re.search(r'data:image/png;base64,([A-Za-z0-9+/=]+)',text)
    embedded_bytes=base64.b64decode(embedded[1]) if embedded else None
    embedded_sha=hashlib.sha256(embedded_bytes).hexdigest() if embedded_bytes else None
    builders=[];variant=None;current=None
    for line_number,raw in enumerate(text.splitlines(),1):
        clean=raw.strip()
        if clean=='![][image1]':break  # graph/code examples below are separate from all six plan lists
        heading=re.fullmatch(r'\*\*아파트 설계도 (.+)\*\*',clean)
        if heading:variant=heading[1];current=None;continue
        if clean in {'승인도','실시도'} and variant:
            current=Builder(path,sha,variant,'Ref' if clean=='승인도' else 'Target',embedded_sha);builders.append(current);continue
        if current and clean:current.add_line(line_number,clean)
    if len(builders)!=6:raise ValueError('Expected the six explicit Ref/Target source sections; do not invent missing sections')
    graphs=[b.finish() for b in builders]
    # The code block explicitly gives an OpenPassage, but does not identify a
    # single plan variant. Preserve it as a standalone example, not six new facts.
    code_start=next((i for i,l in enumerate(text.splitlines(),1) if l.strip()=='Connection_001'),None)
    standalone=None
    if code_start:
        b=Builder(path,sha,None,'SINGLE',embedded_sha)
        excerpt=text.splitlines()[code_start-1:code_start+4]
        s=b.statement(code_start,'\n'.join(excerpt))
        s['source_excerpt_lines']=excerpt
        a=b.entity('침실1',s);c=b.entity('드레스룸',s);b.relation('Connection',a,c,s,mode='OpenPassage',via_entity_id=None)
        door_start=next((i for i,l in enumerate(text.splitlines(),1) if l.strip()=='Connection_002'),None)
        if door_start:
            excerpt=text.splitlines()[door_start-1:door_start+4]
            s2=b.statement(door_start,'\n'.join(excerpt));s2['source_excerpt_lines']=excerpt
            s2['interpretation']='TYPE_CODE_IN_OPENING_SLOT_IS_NOT_A_PHYSICAL_OPENING_ID'
            left=b.entity('드레스룸',s2);right=b.entity('부부욕실',s2)
            door=b.element('PD/2',('connection',tuple(sorted((left['id'],right['id'])))),s2,True)
            b.relation('Connection',left,right,s2,mode='Door',via_entity_id=door['id'],via_opening_id=None)
            b.graph['issues'].append({'kind':'CODE_VS_INSTANCE_DISAMBIGUATION','statement_id':s2['id'],'meaning':'opening_element: PD/2 names a type code. Door hypothesis created; physical opening identity and location remain unbound.'})
        standalone=b.finish()
    return graphs,standalone,embedded_bytes


def generate_examples(source,output):
    output=Path(output)
    if output.exists():raise FileExistsError('Choose a new output directory; existing examples are preserved')
    graphs,standalone,embedded=read_apartment_document(source)
    prepared=[]
    for graph in graphs+([standalone] if standalone else []):
        rdf=to_rdf(graph);serialized=rdf.serialize(format='turtle')
        if not isomorphic(rdf,Graph().parse(data=serialized,format='turtle')):raise ValueError('Turtle roundtrip failed')
        stem=(graph['context']['variant']+'_'+graph['context']['source_role']) if graph['context']['variant'] else 'standalone_connection_examples'
        prepared.append((stem,graph,serialized))
    output.mkdir(parents=True,exist_ok=False)
    schema=build_understanding_schema()
    with (output/'fpue_understanding_v0_3.ttl').open('x') as stream:stream.write(schema.serialize(format='turtle'))
    summaries=[]
    for stem,graph,serialized in prepared:
        with (output/(stem+'.json')).open('x') as stream:json.dump(graph,stream,ensure_ascii=False,indent=2)
        with (output/(stem+'.ttl')).open('x') as stream:stream.write(serialized)
        summaries.append({'example':stem,'source_statements':len(graph['source_statements']),'entities':len(graph['entities']),
            'relation_kinds':dict(Counter(r['kind'] for r in graph['relations'])),'claims':len(graph['claims']),'questions_by_engine':dict(Counter(r['engine'] for r in graph['evidence_requests'])),
            'issues':graph['issues'],'coverage':graph['coverage'],'native_grounding_performed':False,'facts_verified':0})
    if embedded:
        with (output/'source_embedded_code_glossary.png').open('xb') as stream:stream.write(embedded)
    with (output/'example_audit.json').open('x') as stream:json.dump({'schema_version':VERSION,'contract_sha256':contract_digest(),'examples':summaries,'examples_are_not_rules_or_native_GT':True},stream,ensure_ascii=False,indent=2)
    return summaries


def main():
    parser=argparse.ArgumentParser(description='Build v0.3 schema and source-line-preserving apartment examples')
    parser.add_argument('--source',type=Path,required=True);parser.add_argument('--output-dir',type=Path,required=True);args=parser.parse_args()
    try:summary=generate_examples(args.source,args.output_dir)
    except (ValueError,OSError) as error:parser.exit(1,f'Cannot build examples: {error}\n')
    print(json.dumps(summary,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
