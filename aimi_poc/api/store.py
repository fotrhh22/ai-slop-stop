"""Durable store: local SQLite WAL or PostgreSQL via DATABASE_URL."""
import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS poc_studies (id TEXT PRIMARY KEY, config TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS poc_sessions (
 id TEXT PRIMARY KEY, token_hash TEXT NOT NULL UNIQUE, study_id TEXT NOT NULL,
 state TEXT NOT NULL, seed INTEGER NOT NULL, pre_answers TEXT, post_answers TEXT,
 pre_draft TEXT, post_draft TEXT, training_count INTEGER NOT NULL DEFAULT 0,
 training_passed INTEGER NOT NULL DEFAULT 0, explanation_viewed_at TEXT,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS poc_jobs (
 id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES poc_sessions(id),
 phase TEXT NOT NULL, prompt TEXT NOT NULL, seed INTEGER NOT NULL,
 status TEXT NOT NULL, generate_id TEXT NOT NULL, route_id TEXT NOT NULL,
 result_image TEXT, verdict TEXT, reason TEXT, criteria TEXT, error TEXT,
 generation_s REAL, router_s REAL, model_metadata TEXT,
 created_at TEXT NOT NULL, started_at TEXT, finished_at TEXT,
 UNIQUE(session_id, phase));
CREATE TABLE IF NOT EXISTS poc_training_attempts (
 id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES poc_sessions(id),
 idempotency_key TEXT NOT NULL, selected TEXT NOT NULL, passed INTEGER NOT NULL,
 created_at TEXT NOT NULL, UNIQUE(session_id, idempotency_key));
CREATE TABLE IF NOT EXISTS poc_events (
 id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES poc_sessions(id),
 event TEXT NOT NULL, phase TEXT NOT NULL, at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS poc_submissions (
 id TEXT PRIMARY KEY, session_id TEXT NOT NULL UNIQUE REFERENCES poc_sessions(id),
 number INTEGER NOT NULL UNIQUE, submitted_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS poc_counters (id INTEGER PRIMARY KEY, value INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS poc_session_profiles (session_id TEXT PRIMARY KEY REFERENCES poc_sessions(id), profile_id TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS poc_api_calls (id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, status TEXT NOT NULL, response TEXT, error TEXT);
CREATE TABLE IF NOT EXISTS poc_runtimes (study_id TEXT PRIMARY KEY, pod_id TEXT NOT NULL, url TEXT NOT NULL, verified_at TEXT NOT NULL);
INSERT INTO poc_counters(id,value) VALUES(1,0) ON CONFLICT(id) DO NOTHING;
CREATE INDEX IF NOT EXISTS poc_jobs_queue ON poc_jobs(status,created_at);
CREATE INDEX IF NOT EXISTS poc_events_session ON poc_events(session_id);
"""


class Connection:
    def __init__(self, raw, postgres):
        self.raw, self.postgres = raw, postgres

    def execute(self, sql, values=()):
        return self.raw.execute(sql.replace("?", "%s") if self.postgres else sql, values)


class Store:
    def __init__(self, url=None, path=None):
        self.url = url if url is not None else os.getenv("DATABASE_URL", "")
        self.path = Path(path or os.getenv("POC_DB_PATH", "data/poc.sqlite3"))
        if not self.url:
            self.path.parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def worker_owner(self):
        """Only one inference consumer may own a study database, including replicas."""
        if self.url:
            import psycopg
            raw = psycopg.connect(self.url, autocommit=True)
            if not raw.execute("SELECT pg_try_advisory_lock(16111502)").fetchone()[0]:
                raw.close()
                raise RuntimeError("Another PoC queue worker already owns this database")
            try:
                yield
            finally:
                raw.close()
        else:
            import fcntl
            with open(str(self.path) + ".owner.lock", "a") as lock:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError as e:
                    raise RuntimeError("Use one API instance and uvicorn --workers 1") from e
                yield

    @contextmanager
    def db(self, write=False):
        if self.url:
            import psycopg
            from psycopg.rows import dict_row
            raw = psycopg.connect(self.url, row_factory=dict_row, connect_timeout=10)
        else:
            raw = sqlite3.connect(self.path, timeout=30)
            raw.row_factory = sqlite3.Row
            raw.execute("PRAGMA foreign_keys=ON")
            raw.execute("PRAGMA busy_timeout=30000")
        conn = Connection(raw, bool(self.url))
        try:
            if write:
                if self.url:
                    conn.execute("SELECT pg_advisory_xact_lock(16111501)")
                else:
                    raw.execute("BEGIN IMMEDIATE")
            yield conn
            raw.commit()
        except Exception:
            raw.rollback()
            raise
        finally:
            raw.close()

    def initialize(self, config):
        if not self.url:
            with sqlite3.connect(self.path) as db:
                db.execute("PRAGMA journal_mode=WAL")
        with self.db(write=True) as db:
            for sql in SCHEMA.split(";"):
                if sql.strip():
                    db.execute(sql)
            if self.url:
                # Supabase REST must not expose anonymous session tokens or answers.
                for table in ["poc_studies", "poc_sessions", "poc_jobs", "poc_training_attempts", "poc_events", "poc_submissions", "poc_counters", "poc_runtimes", "poc_session_profiles", "poc_api_calls"]:
                    db.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
                    for role in ("anon", "authenticated"):
                        if db.execute("SELECT 1 FROM pg_roles WHERE rolname=?", (role,)).fetchone():
                            db.execute(f"REVOKE ALL ON TABLE {table} FROM {role}")
            row = db.execute("SELECT config FROM poc_studies WHERE id=?", (config["id"],)).fetchone()
            value = json.dumps(config, sort_keys=True, ensure_ascii=False)
            if row and row["config"] != value:
                raise RuntimeError("Study configuration changed: use a NEW POC_STUDY_ID; preserve existing study data")
            db.execute("INSERT INTO poc_studies(id,config) VALUES(?,?) ON CONFLICT(id) DO NOTHING", (config["id"], value))

