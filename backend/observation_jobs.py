"""Bounded jobs using uploaded bytes only; results remain in the owning workspace."""
import json,os,signal,subprocess,tempfile,threading,time,uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from fastapi import HTTPException

ROOT=Path(__file__).resolve().parents[1]

class ObservationJobs:
    def __init__(self,maximum=4,runner=None,geometry_runner=None,spatial_runner=None,reference_runner=None,target_runner=None):
        self.pool=ThreadPoolExecutor(max_workers=1,thread_name_prefix='fpue-observation');self.lock=threading.Lock();self.jobs={};self.maximum=maximum;self.runner=runner or self.process;self.geometry_runner=geometry_runner or self.geometry_process;self.spatial_runner=spatial_runner or self.spatial_process;self.reference_runner=reference_runner or self.reference_process;self.target_runner=target_runner or self.target_process
    def create(self,workspace,document,page_image,binding,*,parent=None,kind=None):
        with self.lock:
            if sum(j['status'] in ['QUEUED','RUNNING'] for j in self.jobs.values())>=self.maximum:raise HTTPException(429,'분석 작업이 진행 중입니다. 잠시 후 다시 실행하세요.')
            owner=getattr(workspace,'observation_owner',None)
            if owner is None:owner=uuid.uuid4().hex;workspace.observation_owner=owner
            completed=[(key,j) for key,j in self.jobs.items() if j['status'] not in ['QUEUED','RUNNING']]
            for key,j in completed[:-48]:del self.jobs[key]
            owned=[(key,j) for key,j in self.jobs.items() if j['owner']==owner]
            for key,j in owned[:-5]:
                if j['status'] not in ['QUEUED','RUNNING']:del self.jobs[key]
            identity=uuid.uuid4().hex;binding={**binding,'analysis_run_id':identity}
            job={'run_id':identity,'owner':owner,'document_id':document.id,'source_sha256':document.info.sha256,'binding':binding,'status':'QUEUED','created_at':time.time(),'result':None,'failure':None,'job_kind':kind or ('geometry' if parent is not None else 'observation')}
            self.jobs[identity]=job
        self.pool.submit(self.execute,job,document.data,page_image.png,binding,parent)
        return self.public(job)
    def execute(self,job,pdf,png,binding,parent=None):
        job['status']='RUNNING'
        try:
            result=self.target_runner(pdf,png,binding) if job['job_kind']=='target' else self.reference_runner(pdf,png,binding) if job['job_kind']=='reference' else self.spatial_runner(pdf,png,binding,parent) if job['job_kind']=='spatial' else self.geometry_runner(pdf,png,binding,parent) if parent is not None else self.runner(pdf,png,binding);result['run_id']=job['run_id'];job['result']=result;job['status']='COMPLETED' if result['status']=='COMPLETED' else 'PARTIAL'
        except Exception as error:job['status']='FAILED';job['failure']={'code':'REFERENCE_WORKER_FAILED' if job['job_kind']=='reference' else 'SPATIAL_WORKER_FAILED' if job['job_kind']=='spatial' else 'GEOMETRY_WORKER_FAILED' if parent is not None else 'OBSERVATION_WORKER_FAILED','reason':'Ref 이해 작업이 실패했습니다. 페이지·이미지 배율과 원본을 확인하세요.' if job['job_kind']=='reference' else '공간 분석이 실패했습니다. 범위와 기하 근거를 확인하고 다시 실행하세요.' if job['job_kind']=='spatial' else '기하 분석이 실패했습니다. ROI를 줄이거나 다시 실행하세요.' if parent is not None else '관찰 생성이 실패했습니다. ROI를 줄이거나 다시 실행하세요.','failure_type':type(error).__name__}
    def get(self,workspace,identity):
        job=self.jobs.get(identity)
        if not job or job['owner']!=getattr(workspace,'observation_owner',None):raise HTTPException(404)
        document=workspace.api.state.documents.documents.get(job['document_id'])
        if document is None or document.info.sha256!=job['source_sha256']:raise HTTPException(410,'분석 원본 PDF가 제거됐습니다. 현재 도면에서 다시 실행하세요.')
        return self.public(job)
    @staticmethod
    def public(job):return {k:job[k] for k in ['run_id','document_id','source_sha256','binding','status','created_at','result','failure','job_kind']}
    @staticmethod
    def process(pdf,png,binding,parent=None,kind=None):
        with tempfile.TemporaryDirectory(prefix='fpue-observation-') as directory:
            folder=Path(directory);(folder/'input.pdf').write_bytes(pdf);(folder/'native.png').write_bytes(png);(folder/'binding.json').write_text(json.dumps(binding))
            command=[str(ROOT/'backend/.venv/bin/python'),'-B',str(ROOT/'backend/observation_worker.py'),'--directory',directory]
            if kind in ('reference','target'):command+=['--'+kind]
            if parent is not None:
                (folder/'parent.json').write_text(json.dumps(parent,ensure_ascii=False));command+=['--spatial' if kind=='spatial' else '--geometry']
            p=subprocess.Popen(command,cwd=ROOT,stdout=subprocess.PIPE,stderr=subprocess.PIPE,start_new_session=True,env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1'})
            try:stdout,stderr=p.communicate(timeout=180)
            except subprocess.TimeoutExpired:
                os.killpg(p.pid,signal.SIGKILL);p.communicate();raise TimeoutError('Observation worker timed out')
            if stderr:
                logs=ROOT/'var/fpue/observation_logs';logs.mkdir(parents=True,exist_ok=True,mode=0o700)
                log=logs/(binding.get('analysis_run_id',uuid.uuid4().hex)+'.log');log.write_bytes(stderr[-32000:]);log.chmod(0o600)
            if p.returncode or not (folder/'bundle.json').is_file():
                import logging
                logging.getLogger('fpue.observations').error('Private observation worker diagnostics: %s',stderr.decode(errors='replace')[-8000:])
                raise RuntimeError('Observation worker failed')
            return json.loads((folder/'bundle.json').read_text())
    @staticmethod
    def geometry_process(pdf,png,binding,parent):return ObservationJobs.process(pdf,png,binding,parent)
    @staticmethod
    def spatial_process(pdf,png,binding,parent):return ObservationJobs.process(pdf,png,binding,parent,kind='spatial')
    @staticmethod
    def reference_process(pdf,png,binding):return ObservationJobs.process(pdf,png,binding,kind='reference')
    @staticmethod
    def target_process(pdf,png,binding):return ObservationJobs.process(pdf,png,binding,kind='target')
    def close(self):self.pool.shutdown(wait=True,cancel_futures=True)
