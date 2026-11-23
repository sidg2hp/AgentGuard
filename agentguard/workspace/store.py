"""SQLite-backed single-workspace queue. Each claim has a distinct fencing token."""
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import sqlite3
import time
from uuid import uuid4


class Conflict(Exception):
    pass


class QueueFull(Exception):
    pass


def canonical(data):
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


SUMMARY = "id,kind,status,created,updated,attempt,progress,total,error"


class Store:
    def __init__(self, path, *, capacity=100):
        self.path = Path(path)
        self.capacity = capacity

    @contextmanager
    def connection(self, write=False):
        db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            if write:
                db.execute("BEGIN IMMEDIATE")
            yield db
            if write:
                db.commit()
        finally:
            db.close()  # SQLite rolls back an incomplete transaction.

    def initialize(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.execute("PRAGMA journal_mode=WAL")
        with self.connection(True) as db:
            v = db.execute("PRAGMA user_version").fetchone()[0]
            if v == 1:
                return
            if v:
                raise RuntimeError("Unsupported workspace database schema.")
            db.execute("""CREATE TABLE packs (
                id TEXT PRIMARY KEY, name TEXT NOT NULL, size INTEGER NOT NULL, body TEXT NOT NULL, created REAL NOT NULL)""")
            db.execute("""CREATE TABLE runs (
                id TEXT PRIMARY KEY, kind TEXT NOT NULL, status TEXT NOT NULL, body TEXT NOT NULL,
                idem TEXT UNIQUE, created REAL NOT NULL, updated REAL NOT NULL, attempt INTEGER NOT NULL DEFAULT 0,
                fence TEXT, expires REAL, progress INTEGER NOT NULL DEFAULT 0, total INTEGER NOT NULL,
                result TEXT, error TEXT)""")
            db.execute("CREATE INDEX runs_queue ON runs(status,created)")
            db.execute("""CREATE TABLE events (
                seq INTEGER PRIMARY KEY, run_id TEXT NOT NULL, at REAL NOT NULL, state TEXT NOT NULL, attempt INTEGER NOT NULL)""")
            db.execute("CREATE INDEX events_run ON events(run_id,seq)")
            db.execute("CREATE TABLE workers (id TEXT PRIMARY KEY, seen REAL NOT NULL)")
            db.execute("PRAGMA user_version=1")

    def put_pack(self, pack):
        body = canonical(pack)
        key = hashlib.sha256(body.encode()).hexdigest()
        with self.connection(True) as db:
            db.execute("INSERT OR IGNORE INTO packs VALUES(?,?,?,?,?)",
                       (key, pack["name"], len(pack["documents"]), body, time.time()))
        return key

    def packs(self):
        with self.connection() as db:
            return [dict(r) for r in db.execute("SELECT id,name,size,created FROM packs ORDER BY created DESC LIMIT 100")]

    def pack(self, key):
        with self.connection() as db:
            row = db.execute("SELECT body FROM packs WHERE id=?", (key,)).fetchone()
            return json.loads(row[0]) if row else None

    @staticmethod
    def _event(db, run_id, state, attempt, now):
        db.execute("INSERT INTO events(run_id,at,state,attempt) VALUES(?,?,?,?)", (run_id, now, state, attempt))

    def submit(self, spec, idem=None, total=1):
        body, now = canonical(spec), time.time()
        key = hashlib.sha256(idem.encode()).hexdigest() if idem else None
        with self.connection(True) as db:
            if key:
                prior = db.execute("SELECT id,body FROM runs WHERE idem=?", (key,)).fetchone()
                if prior:
                    if prior["body"] != body:
                        raise Conflict("This idempotency key belongs to a different submission.")
                    return prior["id"]
            count = db.execute("SELECT COUNT(*) FROM runs WHERE status IN ('queued','running')").fetchone()[0]
            if count >= self.capacity:
                raise QueueFull()
            run_id = uuid4().hex
            db.execute("""INSERT INTO runs(id,kind,status,body,idem,created,updated,total)
                          VALUES(?,?,'queued',?,?,?,?,?)""", (run_id, spec["kind"], body, key, now, now, total))
            self._event(db, run_id, "queued", 0, now)
            return run_id

    def list_runs(self, *, limit=25, offset=0, status=None):
        with self.connection() as db:
            return [dict(r) for r in db.execute(
                f"SELECT {SUMMARY} FROM runs WHERE (? IS NULL OR status=?) ORDER BY created DESC,id DESC LIMIT ? OFFSET ?",
                (status, status, limit, offset))]

    def get(self, run_id):
        with self.connection() as db:
            db.execute("BEGIN")
            r = db.execute(f"SELECT {SUMMARY},body,result FROM runs WHERE id=?", (run_id,)).fetchone()
            if not r:
                return None
            data = dict(r)
            data["request"] = json.loads(data.pop("body"))
            data["result"] = json.loads(data["result"]) if data["result"] else None
            data["events"] = [dict(e) for e in db.execute(
                "SELECT seq,at,state,attempt FROM events WHERE run_id=? ORDER BY seq", (run_id,))]
            return data

    def claim(self, *, lease=45, max_attempts=3):
        now = time.time()
        with self.connection(True) as db:
            exhausted = db.execute("""SELECT id,attempt FROM runs WHERE status='running'
                                      AND expires<=? AND attempt>=?""", (now, max_attempts)).fetchall()
            for row in exhausted:
                db.execute("""UPDATE runs SET status='failed',error='worker_lost',fence=NULL,expires=NULL,updated=?
                              WHERE id=?""", (now, row["id"]))
                self._event(db, row["id"], "failed", row["attempt"], now)
            row = db.execute("""SELECT id,body,attempt FROM runs WHERE status='queued'
                                OR (status='running' AND expires<=?) ORDER BY created,id LIMIT 1""", (now,)).fetchone()
            if row is None:
                return None
            fence, attempt = uuid4().hex, row["attempt"] + 1
            db.execute("""UPDATE runs SET status='running',attempt=?,fence=?,expires=?,progress=0,updated=? WHERE id=?""",
                       (attempt, fence, now + lease, now, row["id"]))
            self._event(db, row["id"], "reclaimed" if attempt > 1 else "running", attempt, now)
            return row["id"], fence, json.loads(row["body"])

    def renew(self, run_id, fence, *, lease=45):
        now = time.time()
        with self.connection(True) as db:
            return bool(db.execute("""UPDATE runs SET expires=? WHERE id=? AND status='running' AND fence=? AND expires>?""",
                                   (now + lease, run_id, fence, now)).rowcount)

    def advance(self, run_id, fence, count):
        now = time.time()
        with self.connection(True) as db:
            return bool(db.execute("""UPDATE runs SET progress=?,updated=?
                                      WHERE id=? AND fence=? AND expires>? AND status='running'""",
                                   (count, now, run_id, fence, now)).rowcount)

    def finish(self, run_id, fence, result=None, error=None):
        now = time.time()
        status = "failed" if error else "completed"
        with self.connection(True) as db:
            row = db.execute("""UPDATE runs SET status=?,result=?,error=?,fence=NULL,expires=NULL,updated=?,
                                progress=CASE WHEN ?='completed' THEN total ELSE progress END
                                WHERE id=? AND fence=? AND status='running' AND expires>? RETURNING attempt""",
                             (status, canonical(result) if result is not None else None, error, now, status,
                              run_id, fence, now)).fetchone()
            if row:
                self._event(db, run_id, status, row[0], now)
            return bool(row)

    def cancel(self, run_id):
        now = time.time()
        with self.connection(True) as db:
            row = db.execute("SELECT status,attempt FROM runs WHERE id=?", (run_id,)).fetchone()
            if row is None:
                return False
            if row["status"] == "cancelled":
                return True
            if row["status"] not in {"queued", "running"}:
                raise Conflict("The run has already finished.")
            db.execute("UPDATE runs SET status='cancelled',fence=NULL,expires=NULL,updated=? WHERE id=?", (now, run_id))
            self._event(db, run_id, "cancelled", row["attempt"], now)
            return True

    def heartbeat(self, worker):
        now = time.time()
        with self.connection(True) as db:
            db.execute("DELETE FROM workers WHERE seen<?", (now - 90,))
            db.execute("INSERT OR REPLACE INTO workers VALUES(?,?)", (worker, now))

    def stats(self):
        with self.connection() as db:
            counts = dict(db.execute("SELECT status,COUNT(*) FROM runs GROUP BY status"))
            workers = db.execute("SELECT COUNT(*) FROM workers WHERE seen>?", (time.time() - 30,)).fetchone()[0]
            return {"runs": counts, "workers": workers, "capacity": self.capacity}

    def backup(self, destination):
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("xb"):
            pass
        try:
            with self.connection() as source:
                target = sqlite3.connect(destination)
                try:
                    source.backup(target)
                finally:
                    target.close()
        except BaseException:
            destination.unlink(missing_ok=True)
            raise
