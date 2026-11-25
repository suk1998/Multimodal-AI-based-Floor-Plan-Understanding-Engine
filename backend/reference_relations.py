"""Same-frame relation grounding over the Reference geometry revision."""
from collections import Counter
import math

import cv2
import numpy as np
import pymupdf

from backend.reference_domains import components, derive_reference_domains
from backend.reference_restoration import digest, reobserve
from backend.symbol_codes import marker_reading, recover_numbers
from integrations.fpue_ontology import understanding_contract_v0_4 as ontology
from integrations.fpue_ontology.vocabulary import normalize_label
from poc.space_grounding_geometry import sample_line, transform_points

FUNCTIONS = {'거실', '주방/식당'}
MAX_DOOR_REOBSERVATIONS = 3


def inside(box, window):
    return window[0] <= box[0] <= box[2] <= window[2] and window[1] <= box[1] <= box[3] <= window[3]


def rectangle(points):
    x, y = np.asarray(points).T
    return [float(x.min()), float(y.min()), float(x.max()), float(y.max())]


def expand(box, scope, margin):
    return [max(math.floor(scope[0]), math.floor(box[0]-margin)),
            max(math.floor(scope[1]), math.floor(box[1]-margin)),
            min(math.ceil(scope[2]), math.ceil(box[2]+margin)),
            min(math.ceil(scope[3]), math.ceil(box[3]+margin))]


def relation(run, kind, source, target, refs, status='SUPPORTED', **fields):
    ontology.validate_relation_signature(kind, source['type'], target['type'])
    if source['id']==target['id'] or not refs:
        raise ValueError('Relation requires distinct subjects and source evidence')
    via = fields.get('via_entity_id')
    identity = f'{run}:relation:{kind}:{source["id"]}:{target["id"]}'
    return {'id':identity if not via else f'{identity}:via:{via}', 'kind':kind,
            'source':source,'target':target,'evidence_refs':refs,'claim_status':status, **fields}


def subject(identity, kind):
    return {'id':identity, 'type':kind}


def pdf_text(page, matrix, scope, prefix):
    flags = pymupdf.TEXTFLAGS_DICT & ~pymupdf.TEXT_PRESERVE_IMAGES
    for b, block in enumerate(page.get_text('dict',flags=flags)['blocks']):
        for n, line in enumerate(block.get('lines',[])):
            box = line['bbox']
            corners = transform_points([[box[0],box[1]],[box[2],box[1]],[box[2],box[3]],[box[0],box[3]]],matrix)
            native = rectangle(corners)
            if inside(native,scope):
                raw = ''.join(span['text'] for span in line['spans'])
                yield {'id':f'{prefix}:pdf-text:{b}:{n}', 'raw':raw,'normalized':normalize_label(raw),
                       'bbox_native':native,'polygon_native':corners,'bbox_pdf_local':list(box)}


def local_text(page, row, binding):
    """Reread the original span in its local PDF clip and keep the rendered view hash."""
    rect = pymupdf.Rect(row['bbox_pdf_local']) + (-2,-2,2,2)
    zoom = min(4, binding['extraction_zoom']*2)
    pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom,zoom),clip=rect,alpha=False)
    raw = page.get_text('text',clip=rect).strip()
    return {'raw_reread':raw,'status':'COMPLETED','input_png_sha256':digest(pix.tobytes('png')),
            'clip_pdf_local':list(rect),'render_zoom':zoom,'render_size':[pix.width,pix.height],
            'render_to_native':[[binding['extraction_zoom']/zoom,0,pix.x*binding['extraction_zoom']/zoom],
                                [0,binding['extraction_zoom']/zoom,pix.y*binding['extraction_zoom']/zoom],[0,0,1]],
            'same_source_dependency':binding['source_sha256']}


