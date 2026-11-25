"""Read-only bridge from actual marker readings to the versioned v0.5 graph.

Reuses the existing user glossary. Does not run OCR, find a physical referent,
create Door/Window instances, compare roles or overwrite an input bundle.
"""
from copy import deepcopy
import hashlib,json
from pathlib import Path
from .schema import BASE_ELEMENT_CODES
from . import understanding_contract_v0_5 as contract
from .opening_graph_v0_5 import validate_graph


def from_marker_readings(result):
    binding=deepcopy(result['source_binding']);role=binding['source_role']
    if role not in ('Ref','Target') or result.get('source_role')!=role or result.get('frame_id')!=binding['frame_id']:
        raise ValueError('own-source marker result required')
    binding.setdefault('origin','CURRENT_INPUT')
    graph={'schema_version':contract.VERSION,'graph_id':'marker-types:'+hashlib.sha256(json.dumps(result,sort_keys=True,ensure_ascii=False).encode()).hexdigest()[:24],
        'source_binding':binding,'input_results_reused':True,'new_image_inference_performed':False,
        'notice':'Observed type-code candidates only. No physical instance assignment, GT or Ref/Target matching.',
        **{key:[] for key in ['observations','entities','relations','claims','derivations','alternatives','external_evidence',*contract.EXTENSION_GROUPS]}}
    def record(group,identity,**fields):
        row=dict(id=identity,source_role=role,frame_id=binding['frame_id'],**fields);graph[group].append(row);return row
    glossary=Path(__file__).with_name('vocabulary.py');sha=hashlib.sha256(glossary.read_bytes()).hexdigest()
    record('external_evidence','initial-glossary-source',evidence_origin='USER_INITIAL_GLOSSARY',
        source_document_sha256=sha,definition_source='integrations/fpue_ontology/vocabulary.py:BASE_ELEMENT_CODES')
    record('catalogs','initial-glossary',scope='INITIAL_USER_VOCAB',origin='USER_INITIAL_GLOSSARY',document_sha256=sha,
        source_evidence_refs=['initial-glossary-source'])
    known={code:dict(category=kind,name=name,material=mat,function=feature) for code,kind,name,mat,feature in BASE_ELEMENT_CODES}
    observations={};entries={}
    def geometry(box):
        x,y,u,v=box
        return dict(kind='Polygon',frame_id=binding['frame_id'],coordinate_system='image_native_pixels',
                    outer_rings=[[[x,y],[u,y],[u,v],[x,v]]],holes=[])
    for marker in result.get('code_reviews',[]):
        refs=[]
        for text in [marker['text'],marker.get('number')]:
            if not text:continue
            if text.get('frame_id',binding['frame_id'])!=binding['frame_id'] or text.get('source_role',role)!=role:
                raise ValueError('foreign text in marker reading')
            if text.get('source_sha256',binding['source_sha256'])!=binding['source_sha256'] or text.get('image_sha256',binding['image_sha256'])!=binding['image_sha256']:
                raise ValueError('marker text source bytes mismatch')
            ident=text['id'];refs.append(ident)
            if ident not in observations:
                crop=text.get('geometry_semantics')=='RECOGNITION_CROP_EXTENT_NOT_DETECTED_TEXT_BOX'
                observations[ident]=record('observations',ident,observation_type='RecognitionCrop' if crop else 'TextRegion',
                    text=text.get('raw',text.get('normalized','')),geometry=geometry(text['bbox_native']),
                    geometry_semantics=text.get('geometry_semantics','OBSERVED_TEXT_EXTENT'),raw_observation=deepcopy(text))
        refs=list(dict.fromkeys(refs));symbol_id=marker['id'];symbol_obs=symbol_id+':native-observation'
        record('observations',symbol_obs,observation_type='Symbol',geometry=geometry(marker['bbox_native']),
            geometry_semantics='OBSERVED_MARKER_BOUNDING_EXTENT',raw_observation=deepcopy(marker['symbol_path']))
        record('entities',symbol_id,expected_type='Symbol',grounding_status='PARTIAL',geometry_state='POLYGON_AVAILABLE',
            geometry=geometry(marker['bbox_native']),source_evidence_refs=[symbol_obs,*refs])
        code=marker.get('canonical_code');entry_id=None
        if code:
            family,number=contract.split_code(code);entry_id='initial-entry:'+code
            if code not in entries:
                definition=known.get(code)
                entries[code]=record('catalog_entries',entry_id,catalog_id='initial-glossary',code=code,family=family,type_number=number,
                    definition_status='INITIAL_GLOSSARY' if definition else 'UNRESOLVED',
                    source_evidence_refs=['initial-glossary-source'] if definition else [],**(definition or {}))
        else:family=marker['code_family'];number=None
        reading={'SUPPORTED':'COMPLETE','PARTIAL':'PARTIAL','AMBIGUOUS':'AMBIGUOUS'}[marker['reading_status']]
        record('type_designations',symbol_id+':type-designation',symbol_id=symbol_id,canonical_code=code,
            family=family,type_number=number,entry_id=entry_id,reading_status=reading,status='PROPOSED',
            reading_observation_ids=refs,source_evidence_refs=refs,
            original_reading_status=marker['reading_status'],physical_referent_verified=False)
    return validate_graph(graph)
