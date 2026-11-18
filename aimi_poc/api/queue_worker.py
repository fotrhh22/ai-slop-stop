import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter

from .gpu import GPUError

log = logging.getLogger("aimi.poc.queue")


def now():
    return datetime.now(timezone.utc).isoformat()


class QueueWorker:
    def __init__(self, store, gpu, media):
        self.store, self.gpu, self.media = store, gpu, Path(media)
        self.media.mkdir(parents=True, exist_ok=True)
        self.active = 0
        self.peak_active = 0
        self.stopping = False
        self.runtime_lock = asyncio.Lock()
        self.clients = {}

    def recover(self):
        with self.store.db(write=True) as db:
            db.execute("UPDATE poc_jobs SET status='queued' WHERE status IN ('running','generating','judging') AND session_id IN (SELECT id FROM poc_sessions WHERE study_id=?)", (self.gpu.config["id"],))

    async def run(self):
        self.recover()
        while not self.stopping:
            with self.store.db(write=True) as db:
                row = db.execute("SELECT poc_jobs.*,poc_session_profiles.profile_id FROM poc_jobs LEFT JOIN poc_session_profiles ON poc_session_profiles.session_id=poc_jobs.session_id WHERE status='queued' AND poc_jobs.session_id IN (SELECT id FROM poc_sessions WHERE study_id=?) ORDER BY created_at,id LIMIT 1", (self.gpu.config["id"],)).fetchone()
                if row:
                    row = dict(row)
                    db.execute("UPDATE poc_jobs SET status='running',started_at=COALESCE(started_at,?) WHERE id=?", (now(), row["id"]))
            if not row:
                await asyncio.sleep(0.08)
                continue
            self.active += 1
            self.peak_active = max(self.peak_active, self.active)
            try:
                async with self.runtime_lock:
                    await self.execute(row)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                code = str(exc) if isinstance(exc, GPUError) else "GPU_CONNECTION_OR_PROCESSING_ERROR"
                log.warning("job=%s error=%s", row["id"], code)
                with self.store.db(write=True) as db:
                    db.execute("UPDATE poc_jobs SET status='error',error=?,finished_at=? WHERE id=?", (code[:160], now(), row["id"]))
            finally:
                self.active -= 1

    async def execute(self, row):
        gpu = getattr(self, 'clients', {}).get(row.get('profile_id'), self.gpu)
        health = await gpu.health()
        existing_meta = json.loads(row['model_metadata'] or '{}')
        runtime_id = health.get('runtime_id')
        if existing_meta.get('runtime_id') and existing_meta['runtime_id'] != runtime_id:
            raise GPUError('GPU_WORKER_CHANGED_UNCONFIRMED')
        existing_meta['runtime_id'] = runtime_id
        existing_meta['profile_id'] = row.get('profile_id') or gpu.mode
        with self.store.db(write=True) as db:
            db.execute('UPDATE poc_jobs SET model_metadata=? WHERE id=?', (json.dumps(existing_meta), row['id']))
        with self.store.db(write=True) as db:
            db.execute("UPDATE poc_jobs SET status='generating',error=NULL WHERE id=?", (row["id"],))
        if row["result_image"] and (self.media / row["result_image"]).is_file():
            raw = (self.media / row["result_image"]).read_bytes()
            meta = existing_meta
        else:
            start = perf_counter()
            raw, genmeta = await gpu.generate(row["generate_id"], row["prompt"], row["seed"])
            filename = row["id"] + ".png"
            temp = self.media / (filename + ".tmp")
            temp.write_bytes(raw)
            os.replace(temp, self.media / filename)
            meta = {**existing_meta, "generation": genmeta}
            with self.store.db(write=True) as db:
                db.execute("UPDATE poc_jobs SET result_image=?,generation_s=?,model_metadata=? WHERE id=?", (filename, perf_counter() - start, json.dumps(meta), row["id"]))
        with self.store.db(write=True) as db:
            db.execute("UPDATE poc_jobs SET status='judging' WHERE id=?", (row["id"],))
        start = perf_counter()
        verdict, route_meta = await gpu.route(row["route_id"], raw, row["prompt"])
        route_s = perf_counter() - start
        meta["router"] = route_meta
        state = ("post" if row["phase"].startswith("post") else "pre") + "_result"
        with self.store.db(write=True) as db:
            db.execute("UPDATE poc_jobs SET status='done',verdict=?,reason=?,criteria=?,router_s=?,model_metadata=?,finished_at=? WHERE id=?", (verdict["verdict"], verdict.get("reason"), json.dumps(verdict["criteria"]) if verdict.get("criteria") else None, route_s, json.dumps(meta), now(), row["id"]))
            db.execute("UPDATE poc_sessions SET state=?,updated_at=? WHERE id=?", (state, now(), row["session_id"]))
