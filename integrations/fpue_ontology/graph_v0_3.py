"""Validate/serialize partial semantic hypothesis graphs; never detect or compare."""
import json
from urllib.parse import quote
from rdflib import Graph, Literal, Namespace
from rdflib.namespace import RDF, XSD
from .schema_v0_3 import U
from .understanding_contract import VERSION, ENTITY_PARENTS, CLAIM_STATES, GROUNDING_STATES, RELATION_CONTRACTS, validate_relation_signature, is_a
from .vocabulary import resolve_space_term

DATA=Namespace('https://example.org/fpue/understanding/0.3/data/')


def iri(*parts):
    return DATA['/'.join(quote(str(part),safe='') for part in parts)]


def json_text(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,allow_nan=False,separators=(',',':'))


def index(records,name):
    if not isinstance(records,list):raise ValueError(f'{name} must be a list')
    result={}
    for record in records:
        identity=record.get('id') if isinstance(record,dict) else None
        if not isinstance(identity,str) or not identity.strip():raise ValueError(f'{name}: record ID required')
        if identity in result:raise ValueError(f'{name}: duplicate record ID {identity}')
        result[identity]=record
    return result


def validate_graph(graph):
    if not isinstance(graph,dict) or graph.get('schema_version')!=VERSION:raise ValueError('v0.3 understanding graph required')
    json_text(graph)
    context=graph.get('context',{})
    if context.get('source_role') not in {'Ref','Target','SINGLE'}:raise ValueError('explicit source role required')
    if not isinstance(graph.get('graph_id'),str) or not graph['graph_id']:raise ValueError('graph_id required')
    source=context.get('source_document',{})
    sha=source.get('sha256','')
    if len(sha)!=64 or any(c not in '0123456789abcdef' for c in sha):raise ValueError('source document SHA required')
    if type(context.get('inventory_complete')) is not bool:raise ValueError('inventory completeness must be explicit boolean')
    if context['inventory_complete']:
        proof=context.get('inventory_verification',{})
        if not context.get('source_binding') or not proof.get('criteria_version') or not proof.get('reviewed_scope') or not proof.get('independent_evidence_ids'):
            raise ValueError('complete inventory requires scoped independent verification, not a complete-looking example list')
    binding=context.get('source_binding')
    if binding is not None:
        required={'source_sha256','image_sha256','page','extraction_zoom','width','height','frame_id','source_role'}
        if not isinstance(binding,dict) or not required<=set(binding) or binding['source_role']!=context['source_role']:
            raise ValueError('explicit same-role source/native binding required')
    groups={key:index(graph.get(key,[]),key) for key in ['entities','relations','claims','source_statements','evidence_requests']}
    ids=[identity for group in groups.values() for identity in group]
    if len(ids)!=len(set(ids)):raise ValueError('record IDs collide across groups')
    entities=groups['entities'];statements=groups['source_statements'];relations=groups['relations']
    for entity in entities.values():
        if entity.get('expected_type') not in ENTITY_PARENTS:raise ValueError('unknown entity type; keep unclassified hypotheses rather than inventing ontology classes')
        if entity.get('grounding_status') not in GROUNDING_STATES:raise ValueError('entity grounding status required')
        if entity.get('source_role',context['source_role'])!=context['source_role']:raise ValueError('mixed source role')
        if entity.get('geometry') is not None:
            binding=context.get('source_binding')
            if not binding or not binding.get('frame_id'):raise ValueError('geometry requires an explicit source/native frame binding')
            if entity['geometry'].get('frame_id')!=binding['frame_id'] or entity['geometry'].get('coordinate_system')!='image_native_pixels':raise ValueError('geometry frame mismatch')
        if any(sid not in statements for sid in entity.get('statement_ids',[])):raise ValueError('missing entity source statement')
    for relation in relations.values():
        a,b=relation.get('source_id'),relation.get('target_id')
        if a not in entities or b not in entities or a==b:raise ValueError('relation needs distinct, same-snapshot endpoints')
        validate_relation_signature(relation.get('kind'),entities[a]['expected_type'],entities[b]['expected_type'])
        if any(sid not in statements for sid in relation.get('statement_ids',[])):raise ValueError('missing relation source statement')
        if relation['kind']=='Connection':
            mode=relation.get('mode','Unknown');via=relation.get('via_entity_id')
            if mode not in RELATION_CONTRACTS['Connection']['modes']:raise ValueError('unsupported connection mode')
            if via is not None and (via not in entities or not any(is_a(entities[via]['expected_type'],kind) for kind in ['Door','Opening'])):raise ValueError('Connection via must be a Door/Opening hypothesis')
            if mode=='Door' and (via is None or not is_a(entities[via]['expected_type'],'Door')):raise ValueError('Door connection requires a separate Door hypothesis, not a type-code literal')
    for claim in groups['claims'].values():
        if claim.get('subject_id') not in entities|relations:raise ValueError('unknown claim subject')
        if claim.get('status') not in CLAIM_STATES:raise ValueError('explicit supported claim state required')
        if any(sid not in statements for sid in claim.get('statement_ids',[])):raise ValueError('missing Claim source statement')
        if claim['status']=='VERIFIED':
            verification=claim.get('verification_record',{})
            if not verification.get('criteria_version') or not verification.get('verified_scope') or not verification.get('independent_evidence_ids'):
                raise ValueError('VERIFIED needs explicit scoped criteria and independent evidence; a manual example is insufficient')
    known=set(entities)|set(relations)|set(statements)
    for request in groups['evidence_requests'].values():
        if any(identity not in known for identity in request['subject_ids']):raise ValueError('unknown evidence-request subject')
        if request.get('source_role')!=context['source_role']:raise ValueError('request crosses independent role')
        if request.get('executable') is not False:raise ValueError('schema example requests are plans, not executed engine observations')
    return graph


