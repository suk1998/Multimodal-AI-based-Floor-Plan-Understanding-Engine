"""Append-only user review revisions and revocable local sessions."""
import hashlib,json,os,sqlite3,time
from datetime import datetime,timezone
from pathlib import Path


class RevisionConflict(Exception):
    def __init__(self,latest):self.latest=latest


class ReviewStore:
    def __init__(self,path):
        self.path=Path(path);self.path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        with self.connect() as db:
            db.executescript('''PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS review_revisions(owner TEXT NOT NULL,dataset TEXT NOT NULL,revision INTEGER NOT NULL,save_id TEXT NOT NULL,payload TEXT NOT NULL,sha256 TEXT NOT NULL,created_at TEXT NOT NULL,PRIMARY KEY(owner,dataset,revision),UNIQUE(owner,dataset,save_id));
            CREATE TABLE IF NOT EXISTS sessions(token_hash TEXT PRIMARY KEY,owner TEXT NOT NULL,display TEXT NOT NULL,expires REAL NOT NULL);''')
        os.chmod(self.path,0o600)

    def connect(self):
        db=sqlite3.connect(self.path,timeout=10);db.row_factory=sqlite3.Row;return db

    @staticmethod
    def packet(row,latest=None):
        return {'revision':row['revision'],'latest_revision':latest or row['revision'],'saved_by':row['owner'],'package':json.loads(row['payload']),
            'saved_at':row['created_at'],'content_sha256':row['sha256']}

    def load(self,owner,dataset,revision=None):
        with self.connect() as db:
            if revision is None:row=db.execute('SELECT * FROM review_revisions WHERE owner=? AND dataset=? ORDER BY revision DESC LIMIT 1',(owner,dataset)).fetchone()
            else:row=db.execute('SELECT * FROM review_revisions WHERE owner=? AND dataset=? AND revision=?',(owner,dataset,revision)).fetchone()
        return self.packet(row) if row else None

    def save(self,owner,dataset,base_revision,save_id,package):
        payload=json.dumps(package,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False);digest=hashlib.sha256(payload.encode()).hexdigest()
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            latest=db.execute('SELECT MAX(revision) FROM review_revisions WHERE owner=? AND dataset=?',(owner,dataset)).fetchone()[0] or 0
            previous=db.execute('SELECT * FROM review_revisions WHERE owner=? AND dataset=? AND save_id=?',(owner,dataset,save_id)).fetchone()
            if previous:
                if previous['sha256']!=digest:raise RevisionConflict(latest)
                return self.packet(previous,latest)
            if latest!=base_revision:raise RevisionConflict(latest)
            now=datetime.now(timezone.utc).isoformat()
            db.execute('INSERT INTO review_revisions VALUES(?,?,?,?,?,?,?)',(owner,dataset,latest+1,save_id,payload,digest,now))
            row=db.execute('SELECT * FROM review_revisions WHERE owner=? AND dataset=? AND revision=?',(owner,dataset,latest+1)).fetchone()
            return self.packet(row)

    def history(self,owner,dataset):
        with self.connect() as db:return [dict(r) for r in db.execute('SELECT revision,created_at,sha256 FROM review_revisions WHERE owner=? AND dataset=? ORDER BY revision DESC LIMIT 100',(owner,dataset))]

    def session(self,token):
        if not token:return None
        with self.connect() as db:
            row=db.execute('SELECT * FROM sessions WHERE token_hash=? AND expires>?',(hashlib.sha256(token.encode()).hexdigest(),time.time())).fetchone()
        return {'id':row['owner'],'display':row['display']} if row else None

    def add_session(self,token,identity,lifetime=28800):
        with self.connect() as db:
            db.execute('DELETE FROM sessions WHERE expires<?',(time.time(),))
            db.execute('INSERT INTO sessions VALUES(?,?,?,?)',(hashlib.sha256(token.encode()).hexdigest(),identity['id'],identity['display'],time.time()+lifetime))

    def revoke_session(self,token):
        if token:
            with self.connect() as db:db.execute('DELETE FROM sessions WHERE token_hash=?',(hashlib.sha256(token.encode()).hexdigest(),))
