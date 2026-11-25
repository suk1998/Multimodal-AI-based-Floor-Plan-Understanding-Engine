"""Diagnostic ledger controls; no new geometric truth or GT-derived rules."""
from copy import deepcopy
import base64,hashlib,json,unittest
from io import BytesIO
import cv2,numpy as np,pymupdf
from fastapi.testclient import TestClient
from backend.observation_trace import TraceJournal,iter_records
from backend.upstream_lineage import attach_upstream_lineage,public_comparison,select_records,trace_records
from backend.change_candidate_audit import attach_candidate_audit
from backend.comparison_objects import catalog
from backend.structural_comparison import compare_registered
from backend.test_structural_comparison import Scene
from backend.test_architectural_geometry import prepare
from backend.architectural_geometry import analyze_geometry
from poc.space_grounding_geometry import merge_lines,Settings

def comparison():
    scene=Scene();result=compare_registered(scene.models,scene.alignment);attach_candidate_audit(result)
    return scene,result

class TraceJournalTests(unittest.TestCase):
    def binding(self):return dict(source_role='Ref',frame_id='Ref:TEST',source_sha256='TEST-PDF',image_sha256='TEST-PNG',page=1,extraction_zoom=2,width=400,height=400)

    def test_budget_is_explicit_not_silent_history_loss(self):
        journal=TraceJournal(self.binding(),'test','TEST_ONLY',maximum_records=2)
        for i in range(5):journal.record(str(i),'PdfLine','REJECTED','TEST_REASON',points=[[1,2],[3,4]])
        stream=journal.finish();self.assertEqual((stream['record_count'],stream['unrecorded_record_count']),(2,3))
        self.assertEqual(stream['status'],'PARTIAL_TRACE_BUDGET');self.assertEqual(len(list(iter_records(stream))),2)

    def test_hash_and_record_count_are_checked(self):
        journal=TraceJournal(self.binding(),'test','TEST_ONLY');journal.record('o','TextRegion','RETAINED','TEST')
        stream=journal.finish();bad=deepcopy(stream);bad['payload_base64']=base64.b64encode(b'not-the-payload').decode()
        with self.assertRaises(ValueError):list(iter_records(bad))
        bad=deepcopy(stream);bad['record_count']+=1
        with self.assertRaises(ValueError):list(iter_records(bad))

    def test_nonserializable_diagnostic_metadata_becomes_a_gap_not_a_detection_gate(self):
        journal=TraceJournal(self.binding(),'test','TEST_ONLY')
        journal.record('o','PdfLine','RETAINED','TEST',unserializable_diagnostic=object())
        stream=journal.finish();record,=list(iter_records(stream))
        self.assertEqual(stream['serialization_error_count'],1)
        self.assertEqual(stream['status'],'PARTIAL_TRACE_SERIALIZATION')
        self.assertEqual(record['status'],'TRACE_SERIALIZATION_FAILURE')

    def test_actual_filter_decisions_extend_beyond_old_example_cap_without_changing_math(self):
        lines=[dict(id='TEST:'+str(i),points=[[10.,20.],[10.2,20.]],stroke_px=1.,layer='',path_type='s') for i in range(512)]
        raster={'ink':np.zeros((100,100),np.uint8)};events=[]
        expected=merge_lines(lines,raster,10,Settings())
        actual=merge_lines(lines,raster,10,Settings(),audit_sink=lambda s,status,reason:events.append((s['id'],status,reason)))
        self.assertEqual(expected,actual);self.assertEqual(len(events),512)
        self.assertTrue(all(e[2]=='SHORT_FRAGMENT_OR_GLYPH' for e in events))
        self.assertEqual(actual[1]['examples'],[])

