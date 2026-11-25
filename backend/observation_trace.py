"""Bounded, loss-explicit producer journals. Diagnostic data never decides a change.

Repeated native frame metadata lives in the stream header. Records are streamed
into gzip so discarded PDF glyph edges do not inflate every normal web reply.
No inferred geometry or architectural truth is created by this module.
"""
import base64,gzip,hashlib,io,json
from collections import Counter

VERSION='fpue_producer_trace/1'
MAX_DECODED_BYTES=512*1024*1024

def native_bounds(points):
    if not points:return None
    return [float(min(p[0] for p in points)),float(min(p[1] for p in points)),
            float(max(p[0] for p in points)),float(max(p[1] for p in points))]

def primitive_identity(binding,run,source):
    raw=source.get('source_primitive_id')
    if source.get('kind')=='OCR' and source.get('raw_detection_id'):
        return binding['frame_id']+':primitive:OCR:'+str(source.get('observation_origin_run_id',run))+':'+source['raw_detection_id']
    if not raw:return None
    # PDF primitives may be reobserved in another run. Raster detector indexes
    # cannot be equated across crops/masks/runs merely because numbers match.
    scope='PDF' if source.get('kind')=='PDF_VECTOR' else 'RASTER:'+run
    return binding['frame_id']+':primitive:'+scope+':'+raw

class TraceJournal:
    def __init__(self,binding,run,stage,*,maximum_records=1_000_000,maximum_compressed_bytes=48*1024*1024):
        self.binding={k:binding[k] for k in ['source_role','frame_id','source_sha256','image_sha256','page','extraction_zoom','width','height'] if k in binding}
        self.run=run;self.stage=stage;self.maximum_records=maximum_records;self.maximum_bytes=maximum_compressed_bytes
        self.buffer=io.BytesIO();self.writer=gzip.GzipFile(fileobj=self.buffer,mode='wb',mtime=0)
        self.count=0;self.recorded=0;self.counts=Counter();self.closed=False;self.serialization_errors=0

    def record(self,subject_id,subject_type,status,reason,*,points=None,bounds=None,input_ids=(),output_ids=(),pointer=None,**extra):
        index=self.count;self.count+=1;self.counts[status]+=1
        if self.recorded>=self.maximum_records or self.buffer.tell()>self.maximum_bytes:return None
        identity=f'{self.binding["frame_id"]}:trace:{self.run}:{self.stage}:{index}'
        record=dict(record_id=identity,stage=self.stage,subject_id=subject_id or identity,subject_type=subject_type,
                    status=status,reason_codes=[reason],input_ids=list(dict.fromkeys(i for i in input_ids if i)),
                    output_ids=list(dict.fromkeys(i for i in output_ids if i)))
        if points is not None or bounds is not None:
            record['geometry_native']={'coordinate_system':'image_native_pixels','bbox_native':bounds if bounds is not None else native_bounds(points)}
        if pointer is not None:record['pointer']=pointer
        record.update(extra)
        try:line=json.dumps(record,ensure_ascii=False,separators=(',',':'),allow_nan=False)
        except (TypeError,ValueError):
            # Auxiliary logging must not reject an otherwise usable observation.
            self.serialization_errors+=1
            record=dict(record_id=identity,stage=self.stage,subject_id=subject_id if isinstance(subject_id,str) else identity,
                subject_type='TraceGap',status='TRACE_SERIALIZATION_FAILURE',reason_codes=['TRACE_METADATA_NOT_JSON_SERIALIZABLE'],
                input_ids=[],output_ids=[])
            line=json.dumps(record,ensure_ascii=False,separators=(',',':'))
        self.writer.write((line+'\n').encode())
        self.recorded+=1;return identity

    def observation_attempt(self,kind,points,source,status,observation_id=None,confidence=None):
        alias=primitive_identity(self.binding,self.run,source)
        excerpt={k:source[k] for k in ['kind','algorithm','source_primitive_id','path_id','raw_detection_id','text',
            'recognition_score','recognition_status','failure_reason','layer','context_clipped'] if k in source}
        refs=[source[k] for k in ['line_observation_id'] if source.get(k)]
        refs+=source.get('observation_refs',[])+source.get('parallel_observation_ids',[])
        return self.record(None,kind,status,{'RETAINED':'OBSERVATION_SERIALIZED','STORAGE_CAP':'DISPLAY_STORAGE_CAP_NOT_PROOF_OF_INFERENCE_LOSS',
            'OUTSIDE_CONTEXT':'OUTSIDE_REQUESTED_CONTEXT','EMPTY_GEOMETRY':'NO_OBSERVED_GEOMETRY'}[status],
            points=points,input_ids=[alias,*refs],output_ids=[observation_id] if observation_id else [],
            source_evidence=excerpt,confidence=confidence,claim_status='OBSERVED_UNREVIEWED')

    def finish(self):
        if self.closed:raise ValueError('Trace journal already finalized')
        self.writer.close();self.closed=True;payload=self.buffer.getvalue()
        return dict(schema_version=VERSION,stage=self.stage,run_id=self.run,source_binding=self.binding,
                    encoding='gzip+base64:jsonl',payload_sha256=hashlib.sha256(payload).hexdigest(),
                    payload_base64=base64.b64encode(payload).decode(),record_count=self.recorded,
                    attempted_record_count=self.count,unrecorded_record_count=self.count-self.recorded,
                    status='PARTIAL_TRACE_SERIALIZATION' if self.serialization_errors else 'COMPLETE_WITHIN_DECLARED_STAGE' if self.count==self.recorded else 'PARTIAL_TRACE_BUDGET',
                    serialization_error_count=self.serialization_errors,
                    status_counts=dict(self.counts),geometry_scope='NATIVE_BBOX_AND_EXPLICIT_SOURCE_REFERENCES',
                    detector_misses_and_unattempted_primitives='UNKNOWN_NOT_INVENTED')

def stream_metadata(stream):return {k:v for k,v in stream.items() if k!='payload_base64'}

def iter_records(stream):
    if stream.get('schema_version')!=VERSION or stream.get('encoding')!='gzip+base64:jsonl':raise ValueError('Unsupported trace format')
    raw=base64.b64decode(stream['payload_base64'],validate=True)
    if hashlib.sha256(raw).hexdigest()!=stream['payload_sha256']:raise ValueError('Trace payload digest mismatch')
    total=count=0
    with gzip.GzipFile(fileobj=io.BytesIO(raw)) as reader:
        while True:
            line=reader.readline(2*1024*1024)
            if not line:break
            total+=len(line)
            if total>MAX_DECODED_BYTES or not line.endswith(b'\n'):raise ValueError('Trace decompression limit exceeded')
            record=json.loads(line);count+=1
            yield {**record,'source_role':stream['source_binding']['source_role'],'frame_id':stream['source_binding']['frame_id'],
                   'source_binding':stream['source_binding']}
    if count!=stream['record_count']:raise ValueError('Trace record count mismatch')