def to_rdf(graph):
    validate_graph(graph)
    g=Graph();g.bind('u',U);g.bind('data',DATA)
    context=graph['context'];role=context['source_role'];snapshot=iri('snapshot',role,graph['graph_id'])
    g.add((snapshot,RDF.type,U.GraphSnapshot));g.add((snapshot,U.sourceRole,Literal(role)))
    if context.get('variant') is not None:g.add((snapshot,U.variant,Literal(context['variant'])))
    g.add((snapshot,U.snapshotContextJSON,Literal(json_text(context))))
    source=context['source_document'];doc=iri('source',source['sha256'])
    g.add((doc,RDF.type,U.SourceDocument));g.add((doc,U.sourceSHA256,Literal(source['sha256'])))
    g.add((doc,U.sourcePath,Literal(source['path'])));g.add((doc,U.sourceKind,Literal(source['kind'])))
    g.add((snapshot,U.hasSourceDocument,doc))
    nodes={record['id']:iri('record',role,graph['graph_id'],record['id']) for key in ['entities','relations','claims','source_statements','evidence_requests'] for record in graph[key]}
    catalog=graph.get('catalog',{});catalog_node=iri('catalog',catalog.get('id',source['sha256']))
    g.add((catalog_node,RDF.type,U.Catalog));g.add((catalog_node,U.catalogScope,Literal(catalog.get('scope','UNRESOLVED'))))
    g.add((catalog_node,U.definedInDocument,doc))
    entries={}
    for entry in catalog.get('entries',[]):
        node=iri('catalog-entry',catalog.get('id',source['sha256']),entry['code']);entries[entry['code']]=node
        g.add((node,RDF.type,U.CatalogEntry));g.add((node,U.inCatalog,catalog_node));g.add((node,U.typeCode,Literal(entry['code'])))
        g.add((node,U.definitionStatus,Literal(entry['definition_status'])))
        g.add((node,U.catalogDefinitionJSON,Literal(json_text(entry))))
        if entry.get('category'):g.add((node,U.declaredEntityType,U['EntityType_'+entry['category']]))
        if entry.get('name'):g.add((node,U.catalogTypeName,Literal(entry['name'])))
        if entry.get('function'):g.add((node,U.declaredFunction,Literal(entry['function'])))
        if entry.get('material'):
            material=iri('material-concept',entry['material']);g.add((material,RDF.type,U.MaterialConcept));g.add((node,U.declaredMaterial,material))
    def common(record,kind):
        node=nodes[record['id']]
        g.add((node,RDF.type,U[kind]));g.add((node,U.recordId,Literal(record['id'])));g.add((node,U.inSnapshot,snapshot));g.add((node,U.recordJSON,Literal(json_text(record))))
        for sid in record.get('statement_ids',[]):g.add((node,U.supportedByStatement,nodes[sid]))
        return node
    for statement in graph['source_statements']:
        node=common(statement,'SourceStatement');g.add((node,U.sourceLine,Literal(statement['line'],datatype=XSD.integer)));g.add((node,U.sourceText,Literal(statement['raw_text'])))
    for entity in graph['entities']:
        kind=entity['expected_type']
        hypothesis='SpaceHypothesis' if is_a(kind,'Space') else 'OpeningHypothesis' if kind=='Opening' else 'BoundaryHypothesis' if is_a(kind,'Boundary') else 'ElementHypothesis' if is_a(kind,'BuildingElement') else 'ObservationHypothesis'
        node=common(entity,hypothesis);g.add((node,U.proposedEntityType,U['EntityType_'+kind]));g.add((node,U.groundingStatus,U['GroundingStatus_'+entity['grounding_status']]))
        if entity.get('label'):
            g.add((node,U.labelHypothesis,Literal(entity['label'])))
            term=resolve_space_term(entity['label']) if is_a(kind,'Space') else None
            if term:
                g.add((node,U.proposedSpaceType,U['SpaceType_'+term[1]]))
                if term[2] is not None:g.add((node,U.spaceOrdinalHypothesis,Literal(term[2],datatype=XSD.integer)))
                if term[3]:g.add((node,U.spaceRoleHypothesis,Literal(term[3])))
        if entity.get('catalog_code') in entries:g.add((node,U.proposedCatalogEntry,entries[entity['catalog_code']]))
        for raw in entity.get('raw_type_codes',[]):g.add((node,U.rawTypeCode,Literal(raw)))
        if entity.get('geometry') is not None:
            geo=iri('geometry',role,graph['graph_id'],entity['id']);binding=context['source_binding'];frame=iri('frame',role,graph['graph_id'],binding['frame_id'])
            g.add((geo,RDF.type,U.GeometryEvidence));g.add((geo,U.geometryJSON,Literal(json_text(entity['geometry']))));g.add((node,U.hypothesisGeometry,geo));g.add((geo,U.inFrame,frame))
            g.add((frame,RDF.type,U.CoordinateFrame));g.add((frame,U.frameId,Literal(binding['frame_id'])));g.add((frame,U.coordinateUnit,Literal('image_native_pixels')))
    for relation in graph['relations']:
        node=common(relation,relation['kind']+'Hypothesis');g.add((node,U.relationSource,nodes[relation['source_id']]));g.add((node,U.relationTarget,nodes[relation['target_id']]))
        g.add((node,U.groundingStatus,U['GroundingStatus_'+relation.get('grounding_status','UNBOUND')]))
        if relation['kind']=='Connection':
            g.add((node,U.connectionMode,U['ConnectionMode_'+relation.get('mode','Unknown')]))
            if relation.get('via_entity_id'):g.add((node,U.viaEntityHypothesis,nodes[relation['via_entity_id']]))
    for claim in graph['claims']:
        node=common(claim,'Claim');subject=nodes[claim['subject_id']]
        g.add((node,U.claimSubject,subject));g.add((subject,U.hasClaim,node));g.add((node,U.claimStatus,U['ClaimStatus_'+claim['status']]))
        g.add((node,U.claimPredicate,Literal(claim['predicate'])));g.add((node,U.claimValueJSON,Literal(json_text(claim['value']))));g.add((node,U.claimScope,Literal(claim['scope'])))
    for request in graph['evidence_requests']:
        node=common(request,'EvidenceRequest');g.add((node,U.requestedEngine,U['Engine_'+request['engine']]))
        for identity in request['subject_ids']:g.add((node,U.requestSubject,nodes[identity]))
        g.add((node,U.question,Literal(request['question'])));g.add((node,U.requestStatus,Literal(request['status'])));g.add((node,U.requestExecutable,Literal(False)));g.add((node,U.requestReason,Literal(request['reason'])))
    return g
