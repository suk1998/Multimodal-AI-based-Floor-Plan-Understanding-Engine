"""One-command access to the existing FPUE scripts; no account or DNS changes."""
import argparse,fcntl,json,os,re,subprocess,time,urllib.request
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT=Path(__file__).resolve().parents[1]
STATE=ROOT/'var/fpue/connection_restart.json'
LOCAL='http://127.0.0.1:8020'

def health():
    try:
        with urllib.request.urlopen(LOCAL+'/api/health',timeout=3) as response:data=json.load(response)
        if data.get('mode')!='public_pdf_workspace' or not data.get('capabilities',{}).get('pdf_upload') or data.get('server_GT_storage') is not False:
            raise RuntimeError('8020에 다른 서버가 응답합니다. 기존 서비스를 변경하지 않습니다.')
        return True
    except OSError:return False

def matching(kind):
    result=[]
    for path in Path('/proc').glob('[0-9]*/cmdline'):
        try:args=path.read_bytes().decode(errors='replace').split('\0')
        except OSError:continue
        if kind=='web':match='backend.public_app:create_app' in args and '--port' in args and args[args.index('--port')+1]=='8020'
        else:match=any(Path(a).name=='cloudflared' for a in args[:2]) and 'tunnel' in args and '--url' in args and args[args.index('--url')+1]==LOCAL
        if match:result.append(int(path.parent.name))
    return result

def launch(script,name):
    stamp=datetime.now(ZoneInfo('Asia/Seoul')).strftime('%Y%m%d_%H%M%S_%f');log=ROOT/'var/fpue'/f'{name}_{stamp}.log'
    with log.open('ab') as stream:
        process=subprocess.Popen([str(ROOT/'scripts'/script)],cwd=ROOT,stdin=subprocess.DEVNULL,stdout=stream,stderr=stream,start_new_session=True)
    return process,log

def save(state):
    temporary=STATE.with_suffix('.tmp');temporary.write_text(json.dumps(state,ensure_ascii=False,indent=2));temporary.replace(STATE)

def main():
    parser=argparse.ArgumentParser(description='FPUE 시작과 접속 주소 확인');parser.add_argument('command',choices=['start','status'],nargs='?',default='start');args=parser.parse_args()
    os.umask(0o077);STATE.parent.mkdir(parents=True,exist_ok=True)
    with (STATE.parent/'control.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX);state=json.loads(STATE.read_text()) if STATE.exists() else {};web_ok=health();web_pids=matching('web');tunnel_pids=matching('tunnel')
        if args.command=='start' and not web_ok:
            if web_pids:raise RuntimeError('FPUE가 시작 중이거나 응답하지 않습니다. 중복 실행하지 않습니다. 로그를 확인하세요.')
            print('FPUE 웹서버 시작 중…',flush=True);process,log=launch('run_web.sh','web_managed');state.update(web_pid=process.pid,web_log=str(log.relative_to(ROOT)),mode='public_pdf_workspace',detached_session=True);save(state)
            for _ in range(150):
                if process.poll() is not None:raise RuntimeError(f'서버가 종료됐습니다. 로그: {log.relative_to(ROOT)}')
                if health():web_ok=True;break
                time.sleep(.2)
            if not web_ok:raise RuntimeError(f'서버 준비 시간이 초과됐습니다. 로그: {log.relative_to(ROOT)}')
            web_pids=matching('web');state['web_started_at_KST']=datetime.now(ZoneInfo('Asia/Seoul')).isoformat();save(state)
        if args.command=='start' and not tunnel_pids:
            print('외부 HTTPS 연결 시작 중…',flush=True);process,log=launch('run_quick_tunnel.sh','quick_tunnel_managed');state.update(tunnel_pid=process.pid,tunnel_log=str(log.relative_to(ROOT)));state.pop('public_url',None);save(state)
            for _ in range(225):
                if process.poll() is not None:raise RuntimeError(f'Tunnel이 종료됐습니다. 로그: {log.relative_to(ROOT)}')
                urls=re.findall(r'https://[a-z0-9-]+\.trycloudflare\.com',log.read_text(errors='replace'))
                if urls:state['public_url']=urls[-1];break
                time.sleep(.2)
            if not state.get('public_url'):raise RuntimeError(f'HTTPS 주소 발급 대기 중입니다. 중복 실행하지 말고 로그를 확인하세요: {log.relative_to(ROOT)}')
            state['tunnel_started_at_KST']=datetime.now(ZoneInfo('Asia/Seoul')).isoformat();save(state);tunnel_pids=matching('tunnel')
        if web_pids:state['web_pid']=web_pids[0]
        # A URL belongs to the recorded connector, never to another process.
        active_url=state.get('public_url') if state.get('tunnel_pid') in tunnel_pids else None
        if active_url and state.get('tunnel_log'):
            path=ROOT/state['tunnel_log']
            if path.is_file():
                urls=re.findall(r'https://[a-z0-9-]+\.trycloudflare\.com',path.read_text(errors='replace'))
                if urls:active_url=state['public_url']=urls[-1]
        save(state)
        print('웹서버: '+('실행 중' if web_ok else '중지/응답 없음'))
        print('로컬 주소: '+LOCAL)
        print('외부 연결: '+('실행 중' if tunnel_pids else '중지'))
        print('외부 주소: '+(active_url or '확인되지 않음'))
        print('고정 외부 주소: 도메인과 Cloudflare 계정 연결 필요 (현재는 임시 주소)')

if __name__=='__main__':
    try:main()
    except (RuntimeError,OSError,ValueError) as error:raise SystemExit(str(error))
