"""Small product responses retain ownership and the complete internal evidence."""
import unittest
from fastapi.testclient import TestClient
from backend.public_app import COOKIE, ROOT, create_app


class ReferenceSummaryTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app()
        self.client = TestClient(self.app)
        self.client.get('/api/workspace')
        document = self.client.post('/api/documents',
            files={'file':('reference.pdf',(ROOT/'source_pdfs/자이사업승인도서.pdf').read_bytes(),'application/pdf')},
            headers={'Origin':'http://testserver'}).json()
        workspace = self.app.state.workspaces.get(self.client.cookies.get(COOKIE))
        workspace.observation_owner = 'summary-test-owner'
        self.identity = 'a'*32
        self.job = dict(run_id=self.identity, owner=workspace.observation_owner,
            document_id=document['id'],source_sha256=document['sha256'], binding={'frame_id':'Ref:test-native-frame'},
            status='PARTIAL',created_at=0,failure=None,job_kind='reference',
            result={'cases':[{'label':{'name':'침실1'}},{'label':{'name':'팬트리'}},
                             {'label':{'name':'침실1'}},{'label':{'name':''}}],
                    'large_internal_evidence':'TEST ONLY '*100_000})
        self.app.state.observation_jobs.jobs[self.identity] = self.job
        self.url = '/api/reference-understandings/'+self.identity

    def tearDown(self):
        self.client.close()
        self.app.state.observation_jobs.close()

    def test_small_summary_does_not_promote_ocr_to_verified_rooms_or_mutate_full_evidence(self):
        response=self.client.get(self.url+'?summary=1')
        self.assertEqual(response.status_code,200)
        self.assertLess(len(response.content),2048)
        view=response.json()
        self.assertEqual(view['observed_space_names'],['침실1','팬트리'])
        self.assertEqual(view['status'],'PARTIAL')
        self.assertNotIn('result',view)
        full=self.client.get(self.url).json()
        self.assertEqual(full['result'],self.job['result'])

    def test_running_or_failed_jobs_have_no_fabricated_space_names(self):
        self.job.update(status='RUNNING',result=None)
        self.assertEqual(self.client.get(self.url+'?summary=1').json()['observed_space_names'],[])
        self.job.update(status='FAILED',failure={'reason':'TEST ONLY failure'})
        view=self.client.get(self.url+'?summary=1').json()
        self.assertEqual(view['observed_space_names'],[])
        self.assertEqual(view['failure']['reason'],'TEST ONLY failure')

    def test_summary_still_requires_the_owning_workspace_and_reference_job_kind(self):
        with TestClient(self.app) as stranger:
            stranger.get('/api/workspace')
            self.assertEqual(stranger.get(self.url+'?summary=1').status_code,404)
        self.job['job_kind']='target'
        self.assertEqual(self.client.get(self.url+'?summary=1').status_code,404)


if __name__=='__main__':unittest.main()
