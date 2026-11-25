"""Reference model inventory and gate evidence over immutable stage outputs."""
from collections import Counter
import copy
import hashlib
import json

from backend.source_contract import assert_same_source


GATE_REQUIREMENTS = [
    ('simple_spaces','단순 공간의 경계와 출입구 원본 검토'),
    ('nonrectangular','꺾인 공간 또는 열린 경계 원본 검토'),
    ('passage_and_shaft','실제 통과와 양쪽 공간 및 PS 내부·외피 검토'),
    ('shared_domain','공유 구획과 미배정 영역 원본 검토'),
    ('revision','재관찰 전후 해석 수정 또는 보류 검토'),
]


def sha(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,allow_nan=False).encode()).hexdigest()


def model_review_cases(geometry, *, expected_role="Ref"):
    """Locations for original-image review, never generated GT judgements."""
    partition = geometry.get('space_closure') or {}
    relational = geometry.get('reference_relations' if expected_role=='Ref' else 'target_relations') or {}
    cases = []
    for region in partition.get('regions',[]):
        points = [region['seed_native'],*[p for b in region['boundary_runs'] for p in b['points_native']]]
        xs, ys = zip(*points)
        bounds=[min(xs)-15,min(ys)-15,max(xs)+15,max(ys)+15]
        if not region['boundary_runs']:
            # A missing boundary needs visible surrounding context for review.
            # This window is not promoted to a space shape.
            x,y=region['seed_native']
            radius=max(80,partition.get('label_scale_px',16)*10)
            bounds=[max(0,x-radius),max(0,y-radius),
                    min(geometry['source_binding']['width'],x+radius),
                    min(geometry['source_binding']['height'],y+radius)]
        inferred = sum(b['owner']<0 for b in region['boundary_runs'])
        cases.append({'case_id':region['region_id'],'name':region['name'],
                      'kind':'Space','subject_ids':[region['region_id'],region['observation_id']],
                      'bounds_native':bounds,'bounds_semantics':'REVIEW_CONTEXT_NOT_SPACE_EXTENT',
                      'analysis_state':'PROPOSED' if region['polygon_native'] else 'UNRESOLVED',
                      'review_state':'PENDING_ORIGINAL_REVIEW',
                      'finding':'CONDITIONAL_POLYGON_WITH_INFERRED_SEGMENTS' if inferred else
                                'CONDITIONAL_POLYGON' if region['polygon_native'] else 'PARTIAL_OR_UNKNOWN_BOUNDARY',
                      'polygon_native':region['polygon_native'],'boundary_runs':region['boundary_runs'],
                      'inferred_run_count':inferred,'interpretations':region.get('interpretations',[])})
    for row in relational.get('door_reviews',[]):
        names = [s.get('space_name') or '미식별' for s in row['sides']]
        cases.append({'case_id':row['id'],'name':'출입구 · '+' ↔ '.join(names),'kind':'Connection',
                      'subject_ids':[row['opening_id'],row['door_id']],
                      'bounds_native':row['window_native'],'analysis_state':row['after']['status'],
                      'review_state':'PENDING_ORIGINAL_REVIEW','finding':row['after']['reason'],
                      'segment_native':row['segment_native'],'unresolved':row['after'].get('unresolved',[])})
    for row in relational.get('code_reviews',[]):
        cases.append({'case_id':row['id'],'name':row['code'],'kind':'SymbolReference',
                      'subject_ids':[row['id']],'bounds_native':row['window_native'],
                      'analysis_state':'UNRESOLVED','review_state':'PENDING_ORIGINAL_REVIEW',
                      'finding':row['reason'],'bbox_native':row['bbox_native']})
    functions = relational.get('functional_review',{})
    for row in functions.get('observations',[]):
        shared = (functions.get('after') or {}).get('shared_region')
        bounds = row['bbox_native']
        if shared:
            rects = shared['rects_native_xyxy']
            bounds = [min(r[0] for r in rects),min(r[1] for r in rects),
                      max(r[2] for r in rects),max(r[3] for r in rects)]
        cases.append({'case_id':row['id'],'name':row['normalized']+' 재관찰','kind':'FunctionalSpace',
                      'subject_ids':[row['id']],'bounds_native':bounds,'shared_region':shared,
                      'analysis_state':(functions.get('after') or {}).get('status','HOLD'),
                      'review_state':'PENDING_ORIGINAL_REVIEW','finding':(functions.get('after') or {}).get('reason','UNKNOWN')})
    return cases