def recover_functions(page, rows, partition, domains, assessment, observations, entities, prefix):
    before = domains.get('open_plan',{'status':'HOLD','reason':domains['reason']})
    existing = {r['name'] for r in partition['regions'] if r['functional_label']}
    missing = FUNCTIONS-existing
    attempt = {'id':prefix+':functions','question':'CHECK_RESIDUAL_COVERAGE','before':before,
               'after':before,'status':'SKIPPED','reason':'NO_RELEVANT_MISSING_FUNCTION_LABEL',
               'window_native':partition.get('scope_native'),'observations':[], 'stale_claim_ids':[],
               'before_claim':{'id':prefix+':function-interpretation:r0','status':'PROPOSED'},
               'after_claim':{'id':prefix+':function-interpretation:r0','status':'PROPOSED'}}
    if before['reason']!='MISSING_OR_AMBIGUOUS_FUNCTION_LABELS' or not missing:
        return attempt
    attempt.update(status='COMPLETED',reason='NO_UNIQUE_LOCAL_PDF_TEXT_MATCH')
    additions = []
    for name in sorted(missing):
        matches = [r for r in rows if r['normalized']==name]
        if len(matches)!=1:
            continue
        row = dict(matches[0])
        if 'bbox_pdf_local' not in row:
            attempt['observations'].append({**row,'reobservation':{'status':'NOT_EXECUTED','reason':'OCR_LOCAL_RECOGNITION_REQUIRED'}})
            continue
        row['reobservation'] = local_text(page,row,assessment['source_binding'])
        attempt['observations'].append(row)
        if normalize_label(row['reobservation']['raw_reread'])!=name:
            continue
        x0,y0,x1,y1 = row['bbox_native']
        additions.append({'region_id':row['id']+':function','observation_id':row['id'],'name':name,
                          'seed_native':[(x0+x1)/2,(y0+y1)/2], 'functional_label':True,'polygon_native':None})
    if additions:
        # Re-evaluate the recovered labels against the existing domain.
        # Treat uncertain geometric gaps as a competing separator hypothesis.
        all_gaps = derive_reference_domains(partition,assessment,observations,entities,
                                           functional_reobservations=additions, expected_role=assessment["source_role"])
        supported_separators = [e for e in entities if
            e['geometry_properties'].get('architectural_admissibility',{}).get('decision')=='ADMISSIBLE_OPENING_GEOMETRY'
            or (e['element_type']=='Door' and e['geometry_properties'].get('gap_status')!='REJECTED_SOLID_OVERLAP')]
        narrowed = derive_reference_domains(partition,assessment,observations,supported_separators,
                                            functional_reobservations=additions, expected_role=assessment["source_role"])
        conditional = narrowed.get('open_plan',before)
        conservative = all_gaps.get('open_plan',before)
        attempt.update(after=conditional,reason='FUNCTION_LABEL_REOBSERVED',
                       competing_interpretation=conservative,
                       separator_policy='Retain geometry-admissible openings and non-contradicted swing doors; run all opening hypotheses as a competing interpretation',
                       considered_separator_ids=[e['entity_id'] for e in supported_separators],
                       stale_claim_ids=[prefix+':function-interpretation:r0'],
                       before_claim={'id':prefix+':function-interpretation:r0','status':'STALE'},
                       after_claim={'id':prefix+':function-interpretation:r1','status':'PROPOSED'})
    return attempt


def point_in_partition(point, row):
    poly = row.get('polygon_native')
    if not poly:
        return False
    def side(ring):
        return cv2.pointPolygonTest(np.asarray(ring,np.float32),tuple(point),False)
    return any(side(r)>=0 for r in poly['outer_rings']) and all(side(r)<0 for r in poly['holes'])


