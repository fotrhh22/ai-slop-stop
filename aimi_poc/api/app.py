import asyncio
import contextlib
import csv
import hashlib
import io
import json
import os
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import schemas, study
from .gemini import GeminiClient
from .gpu import GPUClient
from .queue_worker import QueueWorker, now
from .store import Store


def fail(code, status=409):
    raise HTTPException(status, code)


def encode(value):
    return json.dumps(value, ensure_ascii=False)


def create_app(store=None, mode=None):
    mode = mode or os.getenv("POC_GPU_MODE", "mock")
    if mode not in ("mock", "runpod"):
        raise RuntimeError("POC_GPU_MODE must be mock or runpod")
    cfg = study.config(mode)
    if mode == "runpod" and not cfg["target_approved"] and not cfg['is_test']:
        raise RuntimeError("Approve assessment target A in content/server/study.json before real participant collection")
    store = store or Store(url=os.getenv("POC_DATABASE_URL", os.getenv("DATABASE_URL", "")), path=os.getenv("POC_DB_PATH", str(study.ROOT / "data/poc.sqlite3")))
    media = Path(os.getenv("POC_MEDIA_PATH", str(study.ROOT / "data/images")))
    gpu = GPUClient(mode, cfg)
    clients = {"runpod": gpu, "gemini": GeminiClient(cfg, store)} if mode != "mock" else {"mock": gpu}
    worker = QueueWorker(store, gpu, media)
    worker.clients = clients
    cookie = "aimi_poc_session"

    @asynccontextmanager
    async def lifespan(app):
        store.initialize(cfg)
        if mode == 'runpod':
            with store.db() as db:
                runtime = db.execute('SELECT url FROM poc_runtimes WHERE study_id=?', (cfg['id'],)).fetchone()
                if runtime:
                    gpu.url = runtime['url']
        with store.worker_owner():
            task = asyncio.create_task(worker.run())
            try:
                yield
            finally:
                worker.stopping = True
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

    app = FastAPI(title="AIMI 교육 효과 PoC", lifespan=lifespan)
    app.state.store, app.state.worker, app.state.config = store, worker, cfg

    @app.middleware("http")
    async def boundaries(request, call_next):
        path = request.url.path
        root = request.scope.get("root_path", "")
        if root and path.startswith(root):
            path = path[len(root):]
        if path.startswith("/api/"):
            if request.method not in ("GET", "HEAD", "OPTIONS"):
                if request.headers.get("X-Poc-Request") != "1":
                    return JSONResponse({"detail": "REQUEST_HEADER_REQUIRED"}, status_code=403)
                origin = request.headers.get("Origin")
                allowed = {str(request.base_url).rstrip("/"), *filter(None, os.getenv("POC_ALLOWED_ORIGINS", "").split(","))}
                if origin and origin not in allowed:
                    return JSONResponse({"detail": "ORIGIN_NOT_ALLOWED"}, status_code=403)
                if len(await request.body()) > 64_000:
                    return JSONResponse({"detail": "REQUEST_TOO_LARGE"}, status_code=413)
            response = await call_next(request)
            response.headers["Cache-Control"] = "no-store"
            response.headers["X-Content-Type-Options"] = "nosniff"
            return response
        return await call_next(request)

    def session(request: Request):
        token = request.cookies.get(cookie, "")
        with store.db() as db:
            row = db.execute("SELECT * FROM poc_sessions WHERE token_hash=? AND study_id=?", (hashlib.sha256(token.encode()).hexdigest(), cfg["id"])).fetchone()
        if not row:
            fail("SESSION_REQUIRED", 401)
        return dict(row)

    def require_state(db, sid, expected):
        row = dict(db.execute("SELECT * FROM poc_sessions WHERE id=?", (sid,)).fetchone())
        if row["state"] not in expected:
            fail("STAGE_NOT_ALLOWED")
        return row

    def public_job(db, row):
        item = {k: row[k] for k in ["id", "phase", "prompt", "status", "verdict", "error", "created_at", "started_at", "finished_at", "generation_s", "router_s"]}
        if row["result_image"]:
            item["image"] = "/api/poc/images/" + row["id"]
        if row["status"] == "queued":
            item["position"] = db.execute("SELECT COUNT(*) AS n FROM poc_jobs WHERE status IN ('queued','running','generating','judging') AND session_id IN (SELECT id FROM poc_sessions WHERE study_id=?) AND (created_at < ? OR (created_at=? AND id<=?))", (cfg["id"], row["created_at"], row["created_at"], row["id"])).fetchone()["n"]
        return item

    def snapshot(sid):
        with store.db() as db:
            row = dict(db.execute("SELECT * FROM poc_sessions WHERE id=?", (sid,)).fetchone())
            submission = db.execute("SELECT id,submitted_at FROM poc_submissions WHERE session_id=?", (sid,)).fetchone()
            jobs = db.execute("SELECT * FROM poc_jobs WHERE session_id=? ORDER BY created_at", (sid,)).fetchall()
            initial = next((j for j in jobs if j["phase"] == "pre"), None)
            learned = bool(initial and initial["verdict"] == "FAIL")
            profile = db.execute("SELECT profile_id FROM poc_session_profiles WHERE session_id=?", (sid,)).fetchone()
            return {"profile_id": profile["profile_id"] if profile else mode, "state": row["state"], "mode": mode, "study_id": cfg["id"], "training_count": row["training_count"],
                    "training_passed": bool(row["training_passed"]), "learned": learned, "post_attempt_limit": 2,
                    "pre_answers": json.loads(row["pre_answers"]) if row["pre_answers"] else None,
                    "post_answers": json.loads(row["post_answers"]) if row["post_answers"] else None,
                    "draft": row["post_draft"] if row["state"].startswith("post") else row["pre_draft"],
                    "submission": dict(submission) if submission else None, "jobs": [public_job(db, j) for j in jobs]}

    @app.get("/health")
    def health():
        return {"ok": True, "mode": mode, "is_test": cfg["is_test"], "energy_measurement": False, "queue_peak": worker.peak_active, "study_id": cfg["id"], "target_approved": cfg["target_approved"], "prompt_limits": study.PROMPT_LIMITS}

    @app.get("/api/poc/config")
    def public_config():
        return {"mode": mode, "is_test": cfg['is_test'], "title": cfg["title"], "target": "/content/target-a.png", "training_limit": cfg["training_limit"], "post_attempt_limit": 2,
                "writing_items": study.WRITING, "pre_options": study.PRE_OPTIONS, "pre_labels": study.PRE_LABELS,
                "learning_items": study.TRAINING_ITEMS, "features": study.FEATURES, "revisit_items": study.REVISIT,
                "target_approved": cfg["target_approved"], "prompt_limits": study.PROMPT_LIMITS, "profiles": [{"id": "runpod", "label": "RTX 5090 · Klein 9B / Qwen", "available": mode != "mock" and bool(gpu.key and gpu.url)}, {"id": "gemini", "label": "Gemini", "available": mode != "mock" and bool(clients.get("gemini") and clients["gemini"].key)}] + ([{"id":"mock","label":"모의 실행","available":True}] if mode == "mock" else [])}

    @app.post("/api/poc/session/reset")
    def reset_session(response: Response):
        response.delete_cookie(cookie, path="/", httponly=True,
                               secure=os.getenv("POC_SECURE_COOKIES", "false").lower() == "true", samesite="lax")
        return {"reset": True}

    @app.post("/api/poc/sessions")
    def start(req: schemas.Start, request: Request, response: Response):
        profile_id = req.profile_id or mode
        if profile_id not in clients or (profile_id == "gemini" and not os.getenv("GEMINI_API_KEY")):
            fail("MODEL_PROFILE_UNAVAILABLE", 400)
        token = request.cookies.get(cookie, "")
        with store.db(write=True) as db:
            existing = db.execute("SELECT id FROM poc_sessions WHERE token_hash=? AND study_id=?", (hashlib.sha256(token.encode()).hexdigest(), cfg["id"])).fetchone()
            if existing:
                sid = existing["id"]
                current_profile = db.execute("SELECT profile_id FROM poc_session_profiles WHERE session_id=?", (sid,)).fetchone()
                if current_profile and current_profile["profile_id"] != profile_id:
                    fail("SESSION_MODEL_LOCKED")
            else:
                token, sid = secrets.token_urlsafe(32), str(uuid4())
                db.execute("INSERT INTO poc_sessions(id,token_hash,study_id,state,seed,created_at,updated_at) VALUES(?,?,?,?,?,?,?)", (sid, hashlib.sha256(token.encode()).hexdigest(), cfg["id"], "pre_survey", secrets.randbelow(2**31), now(), now()))
            db.execute("INSERT INTO poc_session_profiles(session_id,profile_id) VALUES(?,?) ON CONFLICT(session_id) DO NOTHING", (sid, profile_id))
        response.set_cookie(cookie, token, httponly=True, secure=os.getenv("POC_SECURE_COOKIES", "false").lower() == "true", samesite="lax", max_age=7 * 86400, path="/")
        return snapshot(sid)

    @app.get("/api/poc/session")
    def current(s=Depends(session)):
        return snapshot(s["id"])

    @app.put("/api/poc/surveys/pre")
    def pre(req: schemas.PreSurvey, s=Depends(session)):
        data = encode(req.model_dump())
        with store.db(write=True) as db:
            row = dict(db.execute("SELECT * FROM poc_sessions WHERE id=?", (s["id"],)).fetchone())
            if row["pre_answers"] == data:
                pass
            else:
                require_state(db, s["id"], ["pre_survey"])
                db.execute("UPDATE poc_sessions SET pre_answers=?,state='pre_assessment',updated_at=? WHERE id=?", (data, now(), s["id"]))
        return snapshot(s["id"])

    @app.put("/api/poc/draft")
    def draft(req: schemas.Draft, s=Depends(session)):
        if len(req.text) > study.PROMPT_LIMITS[req.phase]:
            fail("PROMPT_TOO_LONG", 422)
        with store.db(write=True) as db:
            require_state(db, s["id"], [req.phase + "_assessment"])
            col = "pre_draft" if req.phase == "pre" else "post_draft"
            db.execute(f"UPDATE poc_sessions SET {col}=?,updated_at=? WHERE id=?", (req.text, now(), s["id"]))
        return {"saved": True}

    @app.post("/api/poc/assessments/{phase}/attempts", status_code=202)
    def attempt(phase: str, req: schemas.Attempt, s=Depends(session)):
        if phase not in ("pre", "post") or not req.prompt.strip():
            fail("INVALID_ATTEMPT", 400)
        if len(req.prompt) > study.PROMPT_LIMITS[phase]:
            fail("PROMPT_TOO_LONG", 422)
        stored_phase = 'post2' if phase == 'post' and req.attempt_no == 2 else phase
        if phase == 'pre' and req.attempt_no != 1:
            fail('INVALID_ATTEMPT',400)
        with store.db(write=True) as db:
            existing = db.execute("SELECT * FROM poc_jobs WHERE session_id=? AND phase=?", (s["id"], stored_phase)).fetchone()
            if existing:
                if existing["prompt"] != req.prompt:
                    fail("ATTEMPT_ALREADY_SUBMITTED")
                return public_job(db, existing)
            row = require_state(db, s["id"], [phase + "_assessment"])
            if stored_phase == 'post2':
                prior = db.execute("SELECT verdict,status FROM poc_jobs WHERE session_id=? AND phase='post'", (s['id'],)).fetchone()
                if not prior or prior['status'] != 'done' or prior['verdict'] != 'FAIL':
                    fail('RETRY_NOT_AVAILABLE')
            jid = str(uuid4())
            db.execute("INSERT INTO poc_jobs(id,session_id,phase,prompt,seed,status,generate_id,route_id,created_at) VALUES(?,?,?,?,?,'queued',?,?,?)", (jid, s["id"], stored_phase, req.prompt, row["seed"], str(uuid4()), str(uuid4()), now()))
            db.execute("UPDATE poc_sessions SET state=?,updated_at=? WHERE id=?", (phase + "_pending", now(), s["id"]))
            return public_job(db, db.execute("SELECT * FROM poc_jobs WHERE id=?", (jid,)).fetchone())

    @app.post('/api/poc/test/skip')
    def test_skip(req: schemas.TestSkip, s=Depends(session)):
        if not cfg['is_test']:
            fail('TEST_MODE_REQUIRED',403)
        with store.db(write=True) as db:
            row=dict(db.execute('SELECT * FROM poc_sessions WHERE id=?',(s['id'],)).fetchone())
            state=row['state']
            if state != req.expected_state:
                pass  # A duplicate click must not skip another page.
            else:
                transitions={'pre_survey':'pre_assessment','pre_assessment':'training','pre_result':'training','training':'explanation','explanation':'post_assessment','post_assessment':'post_survey','post_result':'post_survey','post_survey':'ready_to_submit'}
                if state not in transitions:
                    fail('TEST_SKIP_NOT_AVAILABLE')
                at=now();updates={}
                if state=='pre_survey' and not row['pre_answers']:
                    updates['pre_answers']=encode({'test_skipped':True})
                if state in ('pre_assessment','pre_result','post_assessment','post_result'):
                    phase='pre' if state.startswith('pre') else 'post'
                    existing=db.execute("SELECT * FROM poc_jobs WHERE session_id=? AND phase LIKE ? ORDER BY created_at DESC LIMIT 1",(s['id'],phase+'%')).fetchone()
                    meta=json.loads(existing['model_metadata'] or '{}') if existing else {}
                    if existing:meta['original_verdict']=existing['verdict']
                    meta.update(test_skip=True,test_pass=True,inference_executed=bool(existing),assumed_first_fail=True)
                    verdict='FAIL' if phase=='pre' else 'PASS'
                    if existing:
                        db.execute("UPDATE poc_jobs SET verdict=?,model_metadata=? WHERE id=?",(verdict,encode(meta),existing['id']))
                    else:
                        db.execute("INSERT INTO poc_jobs(id,session_id,phase,prompt,seed,status,generate_id,route_id,verdict,model_metadata,created_at,finished_at) VALUES(?,?,?,?,?,'done',?,?,?,?,?,?)",(str(uuid4()),s['id'],phase,row[phase+'_draft'] or '',row['seed'],str(uuid4()),str(uuid4()),verdict,encode(meta),at,at))
                if state=='training':updates.update(training_count=max(1,row['training_count']),training_passed=0)
                if state=='explanation':
                    first=db.execute("SELECT prompt FROM poc_jobs WHERE session_id=? AND phase='pre'",(s['id'],)).fetchone()
                    updates.update(explanation_viewed_at=at,post_draft=row['post_draft'] if row['post_draft'] is not None else (first['prompt'] if first else ''))
                if state=='post_survey':updates['post_answers']=encode({'test_skipped':True})
                updates.update(state=transitions[state],updated_at=at)
                db.execute('UPDATE poc_sessions SET '+','.join(k+'=?' for k in updates)+' WHERE id=?',(*updates.values(),s['id']))
                db.execute('INSERT INTO poc_events(id,session_id,event,phase,at) VALUES(?,?,?,?,?)',(str(uuid4()),s['id'],'test_skip',state,at))
        return snapshot(s['id'])

    @app.post("/api/poc/assessments/{phase}/continue")
    def continue_result(phase: str, s=Depends(session)):
        if phase not in ("pre", "post"):
            fail("INVALID_ATTEMPT", 400)
        with store.db(write=True) as db:
            row = require_state(db, s["id"], [phase + "_result"])
            job = db.execute("SELECT phase,verdict,status,prompt FROM poc_jobs WHERE session_id=? AND phase IN (?,?) ORDER BY created_at DESC LIMIT 1", (s["id"], phase, 'post2' if phase == 'post' else 'pre')).fetchone()
            if not job or job["status"] != "done":
                fail("RESULT_NOT_READY")
            next_state = "training" if phase == "pre" and job["verdict"] == "FAIL" else "post_survey"
            if phase == 'post' and job['phase'] == 'post' and job['verdict'] == 'FAIL':
                next_state = 'post_assessment'
                db.execute("UPDATE poc_sessions SET post_draft=? WHERE id=?",(job['prompt'],row['id']))
            db.execute("UPDATE poc_sessions SET state=?,updated_at=? WHERE id=?", (next_state, now(), row["id"]))
        return snapshot(s["id"])

    @app.get("/api/poc/jobs/{jid}")
    def job(jid: str, s=Depends(session)):
        with store.db() as db:
            row = db.execute("SELECT * FROM poc_jobs WHERE id=? AND session_id=?", (jid, s["id"])).fetchone()
            if not row:
                fail("JOB_NOT_FOUND", 404)
            return public_job(db, row)

    @app.post("/api/poc/jobs/{jid}/recheck")
    def recheck(jid: str, s=Depends(session)):
        with store.db(write=True) as db:
            row = db.execute("SELECT * FROM poc_jobs WHERE id=? AND session_id=?", (jid, s["id"])).fetchone()
            if not row or row["status"] != "error":
                fail("JOB_NOT_RECHECKABLE")
            require_state(db, s["id"], [("post" if row["phase"].startswith("post") else "pre") + "_pending"])
            if row['error'] == 'GPU_ROUTE_FAILED' and row['result_image'] and (media / row['result_image']).is_file():
                metadata = json.loads(row['model_metadata'] or '{}')
                metadata.setdefault('route_retries', []).append({'request_id': row['route_id'], 'error': row['error']})
                db.execute("UPDATE poc_jobs SET route_id=?,model_metadata=? WHERE id=?", (str(uuid4()), json.dumps(metadata), jid))
            db.execute("UPDATE poc_jobs SET status='queued',error=NULL WHERE id=?", (jid,))
        return snapshot(s["id"])

    @app.post("/api/poc/technical-stop")
    def technical_stop(s=Depends(session)):
        with store.db(write=True) as db:
            row = require_state(db, s["id"], ["pre_pending", "post_pending"])
            jobrow = db.execute("SELECT status FROM poc_jobs WHERE session_id=? AND phase LIKE ? ORDER BY created_at DESC LIMIT 1", (s["id"], row["state"].split("_")[0]+"%")).fetchone()
            if not jobrow or jobrow["status"] != "error":
                fail("ERROR_STATE_REQUIRED")
            db.execute("UPDATE poc_sessions SET state='post_survey',updated_at=? WHERE id=?", (now(), s["id"]))
        return snapshot(s["id"])

    @app.get("/api/poc/images/{jid}")
    def image(jid: str, s=Depends(session)):
        with store.db() as db:
            row = db.execute("SELECT result_image FROM poc_jobs WHERE id=? AND session_id=?", (jid, s["id"])).fetchone()
        if not row or not row["result_image"]:
            fail("IMAGE_NOT_FOUND", 404)
        return FileResponse(media / row["result_image"], media_type="image/png")

    @app.get("/api/poc/training")
    def training(s=Depends(session)):
        with store.db() as db:
            row = require_state(db, s["id"], ["training", "explanation"])
            attempts = db.execute("SELECT selected,passed FROM poc_training_attempts WHERE session_id=? ORDER BY created_at,id", (s["id"],)).fetchall()
        public = {**study.TRAINING_PUBLIC, "target_image": "/content/training-a-low.png"}
        # Deterministic per-session ordering; the answer key is never sent here.
        group_meta = {k["id"]: k for k in study.TRAINING["candidate_keywords"]}
        public["keywords"] = [{**k, "group": group_meta[k["id"]]["context"], "group_order": group_meta[k["id"]]["sentence_order"]} for k in public["keywords"]]
        public["keywords"] = sorted(public["keywords"], key=lambda k: hashlib.sha256((s["id"] + k["id"]).encode()).hexdigest())
        return {**public, "answer_count": len(study.TRAINING["correct_keywords"]), "attempts_used": row["training_count"], "attempt_limit": cfg["training_limit"],
                "last_selected": json.loads(attempts[-1]["selected"]) if attempts else []}

    @app.post("/api/poc/training/attempts")
    def training_attempt(req: schemas.TrainingAttempt, s=Depends(session)):
        selected = sorted(req.selected)
        allowed = {k["id"] for k in study.TRAINING["candidate_keywords"]}
        answers = {k["id"] for k in study.TRAINING["correct_keywords"]}
        if len(set(selected)) != len(selected) or not set(selected) <= allowed:
            fail("INVALID_KEYWORDS", 400)
        with store.db(write=True) as db:
            previous = db.execute("SELECT * FROM poc_training_attempts WHERE session_id=? AND idempotency_key=?", (s["id"], str(req.idempotency_key))).fetchone()
            if previous:
                if previous["selected"] != encode(selected):
                    fail("TRAINING_REQUEST_CONFLICT")
                return {"passed": bool(previous["passed"]), "matched": [k for k in selected if k in answers], "incorrect": [k for k in selected if k not in answers]}
            row = require_state(db, s["id"], ["training"])
            passed = set(selected) == answers
            count = row["training_count"] + 1
            closed = passed or count >= cfg["training_limit"]
            db.execute("INSERT INTO poc_training_attempts(id,session_id,idempotency_key,selected,passed,created_at) VALUES(?,?,?,?,?,?)", (str(uuid4()), s["id"], str(req.idempotency_key), encode(selected), int(passed), now()))
            db.execute("UPDATE poc_sessions SET training_count=?,training_passed=?,state=?,updated_at=? WHERE id=?", (count, int(passed), "explanation" if closed else "training", now(), s["id"]))
        return {"passed": passed, "matched": [k for k in selected if k in answers], "incorrect": [k for k in selected if k not in answers], "closed": closed}

    @app.get("/api/poc/training/explanation")
    def explanation(s=Depends(session)):
        with store.db(write=True) as db:
            require_state(db, s["id"], ["explanation", "post_assessment", "post_pending", "post_result"])
            old = db.execute("SELECT id FROM poc_events WHERE session_id=? AND event='explanation_open'", (s["id"],)).fetchone()
            if not old:
                db.execute("INSERT INTO poc_events(id,session_id,event,phase,at) VALUES(?,?,?,?,?)", (str(uuid4()), s["id"], "explanation_open", "explanation", now()))
        return study.explanation()

    @app.post("/api/poc/training/explanation/complete")
    def finish_explanation(s=Depends(session)):
        with store.db(write=True) as db:
            require_state(db, s["id"], ["explanation"])
            first = db.execute("SELECT prompt FROM poc_jobs WHERE session_id=? AND phase='pre'", (s["id"],)).fetchone()
            db.execute("UPDATE poc_sessions SET explanation_viewed_at=?,state='post_assessment',post_draft=COALESCE(post_draft,?),updated_at=? WHERE id=?", (now(), first['prompt'] if first else '', now(), s["id"]))
        return snapshot(s["id"])

    @app.put("/api/poc/surveys/post")
    def post(req: schemas.PostSurvey, s=Depends(session)):
        data = encode(req.model_dump())
        with store.db(write=True) as db:
            row = dict(db.execute("SELECT * FROM poc_sessions WHERE id=?", (s["id"],)).fetchone())
            if row["post_answers"] != data:
                require_state(db, s["id"], ["post_survey"])
                if row["training_count"] and (req.learning is None or req.learning_help is None or not (req.revision or "").strip()):
                    fail("LEARNING_SURVEY_REQUIRED", 422)
                if not row["training_count"] and (req.learning is not None or req.learning_help is not None or req.revision):
                    fail("UNEXPERIENCED_LEARNING_SURVEY", 422)
                db.execute("UPDATE poc_sessions SET post_answers=?,state='ready_to_submit',updated_at=? WHERE id=?", (data, now(), s["id"]))
        return snapshot(s["id"])

    @app.post("/api/poc/submit")
    def submit(s=Depends(session)):
        with store.db(write=True) as db:
            old = db.execute("SELECT id,submitted_at FROM poc_submissions WHERE session_id=?", (s["id"],)).fetchone()
            if old:
                return dict(old)
            row = require_state(db, s["id"], ["ready_to_submit"])
            if not row["pre_answers"] or not row["post_answers"]:
                fail("SURVEYS_REQUIRED")
            db.execute("UPDATE poc_counters SET value=value+1 WHERE id=1")
            number = db.execute("SELECT value FROM poc_counters WHERE id=1").fetchone()["value"]
            code = ("TEST-" if cfg["is_test"] else "POC-") + f"{number:06d}"
            at = now()
            db.execute("INSERT INTO poc_submissions(id,session_id,number,submitted_at) VALUES(?,?,?,?)", (code, s["id"], number, at))
            db.execute("UPDATE poc_sessions SET state='submitted',updated_at=? WHERE id=?", (at, s["id"]))
        return {"id": code, "submitted_at": at}

    @app.post("/api/poc/events")
    def events(req: schemas.Event, s=Depends(session)):
        with store.db(write=True) as db:
            row = db.execute("SELECT state FROM poc_sessions WHERE id=?", (s["id"],)).fetchone()
            count = db.execute("SELECT COUNT(*) AS n FROM poc_events WHERE session_id=?", (s["id"],)).fetchone()["n"]
            if count < 500 and row["state"] != "submitted":
                db.execute("INSERT INTO poc_events(id,session_id,event,phase,at) VALUES(?,?,?,?,?)", (str(uuid4()), s["id"], req.event, row["state"], now()))
        return {"ok": True}

    def admin(authorization: str = Header(default="")):
        key = os.getenv("POC_ADMIN_TOKEN", "")
        if len(key) < 32 or not secrets.compare_digest(authorization, "Bearer " + key):
            fail("ADMIN_AUTH_REQUIRED", 403)

    def records():
        with store.db() as db:
            sessions = db.execute("SELECT * FROM poc_sessions WHERE study_id=? ORDER BY created_at", (cfg["id"],)).fetchall()
            result = []
            for row in sessions:
                item = dict(row)
                item.pop("token_hash")
                profile = db.execute("SELECT profile_id FROM poc_session_profiles WHERE session_id=?", (row["id"],)).fetchone()
                item["profile_id"] = profile["profile_id"] if profile else mode
                item["submission"] = dict(v) if (v := db.execute("SELECT id,submitted_at FROM poc_submissions WHERE session_id=?", (row["id"],)).fetchone()) else None
                item["attempts"] = [dict(v) for v in db.execute("SELECT * FROM poc_jobs WHERE session_id=? ORDER BY created_at", (row["id"],)).fetchall()]
                item["training"] = [dict(v) for v in db.execute("SELECT * FROM poc_training_attempts WHERE session_id=? ORDER BY created_at", (row["id"],)).fetchall()]
                item["events"] = [dict(v) for v in db.execute("SELECT event,phase,at FROM poc_events WHERE session_id=? ORDER BY at", (row["id"],)).fetchall()]
                result.append(item)
            return result

    @app.get("/api/poc/admin/results", dependencies=[Depends(admin)])
    def results():
        return {"study": cfg, "queue_peak": worker.peak_active, "sessions": records()}

    @app.get("/api/poc/admin/export.json", dependencies=[Depends(admin)])
    def export_json():
        return JSONResponse({"study": cfg, "sessions": records()}, headers={"Content-Disposition": 'attachment; filename="aimi-poc-results.json"'})

    @app.get("/api/poc/admin/images/{jid}", dependencies=[Depends(admin)])
    def admin_image(jid: str):
        with store.db() as db:
            row = db.execute("SELECT result_image FROM poc_jobs WHERE id=?", (jid,)).fetchone()
        if not row or not row["result_image"]:
            fail("IMAGE_NOT_FOUND", 404)
        return FileResponse(media / row["result_image"], media_type="image/png")

    @app.get("/api/poc/admin/export.csv", dependencies=[Depends(admin)])
    def export_csv():
        out = io.StringIO()
        writer = csv.writer(out)
        writer.writerow(["participant_id", "temporary_reference", "state", "mode", "profile_id", "test_pass", "pre_verdict", "post_verdict", "pre_prompt", "post_prompt", "pre_writing", "post_writing", "learning_survey", "learning_help", "features", "priority", "training_attempts", "events", "technical_errors", "submitted_at"])
        def safe(v):
            text = str(v)
            return "'" + text if text.lstrip().startswith(("=", "+", "-", "@")) else text
        for item in records():
            jobs = {j["phase"]: j for j in item["attempts"]}
            if "post2" in jobs: jobs["post"] = jobs["post2"]
            pre, post = json.loads(item["pre_answers"] or "{}"), json.loads(item["post_answers"] or "{}")
            sub = item["submission"] or {}
            writer.writerow([safe(v) for v in [sub.get("id", ""), item["id"], item["state"], mode, item["profile_id"], any(json.loads(j["model_metadata"] or "{}").get("test_pass",False) for j in item["attempts"]), jobs.get("pre", {}).get("verdict", ""), jobs.get("post", {}).get("verdict", ""), jobs.get("pre", {}).get("prompt", ""), jobs.get("post", {}).get("prompt", ""), encode(pre.get("writing")), encode(post.get("writing")), encode(post.get("learning")), post.get("learning_help", ""), encode(post.get("features")), encode(post.get("priority")), item["training_count"], len(item["events"]), encode([j["error"] for j in item["attempts"] if j["error"]]), sub.get("submitted_at", "")]])
        return Response("\ufeff" + out.getvalue(), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": 'attachment; filename="aimi-poc-results.csv"'})

    @app.get("/api/poc/admin/gpu", dependencies=[Depends(admin)])
    async def gpu_health():
        try:
            return await gpu.health()
        except Exception:
            fail("GPU_NOT_READY_OR_CONFIG_MISMATCH", 503)

    @app.post('/api/poc/admin/gpu/register', dependencies=[Depends(admin)])
    async def register_gpu(req: schemas.RuntimeRegistration):
        if mode != 'runpod':
            fail('RUNPOD_MODE_REQUIRED')
        if worker.runtime_lock.locked() or worker.active:
            fail('WAIT_FOR_ACTIVE_INFERENCE')
        async with worker.runtime_lock:
            candidate = GPUClient(mode, cfg)
            candidate.url = 'https://' + req.pod_id + '-8000.proxy.runpod.net'
            try:
                await candidate.health()
            except Exception:
                fail('GPU_NOT_READY_OR_CONFIG_MISMATCH', 503)
            with store.db(write=True) as db:
                db.execute('INSERT INTO poc_runtimes(study_id,pod_id,url,verified_at) VALUES(?,?,?,?) ON CONFLICT(study_id) DO UPDATE SET pod_id=excluded.pod_id,url=excluded.url,verified_at=excluded.verified_at', (cfg['id'], req.pod_id, candidate.url, now()))
            gpu.url = candidate.url
        return {'ready': True, 'pod_id': req.pod_id}

    app.mount("/content", StaticFiles(directory=study.CONTENT / "public"), name="content")
    dist = study.ROOT / "web/dist"
    if (dist / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="web-assets")

    @app.get("/")
    def index():
        if (dist / "index.html").is_file():
            return FileResponse(dist / "index.html")
        return JSONResponse({"detail": "WEB_BUILD_REQUIRED", "command": "cd web && npm ci && npm run build"}, status_code=503)

    return app


