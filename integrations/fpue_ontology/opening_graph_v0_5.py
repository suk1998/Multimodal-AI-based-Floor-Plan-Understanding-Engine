"""Validate and serialize catalog/type associations, reusing v0.4 core checks.

This module consumes supplied observations. It does not find symbols, assign the
nearest door, infer passage, merge physical IDs or execute comparison algorithms.
"""
import re
from urllib.parse import quote
from rdflib import Graph,Literal,Namespace,URIRef
from rdflib.namespace import RDF,XSD
from . import region_graph_v0_4 as previous
from . import understanding_contract_v0_4 as old_contract
from . import understanding_contract_v0_5 as contract
from .schema_v0_4 import U as OLD
from .schema_v0_5 import U
from .vocabulary import normalize_label

DATA=Namespace('https://example.org/fpue/understanding/0.5/data/')


def core_graph(graph):
    return {**graph,'schema_version':old_contract.VERSION}


def validate_graph(graph):
    if not isinstance(graph,dict) or graph.get('schema_version')!=contract.VERSION:
        raise ValueError('v0.5 opening-type graph required')
    if not isinstance(graph.get('graph_id'),str) or not graph['graph_id'].strip():raise ValueError('graph ID required')
    previous.validate_graph(core_graph(graph))
    binding=graph['source_binding'];groups={k:previous._index(graph.get(k,[])) for k in previous.GROUPS+contract.EXTENSION_GROUPS}
    records={i:r for group in groups.values() for i,r in group.items()}
    if len(records)!=sum(len(g) for g in groups.values()):raise ValueError('record IDs collide across core/catalog groups')
    evidence={**groups['observations'],**groups['external_evidence']}
    for key in contract.EXTENSION_GROUPS:
        for record in groups[key].values():
            if record.get('source_role')!=binding['source_role'] or record.get('frame_id')!=binding['frame_id']:
                raise ValueError('catalog/type association crosses role or native frame')
            previous._geometry(record.get('geometry'),binding)
            if any(i not in evidence for i in record.get('source_evidence_refs',[])):raise ValueError('missing own-source evidence')
    for catalog in groups['catalogs'].values():
        if catalog.get('origin') not in contract.CATALOG_ORIGINS or not isinstance(catalog.get('scope'),str) or not catalog['scope'].strip():
            raise ValueError('explicit catalog origin and scope required')
        refs=catalog.get('source_evidence_refs',[])
        if not refs or any(i not in groups['external_evidence'] for i in refs):raise ValueError('catalog needs explicit definition-source evidence')
        if any(evidence[i].get('evidence_origin')!=catalog['origin'] for i in refs):raise ValueError('catalog source origin mismatch')
        sha=catalog.get('document_sha256','')
        if not isinstance(sha,str) or len(sha)!=64 or any(c not in '0123456789abcdef' for c in sha):raise ValueError('catalog source SHA required')
        if any(evidence[i].get('source_document_sha256')!=sha for i in refs):raise ValueError('catalog source SHA mismatch')
        if catalog['origin']!='USER_INITIAL_GLOSSARY':
            if type(catalog.get('page')) is not int or catalog['page']<1:raise ValueError('project catalog source page required')
            if any(evidence[i].get('source_page')!=catalog['page'] for i in refs):raise ValueError('catalog source page mismatch')
    scoped=set()
    for entry in groups['catalog_entries'].values():
        catalog=groups['catalogs'].get(entry.get('catalog_id'))
        if catalog is None:raise ValueError('catalog entry needs a resolved catalog')
        family,number=contract.split_code(entry.get('code'))
        if entry.get('family')!=family or entry.get('type_number')!=number:raise ValueError('catalog family/number mismatch')
        key=(catalog['id'],entry['code'])
        if key in scoped:raise ValueError('duplicate code in one catalog; retain conflict explicitly instead')
        scoped.add(key)
        status=entry.get('definition_status')
        if status not in contract.DEFINITION_STATES:raise ValueError('definition status required')
        if status=='UNRESOLVED':
            if any(entry.get(k) for k in ('category','material','function','name','declared_properties')):
                raise ValueError('unresolved catalog entry cannot invent specifications')
        else:
            if entry.get('category') not in contract.ELEMENT_CATEGORIES or not entry.get('source_evidence_refs'):
                raise ValueError('defined entry requires category and definition evidence')
            if not set(entry['source_evidence_refs'])<=set(catalog['source_evidence_refs']):raise ValueError('entry definition needs its own catalog source')
            if (status=='INITIAL_GLOSSARY') != (catalog['origin']=='USER_INITIAL_GLOSSARY'):
                raise ValueError('initial glossary cannot be promoted to a project schedule')
    for designation in groups['type_designations'].values():
        symbol=groups['entities'].get(designation.get('symbol_id'),{})
        if symbol.get('expected_type')!='Symbol':raise ValueError('designation needs an observed Symbol hypothesis')
        if designation.get('status') not in contract.CANDIDATE_STATES or designation.get('reading_status') not in contract.READING_STATES:
            raise ValueError('explicit non-VERIFIED designation state required')
        refs=designation.get('reading_observation_ids',[])
        if not refs or any(i not in groups['observations'] for i in refs):raise ValueError('designation needs own text-reading observations')
        if any(groups['observations'][i].get('observation_type') not in {'TextRegion','RecognitionCrop'} for i in refs):
            raise ValueError('code reading cannot use a wall or catalog definition as OCR')
        if not set(refs)<=set(designation.get('source_evidence_refs',[])):raise ValueError('reading evidence is incomplete')
        marker_geometry=symbol.get('geometry')
        if designation['status']=='SUPPORTED' and not marker_geometry:
            raise ValueError('supported marker designation needs native symbol geometry')
        if marker_geometry:
            rings=marker_geometry.get('outer_rings',[]) if marker_geometry['kind']=='Polygon' else [marker_geometry['points_native']]
            points=[p for ring in rings for p in ring];xs=[p[0] for p in points];ys=[p[1] for p in points]
            for identity in refs:
                geometry=groups['observations'][identity].get('geometry')
                if not geometry:
                    if designation['status']=='SUPPORTED':raise ValueError('supported code reading needs native text geometry')
                    continue
                polygons=geometry.get('outer_rings',[]) if geometry['kind']=='Polygon' else [geometry['points_native']]
                positions=[p for ring in polygons for p in ring];x=sum(p[0] for p in positions)/len(positions);y=sum(p[1] for p in positions)/len(positions)
                if not (min(xs)<=x<=max(xs) and min(ys)<=y<=max(ys)) and designation['status']!='CONFLICTED':
                    raise ValueError('reading observation belongs outside this marker')
        complete=designation['reading_status']=='COMPLETE'
        if designation['status']=='SUPPORTED' and not complete:raise ValueError('partial/ambiguous number cannot be supported as a complete code')
        if complete:
            family,number=contract.split_code(designation.get('canonical_code'))
            if family!=designation.get('family') or number!=designation.get('type_number'):raise ValueError('designation family/number mismatch')
            tokens=[normalize_label(groups['observations'][i].get('text','')).upper() for i in refs]
            families=set();numbers=set()
            for token in tokens:
                combined=re.fullmatch(r'([A-Z]{1,8})/?([0-9]{1,3})',token)
                if combined:families.add(combined[1]);numbers.add(combined[2])
                elif re.fullmatch('[A-Z]{1,8}',token):families.add(token)
                elif re.fullmatch('[0-9]{1,3}',token):numbers.add(token)
            if families!={family} or numbers!={number}:raise ValueError('canonical code contradicts observed family/number')
            entry=groups['catalog_entries'].get(designation.get('entry_id'))
            if entry is None or entry['code']!=designation['canonical_code']:raise ValueError('designation must resolve the exact scoped type code')
        elif designation.get('canonical_code') is not None or designation.get('entry_id') is not None or designation.get('type_number') is not None:
            raise ValueError('missing/ambiguous number cannot select a catalog entry')
    for assignment in groups['type_assignments'].values():
        if assignment.get('status') not in contract.CANDIDATE_STATES or assignment.get('scope')!='CATALOG_TYPE_ONLY':
            raise ValueError('scoped non-VERIFIED type-assignment state required')
        designation=groups['type_designations'].get(assignment.get('designation_id'))
        entry=groups['catalog_entries'].get(assignment.get('entry_id'))
        element=groups['entities'].get(assignment.get('element_id'),{})
        if designation is None or entry is None or designation.get('entry_id')!=entry['id']:
            raise ValueError('assignment needs its exact designation/catalog entry')
        kind=element.get('expected_type')
        if not any(old_contract.is_a(kind,k) for k in ['Door','Window','Grille']):raise ValueError('type assignment needs a separate Door/Window/Grille hypothesis')
        category=entry.get('category')
        compatible=category is None or old_contract.is_a(kind,category) or old_contract.is_a(category,kind)
        if not compatible and assignment['status']!='CONFLICTED':raise ValueError('catalog category contradicts element kind')
        if assignment.get('reference_relation_id'):
            reference=groups['relations'].get(assignment['reference_relation_id'],{})
            if reference.get('kind')!='ReferenceAssociation' or reference.get('source_id')!=designation['symbol_id'] or reference.get('target_id')!=element['id']:
                raise ValueError('assignment reference must resolve its own symbol/element pair')
        if assignment['status']=='SUPPORTED':
            if designation['status']!='SUPPORTED' or entry['definition_status']!='PROJECT_DECLARED':
                raise ValueError('supported instance assignment needs a complete reading and project definition')
            reference=groups['relations'].get(assignment.get('reference_relation_id'),{})
            if (reference.get('kind')!='ReferenceAssociation' or reference.get('source_id')!=designation['symbol_id']
                or reference.get('target_id')!=element['id'] or reference.get('grounding_status')!='GROUNDED'):
                raise ValueError('supported instance assignment needs its own grounded reference')
            required=contract.REFERENCE_METHODS.get(reference.get('reference_method'))
            proofs=[groups['observations'].get(i,{}) for i in reference.get('source_evidence_refs',[])]
            if not required or not any(o.get('observation_type')==required and o.get('geometry') for o in proofs):
                raise ValueError('proximity or code equality is not an instance-reference method')
            if not element.get('geometry') or not assignment.get('source_evidence_refs'):
                raise ValueError('supported instance assignment requires native element geometry and evidence')
            if not set(reference['source_evidence_refs'])<=set(assignment['source_evidence_refs']):
                raise ValueError('assignment must preserve the actual instance-reference evidence')
    return graph


