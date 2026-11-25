"""Stage 10 read-only views of a single source's architectural evidence."""
from collections import Counter, defaultdict

import numpy as np


def catalog(bundle,*,audit_sink=None):
    role = bundle['source_role']
    closure = bundle['space_closure']
    assessment = bundle['architectural_assessment']
    relations = bundle.get('reference_relations' if role == 'Ref' else 'target_relations', {})
    objects, skipped = [], Counter()
    def trace(kind,oid,state,reason,paths=(),refs=(),**details):
        if audit_sink is not None:
            points=[p for path in paths for p in path]
            bounds=([*np.min(points,axis=0).tolist(),*np.max(points,axis=0).tolist()] if points else None)
            audit_sink(dict(subject_id=oid,subject_type=kind,status=state,reason=reason,
                            bounds=bounds,input_ids=list(refs),**details))

    def add(kind, oid, paths, refs, *, supported, **fields):
        paths = [p for p in paths if len(p)]
        if not paths:
            trace(kind,oid,'EXCLUDED_FROM_CATALOG','NO_GEOMETRY_PATH',refs=refs)
            return
        points = np.concatenate(paths).astype(float)
        box = [*points.min(axis=0).tolist(), *points.max(axis=0).tolist()]
        obj = dict(kind=kind, id=oid, paths=paths, bbox=box, supported=supported,
                   refs=list(dict.fromkeys([oid,*refs])), hosts=[], spaces=[], parent=None,
                   name=None, closed=False, physical_ends=False)
        obj.update(fields)
        objects.append(obj)
        trace(kind,oid,'INCLUDED_IN_CATALOG','EXISTING_CATALOG_OBJECT_AVAILABLE',paths,refs,
              geometry_supported=supported,claim_status='PROPOSED')

    # Face owners in partition boundary runs are one-based. Inferred closures
    # have no positive face owner and cannot supply a wall-space attachment.
    attachments = defaultdict(set)
    faces = closure.get('faces', [])
    for space in closure['regions']:
        if 'region_id' not in space:
            trace('Space',space.get('observation_id'),'EXCLUDED_FROM_CATALOG','NO_REGION_ID')
            continue
        rings = space.get('polygon_native') or {}
        runs = space.get('boundary_runs', [])
        for run in runs:
            owner = run.get('owner',0)
            if isinstance(owner,int) and 1 <= owner <= len(faces):
                for fragment in faces[owner-1].get('source_refs', []):
                    attachments[fragment].add(space['region_id'])
        paths = rings.get('outer_rings',[]) + rings.get('holes',[])
        closed = bool(paths) and bool(rings.get('outer_rings'))
        inferred_runs = sum(run.get('owner', 0) < 0 for run in runs)
        supported_outline = closed and (not inferred_runs or space.get('claim_status') == 'VERIFIED')
        add('Space',space['region_id'],paths or [r['points_native'] for r in runs] or [[space['seed_native']]],
            [space['observation_id']],supported=supported_outline,closed=closed,inferred_run_count=inferred_runs,
            holes=len(rings.get('holes',[])),name=space['name'],seed=space['seed_native'],boundary_count=len(runs))

    for parent in assessment['structural_parents']:
        for f in parent['fragments']:
            if f['decision']!='ADMISSIBLE_GEOMETRIC_HYPOTHESIS' or f['boundary_truncated']:
                skipped['wall_fragment_held_or_truncated']+=1
                trace('Wall',f['fragment_id'],'EXCLUDED_FROM_CATALOG',f['decision'],
                      f.get('boundary_faces_native',[]),f.get('source_observation_refs',[]),
                      boundary_truncated=f['boundary_truncated'],additional_reason_codes=['BOUNDARY_TRUNCATED'] if f['boundary_truncated'] else [])
                continue
            sides = f['boundary_faces_native']
            line = np.asarray(sides).mean(axis=0).tolist()
            add('Wall',f['fragment_id'],[line,*sides],f.get('source_observation_refs',[]),
                supported=True,parent=parent['parent_structure_id'],width=f['thickness_px'],
                physical_ends=f.get('physical_endpoints_status')=='VERIFIED',
                spaces=sorted(attachments[f['fragment_id']]))
    installed_openings = {o['opening_id'] for o in assessment.get('installed_elements',[])}
    openings = {o['opening_id']:o for o in assessment.get('openings',[])}
    doors = {r['door_id']:r for r in relations.get('door_reviews',[])}
    for oid,o in openings.items():
        ok = o['decision']=='ADMISSIBLE_OPENING_GEOMETRY'
        if not ok and oid not in installed_openings:
            skipped['opening_'+o['decision']]+=1
            trace('Opening',oid,'EXCLUDED_FROM_CATALOG',o['decision'],[o['opening_segment_native']],
                  o['evidence_refs'],additional_reason_codes=o.get('reasons',[]))
            continue
        add('Opening',oid,[o['opening_segment_native']],o['evidence_refs'],supported=ok,
            hosts=o['host_parent_ids'],bracketed=ok,physical_ends=False)
    for item in assessment.get('installed_elements',[]):
        o = openings.get(item['opening_id'])
        if not o:
            skipped['installation_without_opening']+=1
            trace('Installation',item.get('installation_id'),'EXCLUDED_FROM_CATALOG','INSTALLATION_WITHOUT_OPENING',refs=[item['opening_id']])
            continue
        kinds=item['type_candidates']
        kind=kinds[0] if len(kinds)==1 else 'BuildingElement' if 'Grille' in kinds else 'WindowOrSlidingDoor'
        d=doors.get(item['installation_id'],{})
        connected=[s['space_id'] for s in d.get('sides',[]) if s.get('space_id')]
        grounded=d.get('after',{}).get('status')=='SUPPORTED' and len(set(connected))==2
        add(kind,item['installation_id'],[o['opening_segment_native']],item['evidence_refs']+[o['opening_id']],
            supported=len(kinds)==1 and o['decision']=='ADMISSIBLE_OPENING_GEOMETRY' and item['decision'] not in ('HOLD','CONTRADICTED'),
            hosts=[o['opening_id']], spaces=connected if grounded else [], bracketed=o['decision']=='ADMISSIBLE_OPENING_GEOMETRY')
    for marker in relations.get('code_reviews',[]):
        if not marker.get('text') or not marker.get('bbox_native'):
            trace('CodeMarker',marker.get('id'),'EXCLUDED_FROM_CATALOG','MISSING_TEXT_OR_NATIVE_BBOX')
            continue
        x,y,x2,y2=marker['bbox_native']
        refs=[t['id'] for t in (marker['text'],marker.get('number')) if t]
        add('CodeMarker',marker['id'],[[[x,y],[x2,y],[x2,y2],[x,y2]]],refs,supported=True,
            name=marker.get('code'),
            complete_text=(marker['reading_status']=='SUPPORTED' and marker.get('canonical_code') is not None)
                if 'reading_status' in marker else bool(marker.get('number')),
            canonical_code=marker.get('canonical_code'),ontology_type_code=marker.get('ontology_type_code'),
            native_observations={'symbol_code_reading':marker},closed=True)

    # Relative layout of independently observed, unique local names is a weak
    # search cue. Keep it distinct from an actual bounds/hosts/connects relation.
    counts=Counter(r['name'] for r in closure['regions'])
    labels=[r for r in closure['regions'] if counts[r['name']]==1]
    for obj in objects:
        center=np.asarray(obj['bbox']).reshape(2,2).mean(axis=0)
        neighbors=sorted((float(np.linalg.norm(center-r['seed_native'])),r['name'],r['observation_id'])
                         for r in labels if r['name']!=obj['name'])[:3]
        obj['neighbors']=[n for _,n,_ in neighbors]
        obj['context_refs']=[oid for _,_,oid in neighbors]
    return objects, dict(skipped)
