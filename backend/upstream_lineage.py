"""Source-bound observation → hypothesis → catalog → comparison journals.

This is an evidence ledger, never a new gate, detector or spatial association.
Old missing histories remain explicit gaps. The existing Reference/Target model
inventory supplies JSON pointers; actual producer journals supply lost records.
"""
import base64,hashlib,json
from collections import Counter
from backend.observation_trace import TraceJournal,iter_records,stream_metadata,native_bounds,VERSION as producer_version

VERSION='fpue_upstream_lineage/1'
REFERENCE_KEYS={'observation_refs','source_observation_refs','evidence_refs','source_refs','parent_display_observation_refs',
    'host_parent_ids','unresolved_host_ids','parent_structure_id','source_geometry_entity_id','opening_id','boundary_ids',
    'observation_id','context_refs','refs','related_text_ids','source_line_ids','space_id','door_id','fragment_ids',
    'source_id','target_id','subject_id','supporting_observation_ids','parallel_observation_ids','line_observation_id',
    'marker_id','element_id','candidate_relation_ids','element_grounding_review_id','element_grounding_entity_ids','comparison_source_id'}

def pointer_value(bundle,pointer):
    current=bundle
    for part in pointer.strip('/').split('/'):
        part=part.replace('~1','/').replace('~0','~')
        current=current[int(part)] if isinstance(current,list) else current[part]
    return current

def explicit_references(row):
    out=[]
    def values(value):
        if isinstance(value,str):out.append(value)
        elif isinstance(value,(list,tuple)):
            for v in value:values(v)
    def walk(value):
        if isinstance(value,dict):
            for key,child in value.items():
                if key in REFERENCE_KEYS:values(child)
                elif key in ['text','number','source','target'] and isinstance(child,dict) and isinstance(child.get('id'),str):out.append(child['id'])
                if key not in ['geometry_native','geometry_properties','source_binding','symbol_path','polygons_native','points_native']:
                    if isinstance(child,(dict,list)):walk(child)
        elif isinstance(value,list):
            for child in value:
                if isinstance(child,dict):walk(child)
    walk(row);return list(dict.fromkeys(out))

def _geometry(row):
    geometry=row.get('geometry_native') or {}
    if geometry.get('bbox_native') is not None:return geometry['bbox_native']
    if geometry.get('points_native'):return native_bounds(geometry['points_native'])
    bounds=row.get('bbox_native',row.get('bbox'))
    if bounds is not None:return bounds
    points=[]
    for key in ['opening_segment_native','points_native']:
        points.extend(row.get(key) or [])
    for segment in row.get('boundary_faces_native',[]):points.extend(segment)
    rings=row.get('polygon_native') or {}
    if isinstance(rings,dict):
        for ring in rings.get('outer_rings',[]):points.extend(ring)
    for run in row.get('boundary_runs',[]):points.extend(run.get('points_native',[]))
    if not points and row.get('seed_native'):points=[row['seed_native']]
    return native_bounds(points) if points else None

def _normalize_ref(ref,g):
    frame=g['frame_id'];role=g['source_role']
    for prefix in [role+':pdf_path:',frame+':pdf_path:']:
        if ref.startswith(prefix):return frame+':primitive:PDF:pdf_path:'+ref[len(prefix):]
    if ref.startswith(('pdf:line:','raster:line:')):
        return frame+':geometry:'+g['run_id']+':registry-key:'+ref
    return ref