def boundary_matches(partition, assessment, prefix):
    fragments = {f['fragment_id']:f for p in assessment['structural_parents'] for f in p['fragments']
                 if f['decision']=='ADMISSIBLE_GEOMETRIC_HYPOTHESIS'}
    used = sorted({b['owner'] for r in partition['regions'] for b in r['boundary_runs'] if b['owner']>0})
    matches = []
    for owner in used:
        face = partition['faces'][owner-1]
        fragment = fragments.get(face['source_refs'][0])
        if fragment:
            matches.append(relation(prefix,'BoundaryOfElement',subject(f'{prefix}:face:{owner}','PhysicalBoundary'),
                subject(fragment['parent_structure_id'],'Wall'),[fragment['fragment_id'],*fragment['source_observation_refs']],
                segment_native=face['points_native'],claim_scope='Observed face membership of a proposed parent wall'))
    return matches


def doorway_sides(segment, rows, scale, residual_ids, scope):
    points = np.asarray(segment,float)
    delta = points[1]-points[0]
    length = np.linalg.norm(delta)
    if length<=0:
        return []
    normal = np.array([-delta[1],delta[0]])/length
    sides = []
    for sign in (-1,1):
        probes = []
        for fraction in (.30,.50,.70):
            hit = None
            for distance in np.linspace(2,min(length*.55,scale*2),13):
                p = (points[0]+fraction*delta+sign*distance*normal).tolist()
                if not inside([*p,*p],scope):
                    break
                memberships = [r for r in rows if point_in_partition(p,r)]
                if memberships:
                    hit = {'point_native':p,'region_ids':[r['region_id'] for r in memberships]}
                    break
                x,y = np.rint(p).astype(int)
                residual = int(residual_ids[y,x]) if 0<=y<residual_ids.shape[0] and 0<=x<residual_ids.shape[1] else 0
                if residual and hit is None:
                    hit = {'point_native':p,'region_ids':[],'residual_component':residual}
                    # Stop at the first region on this ray, even if unassigned.
                    break
            probes.append(hit)
        memberships = [p['region_ids'][0] if p and len(p['region_ids'])==1 else None for p in probes]
        ids = set(memberships)
        sides.append({'normal_sign':sign,'probes':probes,'space_id':memberships[0] if len(ids)==1 else None})
    return sides


def residual_components(domains, binding):
    mask = np.zeros((binding['height'],binding['width']),np.uint8)
    for x,y,u,v in domains['layers'].get('R',{}).get('rects_native_xyxy',[]):
        mask[y:v,x:u] = 1
    return components(mask)[1]