def dependencies(graph):
    validate_graph(graph);dep=previous.dependencies(core_graph(graph))
    for group in contract.EXTENSION_GROUPS:
        for record in graph.get(group,[]):dep[record['id']]=set(record.get('source_evidence_refs',[]))
    for entry in graph.get('catalog_entries',[]):dep[entry['id']].add(entry['catalog_id'])
    for d in graph.get('type_designations',[]):
        dep[d['id']].update([d['symbol_id'],*d['reading_observation_ids']])
        if d.get('entry_id'):dep[d['id']].add(d['entry_id'])
    for a in graph.get('type_assignments',[]):
        dep[a['id']].update([a['designation_id'],a['entry_id'],a['element_id']])
        if a.get('reference_relation_id'):dep[a['id']].add(a['reference_relation_id'])
    return dep


def invalidation_plan(graph,changed_ids):
    dep=dependencies(graph);changed=set(changed_ids)
    if not changed<=dep.keys():raise ValueError('unknown changed record')
    stale=set(changed)
    while True:
        additions={i for i,parents in dep.items() if parents&stale}-stale
        if not additions:break
        stale.update(additions)
    return dict(changed_ids=sorted(changed),stale_ids=sorted(stale-changed),state='STALE',applied=False)


def to_rdf(graph):
    validate_graph(graph);result=Graph();result.bind('u',U);result.bind('data',DATA)
    def rebase(term):
        if isinstance(term,URIRef):
            if str(term).startswith(str(OLD)):return U[str(term)[len(str(OLD)):]]
            if str(term).startswith(str(previous.DATA)):
                suffix=str(term)[len(str(previous.DATA)):]
                return DATA[quote(graph['graph_id'],safe='')+'/'+suffix]
        return term
    for triple in previous.to_rdf(core_graph(graph)):result.add(tuple(rebase(t) for t in triple))
    binding=graph['source_binding'];prefix=quote(graph['graph_id'],safe='')+'/'+quote(binding['source_role']+':'+binding['frame_id'],safe='')+'/'
    def iri(identity,category='record'):return DATA[prefix+category+'/'+quote(identity,safe='')]
    def common(record,kind):
        node=iri(record['id']);result.add((node,RDF.type,U[kind]));result.add((node,U.recordId,Literal(record['id'])))
        result.add((node,U.inSnapshot,iri('snapshot','snapshot')));result.add((node,U.recordJSON,Literal(previous._json(record))))
        result.add((node,U.sourceRole,Literal(record['source_role'])));result.add((node,U.frameId,Literal(record['frame_id'])))
        for ref in record.get('source_evidence_refs',[]):result.add((node,U.dependsOn,iri(ref)))
        return node
    for catalog in graph.get('catalogs',[]):
        node=common(catalog,'Catalog');result.add((node,U.catalogScope,Literal(catalog['scope'])))
        result.add((node,U.catalogOrigin,Literal(catalog['origin'])));result.add((node,U.catalogSourceSHA256,Literal(catalog['document_sha256'])))
        if 'page' in catalog:result.add((node,U.catalogSourcePage,Literal(catalog['page'],datatype=XSD.integer)))
        for ref in catalog['source_evidence_refs']:result.add((node,U.catalogEvidence,iri(ref)))
    catalogs={c['id']:c for c in graph.get('catalogs',[])}
    for entry in graph.get('catalog_entries',[]):
        node=common(entry,'CatalogEntry');catalog=catalogs[entry['catalog_id']]
        result.add((node,U.inCatalog,iri(catalog['id'])));result.add((node,U.typeCode,Literal(entry['code'])))
        result.add((node,U.entryTypeNumber,Literal(entry['type_number'])));result.add((node,U.definitionStatus,Literal(entry['definition_status'])))
        result.add((node,U.catalogDefinitionJSON,Literal(previous._json(entry))))
        family=U['CodeFamily_'+entry['family']] if catalog['origin']=='USER_INITIAL_GLOSSARY' and entry['definition_status']=='INITIAL_GLOSSARY' else iri('family:'+catalog['id']+':'+entry['family'],'family')
        result.add((family,RDF.type,U.CodeFamilyConcept));result.add((family,U.codeFamily,Literal(entry['family'])));result.add((node,U.entryFamily,family))
        if entry.get('category'):result.add((node,U.declaredEntityType,U['EntityType_'+entry['category']]))
        if entry.get('name'):result.add((node,U.catalogTypeName,Literal(entry['name'])))
        if entry.get('function'):result.add((node,U.declaredFunction,Literal(entry['function'])))
        if entry.get('material'):
            material=iri('material:'+entry['material'],'material');result.add((material,RDF.type,U.MaterialConcept));result.add((node,U.declaredMaterial,material))
    for d in graph.get('type_designations',[]):
        node=common(d,'TypeDesignationHypothesis');result.add((node,U.designationSymbol,iri(d['symbol_id'])))
        result.add((node,U.claimStatus,U['ClaimStatus_'+d['status']]));result.add((node,U.codeReadingState,Literal(d['reading_status'])))
        result.add((node,U.observedFamily,Literal(d['family'])))
        if d.get('type_number') is not None:result.add((node,U.observedTypeNumber,Literal(d['type_number'])))
        if d.get('canonical_code'):result.add((node,U.canonicalTypeCode,Literal(d['canonical_code'])));result.add((node,U.designationEntry,iri(d['entry_id'])))
        for ref in d['reading_observation_ids']:result.add((node,U.readingObservation,iri(ref)))
    for a in graph.get('type_assignments',[]):
        node=common(a,'TypeAssignmentHypothesis');result.add((node,U.claimStatus,U['ClaimStatus_'+a['status']]))
        for name,predicate in [('designation_id','assignmentDesignation'),('entry_id','assignmentEntry'),('element_id','assignmentElement')]:result.add((node,U[predicate],iri(a[name])))
        if a.get('reference_relation_id'):result.add((node,U.assignmentReference,iri(a['reference_relation_id'])))
        result.add((node,U.assignmentScope,Literal(a['scope'])))
    for identity,parents in dependencies(graph).items():
        for parent in parents:result.add((iri(identity),U.dependsOn,iri(parent)))
    return result