def _source_graph(g,result,accepted_producer_stages=None):
    role=g['source_role'];frame=g['frame_id'];binding={**g.get('source_binding',{}),'source_role':role,'frame_id':frame}
    journal=TraceJournal(binding,g['run_id'],'UPSTREAM_GRAPH');seen=set();retained_obs=set();gaps=[]
    def register(identity,kind,row,pointer,phase):
        if not isinstance(identity,str) or not identity:return
        key=(identity,pointer,phase)
        if key in seen:return
        seen.add(key)
        if row.get('frame_id',frame)!=frame or row.get('source_role',role)!=role:
            journal.record(identity,kind,'QUARANTINED','FOREIGN_FRAME_OR_ROLE_ENTRY',pointer=pointer,stage=phase);return
        refs=[_normalize_ref(v,g) for v in explicit_references(row) if v!=identity]
        other='Target:' if role=='Ref' else 'Ref:'
        foreign=[v for v in refs if v.startswith(other)]
        refs=[v for v in refs if v not in foreign]
        geometry=row.get('geometry_properties') or {}
        reason=row.get('decision') or row.get('evidence_status') or row.get('claim_status') or 'EXISTING_BUNDLE_ENTRY'
        journal.record(identity,kind,str(reason),'EXPLICIT_EXISTING_ENTRY_AND_REFERENCES',bounds=_geometry(row),
            input_ids=refs,pointer=pointer,stage=phase,original_decision=row.get('decision'),
            original_reason_codes=row.get('reasons',row.get('uncertainty_reason',[])),
            classification=geometry.get('classification'),boundary_truncated=row.get('boundary_truncated'),
            quarantined_reference_ids=foreign,claim_status=row.get('claim_status','UNSPECIFIED_NOT_PROMOTED'))
        if row.get('observation_id')==identity or phase in ['OBSERVATION_REGISTRY','OBSERVATION_SUPPLEMENT','LOCAL_REOBSERVATION']:retained_obs.add(identity)

    model_key='reference_model' if role=='Ref' else 'target_model';model=g.get(model_key) or {}
    for entry in model.get('inventory',[]):
        try:
            row=pointer_value(g,entry['pointer'])
            actual={row.get(k) for k in ['observation_id','entity_id','evidence_id','fragment_id','region_id','opening_id','installation_id','id']}
            if entry['id'] not in actual and entry['kind']!='PhysicalBoundary':
                gaps.append(dict(kind='STALE_INVENTORY_POINTER',subject_id=entry['id'],pointer=entry['pointer']));continue
            register(entry['id'],entry['kind'],row,entry['pointer'],'MODEL_INVENTORY')
        except (KeyError,IndexError,TypeError,ValueError):gaps.append(dict(kind='UNRESOLVED_INVENTORY_POINTER',subject_id=entry.get('id'),pointer=entry.get('pointer')))
    for group,key,kind,phase in [('observations','observation_id','Observation','OBSERVATION_REGISTRY'),
                               ('entities','entity_id','GeometryCandidate','GEOMETRIC_HYPOTHESIS'),('evidence','evidence_id','Evidence','GEOMETRIC_EVIDENCE')]:
        for i,row in enumerate(g.get(group,[])):register(row.get(key),row.get('element_type',row.get('observation_type',kind)),row,f'/{group}/{i}',phase)
    for i,row in enumerate(model.get('observation_supplement',[])):
        register(row['observation_id'],row['observation_type'],row,f'/{model_key}/observation_supplement/{i}','OBSERVATION_SUPPLEMENT')
    assessment=g.get('architectural_assessment') or {}
    for i,parent in enumerate(assessment.get('structural_parents',[])):
        for j,row in enumerate(parent.get('fragments',[])):
            register(row['fragment_id'],'WallFragment',row,f'/architectural_assessment/structural_parents/{i}/fragments/{j}','ARCHITECTURAL_ASSESSMENT')
    for group,key,kind in [('openings','opening_id','Opening'),('installed_elements','installation_id','Installation')]:
        for i,row in enumerate(assessment.get(group,[])):register(row[key],kind,row,f'/architectural_assessment/{group}/{i}','ARCHITECTURAL_ASSESSMENT')
    for group in ['regions','unlabelled_regions']:
        for i,row in enumerate(g.get('space_closure',{}).get(group,[])):
            register(row.get('region_id',row.get('observation_id')),'SpaceRegion',row,f'/space_closure/{group}/{i}','SPACE_HYPOTHESIS')
    relation_key='reference_relations' if role=='Ref' else 'target_relations';relations=g.get(relation_key) or {}
    for i,row in enumerate(relations.get('code_reviews',[])):
        pointer=f'/{relation_key}/code_reviews/{i}';register(row.get('id'),'CodeMarker',row,pointer,'SEMANTIC_REVIEW')
        for key in ['text','number']:
            if row.get(key):register(row[key]['id'],'TextGlyph',row[key],pointer+'/'+key,'SYMBOL_GLYPH')
    for i,row in enumerate(relations.get('code_reobservations',{}).get('observations',[])):
        register(row.get('id'),'TextRegion',row,f'/{relation_key}/code_reobservations/observations/{i}','LOCAL_REOBSERVATION')
    for i,row in enumerate(result['source_objects'][role]):
        register(row['id'],row['kind'],row,f'/source_objects/{role}/{i}','COMPARISON_SOURCE_OBJECT')
    grounding=result.get('element_grounding',{}).get('sources',{}).get(role,{})
    for group,kind in [('entities','ElementHypothesis'),('relations','RelationHypothesis'),('marker_reviews','InstanceReferenceReview')]:
        for i,row in enumerate(grounding.get(group,[])):
            register(row['id'],row.get('kind',kind),row,f'/element_grounding/sources/{role}/{group}/{i}','ELEMENT_GROUNDING')
    # Existing evidence edges are transcribed, not inferred from proximity.
    for row in result['correspondences']:
        ids=row['ref_ids' if role=='Ref' else 'target_ids']
        if not ids:continue
        common='comparison:'+result['input_fingerprint']+':'+row['correspondence_id']
        journal.record(common,row['entity_type'],'COMPARISON_RECORDED',row['judgment'],input_ids=ids,
                       output_ids=[row['candidate_id']],stage='FINAL_COMPARISON',correspondence_id=row['correspondence_id'])
        automatic=next(a for a in result['candidate_audit']['records'] if a['correspondence_id']==row['correspondence_id'])['stages']['automatic_output']
        journal.record(row['candidate_id'],row['entity_type'],automatic['status'],
            automatic['reason_codes'][0] if automatic['reason_codes'] else 'NO_REASON_RECORDED',
            input_ids=[common,*[_normalize_ref(v,g) for v in row['evidence_refs'] if not v.startswith('Target:' if role=='Ref' else 'Ref:')]],
            output_ids=row.get('represented_by_correspondence_ids',[]),stage='AUTOMATIC_OUTPUT',
            correspondence_id=row['correspondence_id'],decision_origin='ANALYSIS_SNAPSHOT_NOT_USER_REVIEW')

    producer_stages=set(accepted_producer_stages or [])
    if 'OBSERVATION_ATTEMPT' not in producer_stages:
        gaps.append(dict(kind='LEGACY_OBSERVATION_ATTEMPT_HISTORY_NOT_AVAILABLE',
            aggregate_display_omissions=g.get('coverage',{}).get('parent_display_omissions',{}),
            inference_loss='NOT_ESTABLISHED: geometry reextracts source PDF independently'))
    if 'GEOMETRY_INPUT_AND_EXPORT' not in producer_stages:
        gaps.append(dict(kind='LEGACY_RAW_REGISTRY_AND_FILTER_HISTORY_NOT_AVAILABLE',
            aggregate_registry=g.get('coverage',{}).get('evidence_registry',{})))
        for modality,audit in g.get('audit',{}).get('filter_decisions_crop_coordinates',{}).items():
            line_filter=audit.get('line_filter') or {};examples=line_filter.get('examples',[])
            for example in examples:
                source=example.get('source',{});raw=source.get('id')
                if not raw:continue
                x0,y0=g.get('source_binding',{}).get('context_bbox_native',[0,0])[:2]
                points=[[p[0]+x0,p[1]+y0] for p in source.get('points',[])]
                alias=frame+':primitive:'+('PDF' if modality=='pdf' else 'RASTER:'+g['run_id'])+':'+raw
                journal.record(alias,modality+'Line','LEGACY_RECORDED_FILTER_EXAMPLE',example['reason'],points=points,
                               stage='LEGACY_FILTER_EXAMPLE',sample_is_complete_history=False)
            gaps.append(dict(kind='LEGACY_FILTER_COUNTS_EXCEED_ID_LEVEL_HISTORY',modality=modality,
                rejected_counts=line_filter.get('rejected_counts',{}),example_count=len(examples)))
    curve=g.get('coverage',{}).get('curve_inspection_scope',{})
    understanding_limits=[]
    if curve.get('compound_curve_primitives_not_interpreted'):
        understanding_limits.append(dict(kind='COMPOUND_CURVES_NOT_INTERPRETED',count=curve['compound_curve_primitives_not_interpreted'],
                         individual_producer_decisions_recorded='GEOMETRY_INPUT_AND_EXPORT' in producer_stages,
                         meaning='Processing limitation, not proof of a missing wall/door and not automatically a trace gap.'))
    for gap in gaps:
        journal.record(gap.get('subject_id'),'TraceGap','TRACE_GAP',gap['kind'],stage='TRACE_COVERAGE',gap=gap)
    return journal.finish(),dict(source_role=role,frame_id=frame,retained_observation_id_count=len(retained_obs),
                                trace_gaps=gaps,understanding_limits=understanding_limits,producer_stages=sorted(s for s in producer_stages if s))

