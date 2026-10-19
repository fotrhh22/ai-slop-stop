"""Bounded, single-process evaluation jobs for the mock app's one Render worker.

Only small results are retained. Image payloads live only while a job runs.
A restart interrupts pending jobs; callers receive an explicit interrupted error.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
import hmac
import logging
import threading
from time import monotonic
from typing import Callable

from fastapi import HTTPException

from .persistence import PersistenceError, digest

_logger = logging.getLogger("uvicorn.error")


@dataclass
class Job:
    cid: str
    aid: str
    token_hash: str
    status: str = "running"
    result: dict | None = None
    error: str | None = None
    touched: float = field(default_factory=monotonic)


class EvaluationJobs:
    def __init__(self, workers=2, max_jobs=256, ttl=1800):
        self.executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="evaluation")
        self.slots = threading.BoundedSemaphore(workers)
        self.lock = threading.Lock()
        self.jobs: dict[str, Job] = {}
        self.max_jobs, self.ttl = max_jobs, ttl

    @staticmethod
    def _authorized(job, cid, token, aid=None):
        return (job.cid == cid and (aid is None or job.aid == aid) and bool(token)
                and hmac.compare_digest(job.token_hash, digest(token.encode())))

    @staticmethod
    def _response(jid, job):
        return {"job_id": jid, "status": job.status,
                **({"result": job.result} if job.status == "done" else {}),
                **({"error": job.error} if job.status == "error" else {})}

    def start(self, jid, cid, aid, token, work: Callable[[], dict]):
        with self.lock:
            existing = self.jobs.get(jid)
            if existing:
                if not self._authorized(existing, cid, token, aid):
                    raise HTTPException(403, "EVALUATION_ACCESS_DENIED")
                return self._response(jid, existing)
            # Retransmitted Try requests must join an existing running job.
            for key, job in self.jobs.items():
                if job.cid == cid and job.status == "running":
                    if job.aid == aid and self._authorized(job, cid, token):
                        return self._response(key, job)
                    raise HTTPException(409, "EVALUATION_IN_PROGRESS")
            now = monotonic()
            for key in list(self.jobs):
                job = self.jobs[key]
                if job.status != "running" and now - job.touched > self.ttl:
                    del self.jobs[key]
            if len(self.jobs) >= self.max_jobs:
                finished = [(job.touched, key) for key, job in self.jobs.items() if job.status != "running"]
                if finished:
                    del self.jobs[min(finished)[1]]
                else:
                    raise HTTPException(503, "EVALUATION_BUSY")
            if not self.slots.acquire(blocking=False):
                raise HTTPException(503, "EVALUATION_BUSY")
            job = Job(cid=cid, aid=aid, token_hash=digest(token.encode()))
            self.jobs[jid] = job
            try:
                self.executor.submit(self._run, job, work)
            except Exception:
                del self.jobs[jid]
                self.slots.release()
                raise
            return self._response(jid, job)

    def _run(self, job, work):
        try:
            result = work()
        except Exception as exc:
            if isinstance(exc, HTTPException):
                error = str(exc.detail)
            elif isinstance(exc, PersistenceError):
                error = str(exc)
            else:
                error = "EVALUATION_INTERNAL_ERROR"
                _logger.exception("Background evaluation failed")
            with self.lock:
                job.status, job.error, job.touched = "error", error, monotonic()
        else:
            with self.lock:
                job.status, job.result, job.touched = "done", result, monotonic()
        finally:
            self.slots.release()

    def status(self, jid, cid, token):
        with self.lock:
            job = self.jobs.get(jid)
            if job is None:
                raise HTTPException(404, "EVALUATION_INTERRUPTED")
            if not self._authorized(job, cid, token):
                raise HTTPException(403, "EVALUATION_ACCESS_DENIED")
            return self._response(jid, job)


jobs = EvaluationJobs()
