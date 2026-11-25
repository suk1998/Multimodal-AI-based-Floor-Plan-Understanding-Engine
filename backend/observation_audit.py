"""Scoped observation attempts/layout hints; never architectural ground truth."""
import hashlib
import json
import math
import unicodedata
from collections import Counter

import cv2
import numpy as np


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def normalized_text(text):
    return unicodedata.normalize('NFKC', text).strip()


def overlap(a,b):
    return max(0,min(a[2],b[2])-max(a[0],b[0]))*max(0,min(a[3],b[3])-max(a[1],b[1]))


def box(points):
    p=np.asarray(points,float)
    return [float(p[:,0].min()),float(p[:,1].min()),float(p[:,0].max()),float(p[:,1].max())]


class CoverageAudit:
    """BBox cell incidence, including omitted attempts. Not recall or ink coverage."""
    def __init__(self,binding,columns=16,rows=12):
        self.binding=binding;self.window=binding['context_bbox_native'];self.columns=columns;self.rows=rows
        self.cells={};self.counts={};self.samples={};self.sample_limit=24

    def record(self,kind,bounds,retained,truncated=False):
        counts=self.counts.setdefault(kind,Counter());counts['encountered']+=1
        state='retained' if retained else 'omitted_due_to_limit';counts[state]+=1
        if truncated:counts['boundary_truncated']+=1
        x0,y0,x1,y1=self.window;cw=(x1-x0)/self.columns;ch=(y1-y0)/self.rows
        left=max(0,min(self.columns-1,math.floor((bounds[0]-x0)/cw)))
        right=max(0,min(self.columns-1,math.floor((bounds[2]-x0)/cw)))
        top=max(0,min(self.rows-1,math.floor((bounds[1]-y0)/ch)))
        bottom=max(0,min(self.rows-1,math.floor((bounds[3]-y0)/ch)))
        for row in range(top,bottom+1):
            for col in range(left,right+1):
                cell=self.cells.setdefault((col,row),{})
                counter=cell.setdefault(kind,Counter());counter[state]+=1
                if truncated:counter['boundary_truncated']+=1
        if not retained and len(self.samples.setdefault(kind,[]))<self.sample_limit:
            self.samples[kind].append({'bbox_native':bounds,'reason':'OBSERVATION_STORAGE_CAP','boundary_truncated':'UNKNOWN_NOT_RETAINED'})

    def export(self):
        x0,y0,x1,y1=self.window;cw=(x1-x0)/self.columns;ch=(y1-y0)/self.rows
        return {'schema_version':'observation_coverage/1','frame_id':self.binding['frame_id'],
            'source_role':self.binding['source_role'],'scope_native':self.window,
            'semantics':'BBOX_CELL_INCIDENCE_OF_ATTEMPTED_PRIMITIVES_NOT_PIXEL_COVERAGE_OR_RECALL',
            'undetected_architectural_elements':'UNKNOWN_WITHOUT_GT','empty_cell_is_confirmed_empty':False,
            'counts_by_type':{k:dict(v) for k,v in self.counts.items()},'grid_size':[self.columns,self.rows],
            'cells':[{'cell_id':f'{c}:{r}','bbox_native':[x0+c*cw,y0+r*ch,x0+(c+1)*cw,y0+(r+1)*ch],
                'counts_by_type':{k:dict(v) for k,v in types.items()}} for (c,r),types in sorted(self.cells.items())],
            'omitted_location_samples':self.samples,'sample_limit_per_type':self.sample_limit,
            'truncation_count_scope':'RETAINED_PRIMITIVES_ONLY; omitted truncation unknown',
            'sample_policy':'FIRST_N_ENCOUNTERED_NOT_REPRESENTATIVE; cell counts include all cap omissions'}