def attach_upstream_lineage(result,geometries):
    streams=result.pop('_catalog_trace_streams',[]);sources=[];integrity=[]
    for g in geometries:
        accepted_stages=set()
        for stream in g.get('producer_traces',[]):
            if not isinstance(stream,dict):
                integrity.append(dict(source_role=g['source_role'],frame_id=g['frame_id'],kind='MALFORMED_PRODUCER_TRACE'));continue
            binding=stream.get('source_binding',{})
            keys=['source_role','frame_id','source_sha256','image_sha256','page','extraction_zoom','width','height']
            expected={**g.get('source_binding',{}),'source_role':g['source_role'],'frame_id':g['frame_id']}
            try:
                if any(binding.get(k)!=expected.get(k) for k in keys):raise ValueError('Frame mismatch')
                if stream.get('schema_version')!=producer_version or stream.get('encoding')!='gzip+base64:jsonl':raise ValueError('Unknown format')
                raw=base64.b64decode(stream['payload_base64'],validate=True)
                if hashlib.sha256(raw).hexdigest()!=stream['payload_sha256']:raise ValueError('Digest mismatch')
                streams.append(stream)
                accepted_stages.add(stream['stage'])
            except (ValueError,KeyError,TypeError):integrity.append(dict(source_role=g['source_role'],frame_id=g['frame_id'],kind='PRODUCER_TRACE_INTEGRITY_FAILED'))
        stream,summary=_source_graph(g,result,accepted_stages);streams.append(stream);sources.append(summary)
    metadata=[stream_metadata(s) for s in streams]
    result['upstream_lineage']=dict(schema_version=VERSION,status='PARTIAL_WITH_DECLARED_GAPS' if integrity or any(s['trace_gaps'] for s in sources) or any(s['unrecorded_record_count'] or s.get('serialization_error_count',0) for s in streams) else 'RECORDED_STAGE_HISTORY_AVAILABLE',
        source_summaries=sources,stream_metadata=metadata,integrity_gaps=integrity,
        recorded_events=sum(s['record_count'] for s in streams),unrecorded_events=sum(s['unrecorded_record_count'] for s in streams),
        serialization_errors=sum(s.get('serialization_error_count',0) for s in streams),
        no_new_detection_or_semantic_inference=True,missing_history_is_not_negative_evidence=True,
        payload_integrity_scope='SOURCE_BINDING_AND_SHA_CHECKED; FULL_RECORD_DECODING_ON_DETAIL_REQUEST',
        scope='Actual observation attempts, existing hypotheses/explicit refs, catalog selection and comparison; never unobserved detector misses.',
        detail_url='/api/comparison-reviews/'+result['run_id']+'/upstream-trace',streams=streams)
    return result

