"""Local password sessions; optional verified Cloudflare Access JWT adapter.

No account connection or JWKS request occurs in local mode.
"""
from dataclasses import dataclass
import hashlib,hmac,json,re,secrets,time
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import HTTPException

COOKIE='fpue_session'


def password_record(password):
    salt=secrets.token_hex(24)
    return {'salt':salt,'password_hash':hashlib.pbkdf2_hmac('sha256',password.encode(),bytes.fromhex(salt),310000).hex()}


@dataclass
class AuthConfig:
    mode:str='local'
    user_file:Path|None=None
    public_origin:str='http://127.0.0.1:8020'
    access_issuer:str=''
    access_audience:str=''
    development_origin:str|None=None


class Authentication:
    def __init__(self,config,store,jwks_client=None):
        self.config=config;self.store=store;self.failures={}
        origin=urlsplit(config.public_origin)
        if origin.path not in ['', '/'] or origin.query or origin.fragment or not origin.hostname:raise ValueError('Invalid public origin')
        if config.mode=='local':
            if origin.scheme!='http' or origin.hostname not in ['127.0.0.1','localhost']:raise ValueError('Local mode is restricted to loopback HTTP; external access requires Access mode')
            self.users=json.loads(config.user_file.read_text())['users'] if config.user_file and config.user_file.exists() else []
            if not self.users:raise ValueError('Create a local user with scripts/prepare_local_user.py first')
            if config.development_origin:
                dev=urlsplit(config.development_origin)
                if dev.scheme!='http' or dev.hostname not in ['127.0.0.1','localhost'] or dev.path or dev.query or dev.fragment:raise ValueError('Development origin must be loopback HTTP')
        elif config.mode=='cloudflare_access':
            if origin.scheme!='https':raise ValueError('Access mode requires HTTPS public origin')
            if not re.fullmatch(r'https://[a-zA-Z0-9-]+\.cloudflareaccess\.com',config.access_issuer) or not config.access_audience:raise ValueError('Access issuer/audience must be explicitly configured')
            from jwt import PyJWKClient
            self.jwks=jwks_client or PyJWKClient(config.access_issuer+'/cdn-cgi/access/certs',cache_jwk_set=True,lifespan=300,timeout=5)
        else:raise ValueError('Unknown authentication mode')

    def identify(self,request):
        if self.config.mode=='local':return self.store.session(request.cookies.get(COOKIE))
        token=request.headers.get('Cf-Access-Jwt-Assertion')
        if not token:return None
        try:
            import jwt
            key=self.jwks.get_signing_key_from_jwt(token).key
            claims=jwt.decode(token,key,algorithms=['RS256'],issuer=self.config.access_issuer,audience=self.config.access_audience,
                options={'require':['exp','iat','iss','aud','sub']})
            if not isinstance(claims['sub'],str) or not claims['sub']:return None
            return {'id':'access:'+hashlib.sha256((self.config.access_issuer+'|'+claims['sub']).encode()).hexdigest(),
                'display':str(claims.get('email') or '인증된 검토자')[:150]}
        except Exception:return None

    def login(self,username,password,client):
        if self.config.mode!='local':raise HTTPException(404,'로컬 로그인은 사용하지 않습니다.')
        now=time.time();attempts=[t for t in self.failures.get(client,[]) if t>now-60]
        if len(attempts)>=6:raise HTTPException(429,'잠시 후 다시 로그인하세요.')
        user=next((u for u in self.users if hmac.compare_digest(u['username'].encode(),username.encode())),None)
        salt=bytes.fromhex(user['salt']) if user else bytes(24)
        digest=hashlib.pbkdf2_hmac('sha256',password.encode(),salt,310000).hex()
        if not user or not hmac.compare_digest(digest,user['password_hash']):
            self.failures[client]=attempts+[now]
            if len(self.failures)>1024:self.failures={client:self.failures[client]}
            raise HTTPException(401,'아이디 또는 비밀번호를 확인하세요.')
        self.failures.pop(client,None);token=secrets.token_urlsafe(48);identity={'id':user['subject'],'display':user.get('display',username)}
        self.store.add_session(token,identity);return token,identity

    def check_origin(self,request):
        origin=request.headers.get('origin')
        allowed={self.config.public_origin.rstrip('/')}
        if self.config.mode=='local':
            expected=urlsplit(self.config.public_origin);port=expected.port
            allowed|={f'http://{host}'+(f':{port}' if port else '') for host in ['localhost','127.0.0.1']}
            if self.config.development_origin:allowed.add(self.config.development_origin)
        if origin not in allowed:raise HTTPException(403,'이 웹사이트에서 시작한 저장 요청만 허용합니다. 페이지를 다시 여세요.')