class ProducerTraceTests(unittest.TestCase):
    def test_display_omission_keeps_id_and_original_native_position(self):
        with pymupdf.open() as doc:
            p=doc.new_page(width=200,height=160);shape=p.new_shape()
            for i in range(4003):shape.draw_line((20,30),(150,30))
            shape.finish(width=.8);shape.commit();pdf=doc.tobytes()
        data,image,binding,parent=prepare(data=pdf)
        attempts=list(iter_records(parent['producer_traces'][0]));omitted=[r for r in attempts if r['subject_type']=='PdfLine' and r['status']=='STORAGE_CAP']
        self.assertEqual(len(omitted),parent['coverage']['omitted_due_to_limits']['PdfLine']);self.assertGreater(len(omitted),0)
        self.assertTrue(all(r['geometry_native']['bbox_native']==[40,60,300,60] for r in omitted))
        self.assertTrue(all(r['input_ids'] and not r['output_ids'] for r in omitted))
        geometry=analyze_geometry(data,image.png,binding,parent)
        records=list(iter_records(geometry['producer_traces'][1]))
        self.assertGreaterEqual(geometry['coverage']['source_counts']['pdf_segments_context'],4003)
        alias=omitted[0]['input_ids'][0]
        self.assertTrue(any(alias in r['input_ids'] for r in records))
        self.assertFalse(geometry['coverage']['parent_display_caps_used_for_geometry'])

    def test_pruned_raw_observation_remains_queryable_with_filter_reason(self):
        data,image,binding,parent=prepare()
        with pymupdf.open(stream=data,filetype='pdf') as doc:
            doc[0].draw_line((10,12),(10.2,12),width=.2);pdf=doc.tobytes()
        data,image,binding,parent=prepare(data=pdf);geometry=analyze_geometry(data,image.png,binding,parent)
        records=list(iter_records(geometry['producer_traces'][1]));removed=[r for r in records if r['subject_type']=='PdfLine' and r['status']=='NOT_SERIALIZED']
        self.assertTrue(removed);known={r['observation_id'] for r in geometry['observations']}
        self.assertTrue(all(r['subject_id'] not in known for r in removed))
        self.assertTrue(any(any(f['subject_id']==r['subject_id'] and f['status']=='REJECTED' for f in records) for r in removed))

    def test_compound_curve_has_observed_id_and_actual_unsupported_decision(self):
        data,image,binding,parent=prepare()
        with pymupdf.open(stream=data,filetype='pdf') as doc:doc[0].draw_circle((100,80),15);pdf=doc.tobytes()
        data,image,binding,parent=prepare(data=pdf);geometry=analyze_geometry(data,image.png,binding,parent)
        before=list(iter_records(parent['producer_traces'][0]));after=list(iter_records(geometry['producer_traces'][1]))
        unsupported=[r for r in after if r['status']=='NOT_INTERPRETED']
        self.assertTrue(unsupported)
        self.assertTrue(any(r['subject_id'] in o['input_ids'] for r in unsupported for o in before if o['subject_type']=='PdfCurve'))