def build_reference_model(geometry, observation, *, expected_role="Ref"):
    """Build an aggregate, with explicit JSON pointers into its enclosing bundle."""
    binding = geometry['source_binding']
    if expected_role not in ('Ref','Target') or binding['source_role']!=expected_role:
        raise ValueError('Independent Reference input required')
    assert_same_source(binding,observation['source_binding'])
    if geometry['parent_observation_run_id']!=observation['run_id']:
        raise ValueError('Different observation revision')
    domain_key='reference_domains' if expected_role=='Ref' else 'target_domains'
    relation_key='reference_relations' if expected_role=='Ref' else 'target_relations'
    model_key='reference_model' if expected_role=='Ref' else 'target_model'
    for key in ('space_closure',domain_key,relation_key):
        stage = geometry.get(key)
        if stage and (stage['source_binding']!=geometry['architectural_assessment']['source_binding']
                      or stage['frame_id']!=binding['frame_id']):
            raise ValueError('Mixed downstream revision')
    for row in geometry['observations']+geometry['entities']+observation['observations']:
        if row['source_role']!=expected_role or row['frame_id']!=binding['frame_id']:
            raise ValueError('Mixed evidence frame')
    index = {}
    def register(identity, kind, pointer, state):
        if identity in index:
            return
        index[identity] = {'id':identity,'kind':kind,'pointer':pointer,'claim_status':state}
    for group, key, kind in [('observations','observation_id','Observation'),('entities','entity_id','GeometryCandidate'),
                              ('evidence','evidence_id','Evidence')]:
        for i,row in enumerate(geometry[group]):
            register(row[key],row.get('element_type',kind),f'/{group}/{i}',row.get('claim_status','OBSERVED_UNREVIEWED'))
    # Full uncapped geometry inputs remain reproducible from the source PDF.
    # Preserve parent observations dropped by the geometry display/export limits.
    supplement = []
    for row in observation['observations']:
        if row['observation_id'] not in index:
            i = len(supplement)
            supplement.append(copy.deepcopy(row))
            register(row['observation_id'],'Observation',f'/{model_key}/observation_supplement/{i}',row.get('claim_status','OBSERVED_UNREVIEWED'))
    partition = geometry.get('space_closure') or {}
    for group in ('regions','unlabelled_regions'):
        for i,row in enumerate(partition.get(group,[])):
            register(row['region_id'],'PartitionRegion',f'/space_closure/{group}/{i}',row.get('claim_status','PROPOSED'))
    assessment = geometry['architectural_assessment']
    for i,parent in enumerate(assessment['structural_parents']):
        for j,f in enumerate(parent['fragments']):
            register(f['fragment_id'],'BoundaryFragment',f'/architectural_assessment/structural_parents/{i}/fragments/{j}',f['claim_status'])
    for group,key,kind in [('openings','opening_id','Opening'),('installed_elements','installation_id','Installation')]:
        for i,row in enumerate(assessment[group]):
            register(row[key],kind,f'/architectural_assessment/{group}/{i}',row['claim_status'])
    relational = geometry.get('reference_relations' if expected_role=='Ref' else 'target_relations') or {}
    for i,row in enumerate(relational.get('functional_entities',[])):
        register(row['id'],row['type'],f'/{relation_key}/functional_entities/{i}',row['claim_status'])
    function = relational.get('functional_review',{})
    if function:
        register(function['id'],'Reobservation',f'/{relation_key}/functional_review','OBSERVED_UNREVIEWED')
    for i,row in enumerate(function.get('observations',[])):
        register(row['id'],'TextRegion',f'/{relation_key}/functional_review/observations/{i}','OBSERVED_UNREVIEWED')
    for group in ('door_reviews','code_reviews'):
        for i,row in enumerate(relational.get(group,[])):
            pointer = f'/{relation_key}/{group}/{i}'
            register(row['id'],'Symbol' if group=='code_reviews' else 'Reobservation',pointer,'PROPOSED')
            if group=='code_reviews':
                register(row['text']['id'],'TextRegion',pointer+'/text','OBSERVED_UNREVIEWED')
                if row['number']:
                    register(row['number']['id'],'TextRegion',pointer+'/number','OBSERVED_UNREVIEWED')
    for i,face in enumerate(partition.get('faces',[]),1):
        # BoundaryOfElement subjects are scoped to the relation run.
        run = f'{binding["frame_id"]}:relations:{geometry["run_id"]}'
        register(f'{run}:face:{i}','PhysicalBoundary',f'/space_closure/faces/{i-1}','PROPOSED')
    unresolved = []
    graph = []
    for i,row in enumerate(relational.get('relations',[])):
        missing = [s['id'] for s in (row['source'],row['target']) if s['id'] not in index]
        missing += [r for r in row['evidence_refs'] if r not in index]
        graph.append({'id':row['id'],'kind':row['kind'],'source_id':row['source']['id'],
                      'target_id':row['target']['id'],'evidence_refs':row['evidence_refs'],
                      'claim_status':row['claim_status'],'pointer':f'/{relation_key}/relations/{i}',
                      'reference_integrity':'UNRESOLVED' if missing else 'RESOLVED'})
        if missing:
            unresolved.append({'relation_id':row['id'],'missing_refs':sorted(set(missing))})
    revision_claims = []
    for label in ('before_claim','after_claim'):
        if function.get(label):
            revision_claims.append({**copy.deepcopy(function[label]),'revision':label,'pointer':f'/{relation_key}/functional_review/'+label})
    cases = model_review_cases(geometry,expected_role=expected_role)
    prerequisites = {
        'simple_spaces':any(c['kind']=='Space' and c['polygon_native'] for c in cases),
        'nonrectangular':any(c['kind']=='Space' and (not c['polygon_native'] or len(c['polygon_native']['outer_rings'][0])>4) for c in cases),
        'passage_and_shaft':any(r['kind']=='Connection' for r in graph) and any(c['name']=='PS' and c['analysis_state']=='PROPOSED' for c in cases),
        'shared_domain':bool((function.get('after') or {}).get('shared_region')),
        'revision':bool(function.get('stale_claim_ids')),
    }
    return {'schema_version':'reference_understanding_model/1' if expected_role=='Ref' else 'target_understanding_model/1','run_id':geometry['run_id'],
            'source_role':expected_role,'frame_id':binding['frame_id'],'source_binding':copy.deepcopy(binding),
            'status':'PARTIAL','source_revision':{'observation_run_id':observation['run_id'],
                'geometry_run_id':geometry['run_id'],'observation_sha256':sha(observation),
                'stage_contracts':{k:geometry[k]['schema_version'] for k in ('space_closure',domain_key,relation_key) if geometry.get(k)}},
            'revision_claims':revision_claims,'observation_supplement':supplement,'inventory':list(index.values()),'relations':graph,'review_cases':cases,
            'reference_audit':{'unresolved_relation_refs':unresolved,'status':'FAILED' if unresolved else 'RESOLVED'},
            'coverage':{'observation':copy.deepcopy(observation['coverage']),'geometry':copy.deepcopy(geometry['coverage']),
                        'domain_status':(geometry.get(domain_key) or {}).get('status','NOT_RUN'),
                        'processing_limits':{'geometry':geometry['coverage']['reasons'],
                                             'relation_reobservation_budget':relational.get('reobservation_budget')}},
            'gate':{'status':'NOT_PASSED','criteria':[{'id':key,'label':label,
                      'candidate_evidence_available':prerequisites[key],
                      'review_status':'PENDING_INDEPENDENT_REVIEW'} for key,label in GATE_REQUIREMENTS]},
            'evaluation':{'status':'PENDING_INDEPENDENT_GT','metrics':None,'visual_review_state':'PENDING_ORIGINAL_REVIEW',
                          'scope_native':copy.deepcopy(binding['core_bbox_native'])},
            'claim_counts':dict(Counter(r['claim_status'] for r in index.values())),
            'preserved_outputs':{'geometry':'/entities','rejections':'/audit','counterevidence':'/evidence',
                                 'alternatives':'/space_closure/regions','revision_history':f'/{relation_key}/functional_review'},
            'serialization':'Pointers resolve against the enclosing architectural_geometry_bundle. Export the complete bundle.'}