def public_comparison(result):
    if 'upstream_lineage' not in result:return result
    return {**result,'upstream_lineage':{k:v for k,v in result['upstream_lineage'].items() if k!='streams'}}

def trace_records(result):
    for stream in result.get('upstream_lineage',{}).get('streams',[]):
        try:yield from iter_records(stream)
        except (ValueError,KeyError,TypeError,EOFError,OSError):
            yield dict(stage='TRACE_INTEGRITY',source_role=stream.get('source_binding',{}).get('source_role'),
                       frame_id=stream.get('source_binding',{}).get('frame_id'),status='TRACE_UNAVAILABLE',
                       reason_codes=['TRACE_PAYLOAD_COULD_NOT_BE_DECODED'],subject_id=stream.get('payload_sha256'))

def select_records(result,*,subject_id=None,role=None,stage=None,offset=0,limit=200):
    records=[];matched=0;more=False
    for r in trace_records(result):
        if role and r.get('source_role')!=role:continue
        if stage and r.get('stage')!=stage:continue
        if subject_id and subject_id not in [r.get('subject_id'),r.get('record_id'),r.get('correspondence_id'),*r.get('input_ids',[]),*r.get('output_ids',[])]:continue
        if matched>=offset:
            if len(records)>=limit:more=True;break
            records.append(r)
        matched+=1
    return dict(records=records,offset=offset,limit=limit,has_more=more,
                relation_scope='DIRECT_EXPLICIT_REFERENCES; follow input/output IDs for further stages')

def jsonl_chunks(result,*,subject_id=None,role=None,stage=None):
    buffer=[];size=0
    for record in trace_records(result):
        if role and record.get('source_role')!=role:continue
        if stage and record.get('stage')!=stage:continue
        if subject_id and subject_id not in [record.get('subject_id'),record.get('record_id'),record.get('correspondence_id'),*record.get('input_ids',[]),*record.get('output_ids',[])]:continue
        line=(json.dumps(record,ensure_ascii=False,separators=(',',':'),allow_nan=False)+'\n').encode()
        buffer.append(line);size+=len(line)
        if size>=65536:yield b''.join(buffer);buffer=[];size=0
    if buffer:yield b''.join(buffer)