def propose_drawing_regions(gray,binding,text_observations):
    """Coarse ink-layout groups + own-page text hints, not room segmentation."""
    from integrations.fpue_ontology.vocabulary import resolve_space_term
    height,width=gray.shape
    scale=min(1.,1024/max(width,height))
    small=cv2.resize(gray,(max(1,round(width*scale)),max(1,round(height*scale))),interpolation=cv2.INTER_AREA)
    _,ink=cv2.threshold(small,0,255,cv2.THRESH_BINARY_INV|cv2.THRESH_OTSU)
    kernel=max(3,round(min(small.shape)*.008))
    joined=cv2.dilate(ink,cv2.getStructuringElement(cv2.MORPH_RECT,(kernel,kernel)))
    count,labels,stats,_=cv2.connectedComponentsWithStats(joined,8)
    regions=[];rejected=Counter()
    sx=width/small.shape[1];sy=height/small.shape[0]
    for component_id,(x,y,w,h,area) in enumerate(stats[1:],1):
        if area<ink.size*.0005 or w*h<ink.size*.002:rejected['SMALL_LAYOUT_GROUP']+=1;continue
        bounds=[max(0,float(x*sx)),max(0,float(y*sy)),min(width,float((x+w)*sx)),min(height,float((y+h)*sy))]
        own=[]
        for t in text_observations:
            p=np.asarray(t['geometry_native']['points_native']);seed=p.mean(axis=0)
            if bounds[0]<=seed[0]<=bounds[2] and bounds[1]<=seed[1]<=bounds[3]:own.append(t)
        words=[str(t['source_evidence']['text']) for t in own];caption=' '.join(words)
        names=[word for word in words if resolve_space_term(word)]
        hints=[]
        if any(s in caption.casefold() for s in ['범례','일람','legend','schedule']):hints.append('LEGEND_OR_SCHEDULE_CANDIDATE')
        if any(s in caption for s in ['상세','단면','DETAIL']):hints.append('DETAIL_CANDIDATE')
        if len(names)>=2:hints.append('FLOORPLAN_CANDIDATE')
        elif '평면' in caption:hints.append('DRAWING_CAPTION_CANDIDATE')
        kind=hints[0] if len(hints)==1 else 'MIXED_OR_UNKNOWN'
        pad=max(1,round(min(w,h)*.05))
        interior=float(np.count_nonzero(labels[y+pad:y+h-pad,x+pad:x+w-pad]==component_id))/max(int(area),1)
        if interior<.15 and w*h>ink.size*.15:kind='FRAME_OR_CONTAINER_CANDIDATE'
        if w>small.shape[1]*.97 and h>small.shape[0]*.97:kind='UNPARTITIONED_PAGE_CONTENT'
        identity=binding['frame_id']+':drawing-region:'+fingerprint({'bounds':bounds,'algorithm':'INK_LAYOUT_GROUPS/1'})[:16]
        regions.append({'region_id':identity,'frame_id':binding['frame_id'],'source_role':binding['source_role'],
            'bbox_native':bounds,'kind':kind,'kind_alternatives':hints,'status':'PROPOSED',
            'source_evidence':{'algorithm':'OpenCV dilated ink layout components/1','component_ink_pixels':int(area),
                'component_interior_fraction':interior,'layout_kernel_thumbnail_px':kernel,'thumbnail_to_native':[[sx,0,0],[0,sy,0],[0,0,1]],
                'related_text_ids':[t['observation_id'] for t in own],'own_page_space_label_count':len(names)},
            'is_physical_space_polygon':False,'is_approved_analysis_scope':False})
    regions.sort(key=lambda r:-(r['bbox_native'][2]-r['bbox_native'][0])*(r['bbox_native'][3]-r['bbox_native'][1]))
    return {'schema_version':'drawing_region_candidates/1','frame_id':binding['frame_id'],'candidates':regions[:24],
        'omitted_candidate_count':max(0,len(regions)-24),'rejected_component_counts':dict(rejected),
        'automatic_semantic_partition_verified':False,'scope_policy':'USER_SELECTS_OR_WHOLE_PAGE_REMAINS_UNPARTITIONED',
        'limitations':['Ink grouping may join different drawings or split one drawing.','Text hints do not certify a plan, legend or detail.']}