def inspect_doors(pdf_bytes, partition, domains, assessment, evidence, prefix):
    binding = assessment['source_binding']
    openings = {o['opening_id']:o for o in assessment['openings']}
    evidence_by_id = {e['evidence_id']:e for e in evidence}
    rows = [r for r in partition['regions'] if r['polygon_native'] and r['name']!='PS']
    scope, scale = partition['scope_native'], partition['label_scale_px']
    residual = residual_components(domains,binding)
    doors = [i for i in assessment['installed_elements'] if i['type_candidates']==['Door']]
    doors.sort(key=lambda i:(openings[i['opening_id']]['decision']!='ADMISSIBLE_OPENING_GEOMETRY',i['installation_id']))
    attempts = []
    relations = []
    for index, item in enumerate(doors):
        opening = openings[item['opening_id']]
        segment = opening['opening_segment_native']
        box = expand(opening['geometry_native']['bbox_native'],scope,1.5*scale)
        attempt = {'id':f'{prefix}:door:{index}','question':'GROUND_CONNECTION_ENDPOINTS','opening_id':opening['opening_id'],
                   'door_id':item['installation_id'],'window_native':box,'segment_native':segment,
                   'before':{'existence':'UNKNOWN','mode':'Unknown','opening_decision':opening['decision']},
                   'opening_reasons':opening['reasons'], 'reobservation':{'status':'NOT_EXECUTED_BUDGET'}}
        if index<MAX_DOOR_REOBSERVATIONS:
            observed, image = reobserve(pdf_bytes,binding,box,scale,attempt['id'],expected_role=binding['source_role'])
            attempt['reobservation'] = {k:v for k,v in observed.items() if k not in ('raster_segments_native','rejected_lines','lines','arcs')}
            if image is not None:
                gray = cv2.imdecode(np.frombuffer(image,np.uint8),cv2.IMREAD_GRAYSCALE)
                local = np.asarray(observed['rerender_to_native'])
                distance = cv2.distanceTransform((gray>=245).astype(np.uint8),cv2.DIST_L2,5)
                arc_checks = []
                for ref in item['evidence_refs']:
                    for support in evidence_by_id[ref]['supporting']:
                        if support['kind']!='SWING_LEAF_AND_BOTH_JAMBS':
                            continue
                        points = np.asarray(transform_points(support['swing_polyline_native'],np.linalg.inv(local)))
                        if np.any(points<0) or np.any(points>=np.array(gray.shape[::-1])):
                            continue
                        samples = distance[np.rint(points[:,1]).astype(int).clip(0,gray.shape[0]-1),
                                           np.rint(points[:,0]).astype(int).clip(0,gray.shape[1]-1)]
                        arc_checks.append({'arc_observation_ref':support['arc_observation_ref'],'visible_fraction':float(np.mean(samples<=2)),
                                           'both_jambs_supported':len(support['jamb_connections'])==2 and all(j['connected'] for j in support['jamb_connections'])})
                attempt['reobservation'].update(arc_checks=arc_checks, visible_source_lines=len(observed['lines']))
        sides = doorway_sides(segment,rows,scale,residual,scope)
        space_ids = [s['space_id'] for s in sides if s['space_id']]
        by_region = {r['region_id']:r['name'] for r in rows}
        for side in sides:
            side['space_name'] = by_region.get(side['space_id'])
        attempt['sides'] = sides
        arc_support = any(a['both_jambs_supported'] and a['visible_fraction']>=.8
                          for a in attempt['reobservation'].get('arc_checks',[]))
        # Sample crossing rays in the unit domain, after subtracting only
        # physical occupancy. The door swing itself is a symbol, not a wall.
        can_connect = False
        if arc_support and opening['decision']=='ADMISSIBLE_OPENING_GEOMETRY' and len(set(space_ids))==2:
            free = np.zeros(residual.shape,np.uint8)
            for x,y,u,v in domains['layers'].get('F',{}).get('rects_native_xyxy',[]):
                free[y:v,x:u] = 1
            rays = [[a['point_native'],b['point_native']] for a,b in zip(sides[0]['probes'],sides[1]['probes'])]
            can_connect = all(np.all(sample_line(free,ray)==1) for ray in rays)
            attempt['crossing_rays_native'] = rays
            attempt['crossing_within_free_domain'] = can_connect
        refs = [attempt['id'],*item['evidence_refs']]
        attempt['after'] = {'status':'SUPPORTED' if can_connect else 'HOLD',
                            'claims':[{'predicate':'connection_existence','value':'PRESENT' if can_connect else 'UNKNOWN',
                                       'status':'SUPPORTED' if can_connect else 'PROPOSED','evidence_refs':refs},
                                      {'predicate':'connection_mode','value':'Door' if can_connect else 'Unknown',
                                       'status':'SUPPORTED' if can_connect else 'PROPOSED','evidence_refs':refs}],
                            'reason':'TWO_SPACES_AND_CLEAR_CROSSING' if can_connect else
                            'HOST_OR_OPENING_UNRESOLVED' if opening['decision']!='ADMISSIBLE_OPENING_GEOMETRY' else
                            'TWO_NAMED_SIDES_NOT_GROUNDED' if len(set(space_ids))!=2 else 'SWING_OR_CROSSING_UNRESOLVED'}
        attempt['after']['unresolved'] = [
            reason for missing, reason in (
                (not arc_support,'LOCAL_SWING_OR_JAMBS_UNSUPPORTED'),
                (opening['decision']!='ADMISSIBLE_OPENING_GEOMETRY','HOST_OR_OPENING_UNRESOLVED'),
                (len(set(space_ids))!=2,'TWO_NAMED_SIDES_NOT_GROUNDED'),
                (len(set(space_ids))==2 and not can_connect,'PASSAGE_NOT_ESTABLISHED')) if missing]
        if can_connect:
            connection = relation(prefix,'Connection',subject(space_ids[0],'Space'),subject(space_ids[1],'Space'),refs,
                                  via_entity_id=item['installation_id'],via_opening_id=opening['opening_id'],
                                  existence='PRESENT',mode='Door',segment_native=segment,claims=attempt['after']['claims'])
            relations.append(connection)
            for identity in space_ids:
                relations.append(relation(prefix,'OpeningIncidentTo',subject(opening['opening_id'],'Opening'),
                                          subject(identity,'Space'),refs))
        attempts.append(attempt)
    return attempts, relations