class UpstreamGraphTests(unittest.TestCase):
    def test_catalog_exclusion_is_individual_and_preserves_original_reason(self):
        scene=Scene();scene.wall('Ref','wall','parent',40,100,80)
        f=scene.models[0]['architectural_assessment']['structural_parents'][0]['fragments'][0];f['decision']='HOLD';events=[]
        baseline=catalog(scene.models[0]);observed=catalog(scene.models[0],audit_sink=events.append)
        self.assertEqual(baseline,observed)
        event=next(r for r in events if r['subject_id']==f['fragment_id'])
        self.assertEqual((event['status'],event['reason']),('EXCLUDED_FROM_CATALOG','HOLD'))

    def test_legacy_history_is_partial_not_invented_and_detection_is_unchanged(self):
        scene,result=comparison();before=deepcopy(result['correspondences']);attach_upstream_lineage(result,scene.models)
        self.assertEqual(before,result['correspondences']);self.assertEqual(result['upstream_lineage']['status'],'PARTIAL_WITH_DECLARED_GAPS')
        self.assertTrue(result['upstream_lineage']['source_summaries'][0]['trace_gaps'])
        self.assertNotIn('streams',public_comparison(result)['upstream_lineage'])
        self.assertEqual(sum(s['record_count'] for s in result['upstream_lineage']['streams']),len(list(trace_records(result))))

    def test_corrupt_or_foreign_producer_trace_never_becomes_a_detection_gate(self):
        for defect in ['foreign','corrupt','malformed']:
            scene,result=comparison();before=deepcopy(result['correspondences'])
            journal=TraceJournal({**scene.models[0]['source_binding'],'source_role':'Ref','frame_id':scene.models[0]['frame_id']},'test','OBSERVATION_ATTEMPT')
            journal.record('TEST_OBS','PdfLine','RETAINED','TEST');stream=journal.finish()
            if defect=='foreign':stream['source_binding']['frame_id']='Target:other'
            elif defect=='corrupt':stream['payload_sha256']='invalid'
            else:stream=None
            scene.models[0]['producer_traces']=[stream];attach_upstream_lineage(result,scene.models)
            self.assertEqual(result['correspondences'],before);self.assertTrue(result['upstream_lineage']['integrity_gaps'])

    def test_ref_target_subject_queries_and_native_frames_remain_separate(self):
        scene,result=comparison();attach_upstream_lineage(result,scene.models)
        page=select_records(result,role='Ref',stage='SPACE_HYPOTHESIS',limit=1)
        self.assertEqual(len(page['records']),1);self.assertTrue(page['has_more'])
        subject=page['records'][0]['subject_id'];neighbors=select_records(result,subject_id=subject,role='Ref',limit=100)
        self.assertTrue(neighbors['records']);self.assertTrue(all(r['source_role']=='Ref' and r['frame_id']=='Ref:fixture' for r in neighbors['records']))

    def test_narrative_text_does_not_create_an_evidence_edge(self):
        from backend.upstream_lineage import explicit_references
        self.assertEqual(explicit_references({'text':'some-other-id','description':'some-other-id','evidence_refs':['actual-evidence']}),['actual-evidence'])

    def test_explicit_comparison_id_alias_can_be_followed_between_stages(self):
        scene,result=comparison();attach_upstream_lineage(result,scene.models)
        identity=result['correspondences'][0]['correspondence_id']
        rows=select_records(result,subject_id=identity)['records']
        self.assertTrue(rows);self.assertTrue(any(r['stage']=='FINAL_COMPARISON' for r in rows))

class TraceAPITests(unittest.TestCase):
    def test_workspace_owned_stream_and_paging_without_main_payload(self):
        from backend.public_app import create_app,COOKIE
        from backend.comparison_review import ComparisonReview
        from backend.test_comparison_review import saved_fixture
        from backend.test_spatial_alignment import fixture
        from phase1_pdf import PDFInfo
        pdf=BytesIO()
        with pymupdf.open() as doc:doc.new_page(width=80,height=80);data=doc.tobytes()
        app=create_app()
        with TestClient(app) as owner,TestClient(app) as other:
            owner.get('/api/workspace');other.get('/api/workspace');w=app.state.workspaces.get(owner.cookies[COOKIE])
            documents=[]
            for name in ['ref.pdf','target.pdf']:
                document=w.api.state.documents.add(name,data,PDFInfo(hashlib.sha256(data).hexdigest(),1));documents.append(document.metadata())
            _,result=saved_fixture();j=TraceJournal({'source_role':'Ref','frame_id':'Ref:test'},'test','TEST')
            j.record('one','PdfLine','REJECTED','SYNTHETIC_REASON',points=[[1,2],[3,4]])
            result['upstream_lineage']={'schema_version':'TEST_ONLY','streams':[j.finish()]}
            review=ComparisonReview(result,documents);w.comparisons[result['run_id']]=review
            path='/api/comparison-reviews/'+result['run_id']
            self.assertNotIn('streams',owner.get(path).json()['comparison']['upstream_lineage'])
            query=owner.get(path+'/upstream-trace?subject_id=one');self.assertEqual(query.status_code,200)
            self.assertEqual(query.json()['records'][0]['geometry_native']['bbox_native'],[1,2,3,4])
            exported=owner.get(path+'/upstream-trace?format=jsonl');self.assertEqual(exported.status_code,200)
            self.assertEqual(json.loads(exported.text)['subject_id'],'one')
            self.assertEqual(other.get(path+'/upstream-trace').status_code,404)
            self.assertEqual(owner.get(path+'/upstream-trace?limit=10000').status_code,422)
            self.assertEqual(owner.get(path+'/upstream-trace?role=invalid').status_code,422)

if __name__=='__main__':unittest.main()
