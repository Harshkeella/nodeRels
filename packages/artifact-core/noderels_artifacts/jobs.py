"""A durable bounded queue. SQLite transactions claim work across local processes."""
import json
import sqlite3
import time
import uuid
import os
import shutil
from contextlib import contextmanager
from pathlib import Path


class Jobs:
    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS jobs (
                  id TEXT PRIMARY KEY, owner TEXT NOT NULL, session TEXT,
                  state TEXT NOT NULL, payload TEXT NOT NULL, result TEXT,
                  error TEXT, created REAL NOT NULL, updated REAL NOT NULL);
                CREATE INDEX IF NOT EXISTS jobs_owner ON jobs(owner, created);
                CREATE INDEX IF NOT EXISTS jobs_queue ON jobs(state, created);
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.root / "jobs.sqlite3", timeout=15)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def folder(self, job_id: str) -> Path:
        if str(uuid.UUID(job_id)) != job_id:
            raise ValueError("Invalid artifact id")
        return self.root / job_id

    def submit(self, owner: str, payload: dict, session: str | None = None, job_id: str | None = None) -> dict:
        job_id = job_id or str(uuid.uuid4())
        self.folder(job_id)
        encoded = json.dumps(payload, ensure_ascii=False)
        if len(encoded) > 500_000:
            raise ValueError("Request too large")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            if existing:
                if existing["owner"] != owner or existing["payload"] != encoded:
                    raise ValueError("Request id already used")
                return self.decode(existing)
            retained = db.execute("SELECT COUNT(*) FROM jobs WHERE owner=?", (owner,)).fetchone()[0]
            if retained >= int(os.getenv("ARTIFACT_MAX_SAVED_PER_USER", "100")):
                raise ValueError("Saved artifact limit reached. Ask your administrator to adjust retention or the saved artifact limit.")
            if shutil.disk_usage(self.root).free < int(os.getenv("ARTIFACT_MIN_FREE_BYTES", str(1024**3))):
                raise ValueError("Artifact storage is almost full. Free disk space before generating more.")
            active = db.execute("SELECT COUNT(*), SUM(owner=?) FROM jobs WHERE state IN ('queued','running')", (owner,)).fetchone()
            if active[0] >= 100 or (active[1] or 0) >= 3:
                raise ValueError("Generation queue is full. Wait for an existing job to finish.")
            now = time.time()
            db.execute("INSERT INTO jobs VALUES (?, ?, ?, 'queued', ?, NULL, NULL, ?, ?)",
                       (job_id, owner, session, encoded, now, now))
        return self.get(owner, job_id)

    @staticmethod
    def decode(row) -> dict:
        return {**dict(row), "payload": json.loads(row["payload"]), "result": json.loads(row["result"] or "null")}

    def get(self, owner: str, job_id: str) -> dict:
        self.folder(job_id)
        with self.connect() as db:
            row = db.execute("SELECT * FROM jobs WHERE id=? AND owner=?", (job_id, owner)).fetchone()
        if row is None:
            raise KeyError("Artifact not found")
        return self.decode(row)

    def claim(self) -> dict | None:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            # Work interrupted by a crashed process is explicit, never left spinning forever.
            db.execute("UPDATE jobs SET state='failed', error='Generation was interrupted. Please retry.' WHERE state='running' AND updated < ?", (time.time() - 120,))
            row = db.execute("SELECT * FROM jobs WHERE state='queued' ORDER BY created LIMIT 1").fetchone()
            if row is None:
                return None
            db.execute("UPDATE jobs SET state='running', updated=? WHERE id=?", (time.time(), row["id"]))
        return self.decode(row)

    def update(self, job_id: str, *, state="running", result=None, error=None):
        with self.connect() as db:
            db.execute("UPDATE jobs SET state=?, result=COALESCE(?, result), error=?, updated=? WHERE id=?",
                       (state, json.dumps(result) if result is not None else None, error, time.time(), job_id))

    def touch(self, job_id):
        with self.connect() as db:
            db.execute("UPDATE jobs SET updated=? WHERE id=? AND state='running'", (time.time(), job_id))

    def session_jobs(self, owner: str, session: str) -> list[dict]:
        with self.connect() as db:
            return [self.decode(r) for r in db.execute("SELECT * FROM jobs WHERE owner=? AND session=? ORDER BY created", (owner, session))]

    def prune(self, days: int = 30):
        """Remove only expired generated files under this queue's own directory."""
        with self.connect() as db:
            rows = db.execute("SELECT id FROM jobs WHERE state IN ('done','failed') AND updated < ?", (time.time() - days * 86400,)).fetchall()
            for row in rows:
                folder = self.folder(row["id"])
                if folder.parent != self.root or folder.is_symlink():
                    raise ValueError("Unsafe artifact directory")
                if folder.exists():
                    shutil.rmtree(folder)
                db.execute("DELETE FROM jobs WHERE id=?", (row["id"],))


async def work(queue: Jobs, handler):
    import asyncio
    import logging

    async def heartbeat(job_id):
        while True:
            await asyncio.sleep(20)
            await asyncio.to_thread(queue.touch, job_id)

    last_cleanup = time.monotonic()
    while True:
        if time.monotonic() - last_cleanup > 3600:
            await asyncio.to_thread(queue.prune, int(os.getenv("ARTIFACT_RETENTION_DAYS", "30")))
            last_cleanup = time.monotonic()
        job = await asyncio.to_thread(queue.claim)
        if not job:
            await asyncio.sleep(1)
            continue
        pulse = asyncio.create_task(heartbeat(job["id"]))
        try:
            result = await asyncio.wait_for(handler(job), timeout=3600)
            await asyncio.to_thread(queue.update, job["id"], state="done", result=result)
        except asyncio.CancelledError:
            queue.update(job["id"], state="failed", error="Server stopped during generation. Please retry.")
            raise
        except Exception as exc:
            logging.getLogger("artifacts").exception("Artifact job %s failed", job["id"])
            message = str(exc) if isinstance(exc, ValueError) else "Generation failed. Check the service logs and retry."
            queue.update(job["id"], state="failed", error=message[:300])
        finally:
            pulse.cancel()
            await asyncio.gather(pulse, return_exceptions=True)