def reobservation_audit(bundle):
    """Ledger of real local renders, overlap and shared primitive evidence."""
    binding=bundle['source_binding'];tiles=[];previous={};duplicates=[]
    cases={c.get('request_id'):c for c in bundle['cases'] if c.get('request_id')}
    for request in bundle['reobservation_requests']:
        case=cases[request['request_id']];fresh=case.get('reobservation',{});window=request['native_context']
        tile={'tile_id':request['request_id'],'subject_id':request['subject_id'],'source_role':binding['source_role'],
            'frame_id':binding['frame_id'],'source_sha256':binding['source_sha256'],'page':binding['page'],
            'requested_bbox_native':window,'execution_status':fresh.get('status','NOT_RUN'),
            'question':request['ontology_question'],'profile':request['ontology_profile'],
            'resolution_status':'PARTIAL_GEOMETRY_ONLY' if fresh.get('status')=='COMPLETED' and case['after'].get('status')=='REGION_PROPOSED' else 'UNRESOLVED',
            'missing_evidence':['Independent boundary interpretation','Architectural gap meaning and full-space extent'],
            'previous_support_ids':sorted({identity for edge in case['before'].get('edges',[]) for identity in edge.get('source_line_ids',[])}),
            'request_reasons':request['reasons'],
            'before_candidate_id':case['id']+':r0:region','after_candidate_id':case['id']+':r1:region',
            'no_observation_overwritten':True,'architectural_truth_verified':False,
            'modality_trace':{'text_region_id':case['label']['id'],'seed_native':case['label']['seed'],
                'vector_line_ids':[line['id'] for line in fresh.get('lines',[])],
                'vector_primitive_ids':[line['source_primitive_id'] for line in fresh.get('lines',[])],
                'raster_native_segment_count':len(fresh.get('raster_segments_native',[])),
                'semantics':'COLOCATED_SOURCE_OBSERVATIONS_NOT_VERIFIED_OBJECT_CORRESPONDENCE'}}
        if fresh.get('rerender_sha256'):
            tile.update(image_sha256=fresh['rerender_sha256'],rerender_zoom=fresh['rerender_zoom'],
                raster_size=fresh['rerender_size'],raster_to_native=fresh['rerender_to_native'],
                raster_omitted_due_to_limit=fresh['raster_lsd_omitted_due_to_limit'])
        tile['overlaps']=[{'tile_id':other['tile_id'],'intersection_area_native_px2':overlap(window,other['requested_bbox_native']),
            'independent_evidence':False} for other in tiles if overlap(window,other['requested_bbox_native'])>0]
        for line in fresh.get('lines',[]):
            primitive=line['source_primitive_id']
            if primitive in previous:duplicates.append({'primitive_id':primitive,'tile_ids':[previous[primitive],tile['tile_id']],
                'policy':'SAME_PDF_PRIMITIVE_REOBSERVED_NOT_INDEPENDENT_CONFIRMATION'})
            else:previous[primitive]=tile['tile_id']
        request.update(execution_status=tile['execution_status'],resolution_status=tile['resolution_status'],
            before_candidate_id=tile['before_candidate_id'],after_candidate_id=tile['after_candidate_id'],
            missing_evidence=tile['missing_evidence'])
        tiles.append(tile)
    skipped=[{'subject_id':c['id'],'execution_status':'NOT_RUN','reason':c.get('reobservation_status'),
        'frame_id':binding['frame_id']} for c in bundle['cases'] if not c.get('request_id')]
    return {'schema_version':'reobservation_attempts/1','frame_id':binding['frame_id'],'tiles':tiles,
        'skipped':skipped,'duplicate_source_support':duplicates,'unobserved_page_area':'NOT_ASSESSED_BY_LOCAL_TILES',
        'cache':{'reused':False,'policy':'NO_CROSS_RUN_CACHE; current source reread for each request',
            'identity':fingerprint({'source':{k:binding.get(k) for k in ['source_role','source_sha256','image_sha256','frame_id','page','extraction_zoom','core_bbox_native','context_bbox_native']},
                'algorithm':bundle['algorithm'],'ontology':bundle['ontology_contract']})}}