def code_attempts(rows, symbols, openings, scope, prefix):
    """Each printed marker is independent, even when the code is repeated."""
    attempts = []
    relations = []
    for symbol in symbols:
        box = symbol['bbox']
        if not inside(box,scope):
            continue
        x,y,u,v = box
        radius = np.array([(u-x)/2,(v-y)/2])
        reading = marker_reading(rows,symbol)
        if reading is None:
            continue
        text = reading['text']
        symbol_id = f'{prefix}:symbol:{symbol["id"]}'
        relation_row = relation(prefix,'LabelAssociation',subject(text['id'],'TextRegion'),
            subject(symbol_id,'Symbol'),list(dict.fromkeys([*reading['reading_evidence_refs'],symbol_id])),
            claim_scope='Printed code inside a specific marker; repeated codes remain distinct')
        relations.append(relation_row)
        # Nearby opening hypotheses define a review window, never an assignment.
        window = expand(box,scope,3*max(radius))
        candidates = [{'opening_id':o['opening_id'],'decision':o['decision'],'segment_native':o['opening_segment_native']}
                      for o in openings if o['decision']!='CONTRADICTED' and
                      all(window[0]<=p[0]<=window[2] and window[1]<=p[1]<=window[3] for p in o['opening_segment_native'])]
        attempts.append({'id':symbol_id, 'question':'RESOLVE_REFERENT_IDENTITY','window_native':window,'nearby_opening_hypotheses':candidates,
                         'bbox_native':box,**reading,
                         'symbol_path':symbol, 'referent_status':'HOLD',
                         'reason':'CODE_INSIDE_MARKER_WITHOUT_TRACED_INSTANCE_REFERENCE'})
    return attempts, relations


def shared_membership(review,prefix):
    revised = review.get('after') or {}
    if revised.get('status')!='PROPOSED' or not revised.get('shared_region'):
        return [], []
    shared = subject(prefix+':shared-partition','PartitionRegion')
    refs = [review['id'], *(l['observation_id'] for l in revised['labels'])]
    entities = [{**shared,'claim_status':'PROPOSED','evidence_refs':refs,
                 'geometry_ref':'functional_review.after.shared_region'}]
    links = []
    for label in revised['labels']:
        space = subject(prefix+':functional:'+label['observation_id'],'FunctionalSpace')
        entities.append({**space,'name':label['name'],'seed_native':label['seed_native'],
                         'claim_status':'PROPOSED','individual_polygon_native':None})
        links.append(relation(prefix,'LabelAssociation',subject(label['observation_id'],'TextRegion'),
                             space,[label['observation_id'],review['id']],status='PROPOSED'))
        links.append(relation(prefix,'RegionMembership',space,shared,refs,status='PROPOSED'))
    return entities, links


def ground_reference_relations(pdf_bytes, partition, domains, assessment, observations, entities, evidence, symbols, matrix, *, expected_role="Ref"):
    binding = assessment['source_binding']
    if (expected_role not in ('Ref','Target') or binding['source_role']!=expected_role or binding!=partition['source_binding'] or binding!=domains['source_binding']
            or digest(pdf_bytes)!=binding['source_sha256']):
        raise ValueError('Reference relations require originating PDF, geometry revision and scope')
    prefix = f'{binding["frame_id"]}:relations:{binding["analysis_run_id"]}'
    result = {'schema_version':'reference_relations/1' if expected_role=='Ref' else 'target_relations/1','source_binding':binding,'frame_id':binding['frame_id'],
              'source_role':expected_role,'ontology_contract':{'version':ontology.VERSION,'sha256':ontology.contract_digest()},
              'status':'HOLD','relations':[],'door_reviews':[],'code_reviews':[],
              'verified_relation_count':0,'reobservation_budget':{'door_crops':MAX_DOOR_REOBSERVATIONS,'functional_label_crops':2},
              'reason':'PARTITION_INPUT_NOT_AVAILABLE'}
    if partition['status']!='PROPOSED':
        return result
    scope = partition['scope_native']
    with pymupdf.open(stream=pdf_bytes,filetype='pdf') as document:
        page = document[binding['page']-1]
        eligible_text = list(pdf_text(page,matrix,scope,prefix))
        text_source = 'OWN_PDF_TEXT'
        if not eligible_text:
            # Outlined/scanned text has no PDF span. Reuse only the OCR
            # observations from this exact Target/Ref geometry frame.
            text_source = 'OWN_OCR_OBSERVATIONS'
            for item in observations:
                if item['observation_type']!='TextRegion':
                    continue
                if item['frame_id']!=binding['frame_id'] or item['source_role']!=expected_role:
                    raise ValueError('Foreign OCR evidence')
                points=item['geometry_native']['points_native']
                box=rectangle(points)
                if inside(box,scope):
                    raw=item['source_evidence']['text']
                    eligible_text.append({'id':item['observation_id'],'raw':raw,'normalized':normalize_label(raw),
                                          'bbox_native':box,'polygon_native':points,
                                          'source_kind':'OCR','recognition_score':item.get('confidence')})
        functions = (recover_functions(page,eligible_text,partition,domains,assessment,observations,entities,prefix)
                     if not page.rotation else {'status':'SKIPPED','reason':'ROTATED_LOCAL_CLIP_UNSUPPORTED',
                        'before':domains.get('open_plan'),'after':domains.get('open_plan'),'observations':[],'stale_claim_ids':[]})
        number_rows, number_reobservations = recover_numbers(page,binding,eligible_text,symbols,scope,prefix)
        eligible_text = [*eligible_text,*number_rows]
    doors, links = inspect_doors(pdf_bytes,partition,domains,assessment,evidence,prefix)
    codes, code_links = code_attempts(eligible_text,symbols,assessment['openings'],scope,prefix)
    boundaries = boundary_matches(partition,assessment,prefix)
    labels = [relation(prefix,'LabelAssociation',subject(r['observation_id'],'TextRegion'),
                       subject(r['region_id'],'PipeShaft' if r['name']=='PS' else 'Space'),
                       [r['observation_id'],r['region_id']],status='PROPOSED',name=r['name'],seed_native=r['seed_native'],
                       claim_scope='Label seed inside its conditional partition; shaft marks are independent')
              for r in partition['regions'] if r['polygon_native']]
    functional_entities, functional_links = shared_membership(functions,prefix)
    relations = labels+boundaries+links+code_links+functional_links
    return {**result,'status':'PARTIAL','reason':'SEMANTIC_OR_PASSAGE_EVIDENCE_INCOMPLETE',
            'code_reobservations':number_reobservations,
            'text_source':text_source,'functional_review':functions,'functional_entities':functional_entities,
            'relations':relations,'door_reviews':doors,'code_reviews':codes,
            'summary':dict(Counter(r['kind'] for r in relations))}
