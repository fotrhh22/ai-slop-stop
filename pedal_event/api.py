"""Mountable /api/pedal router. Uses existing Render credentials, not a new service."""
import asyncio
import base64
import hashlib
import hmac
import io
import math
import os
import re
from functools import lru_cache
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field

from . import domain
from .store import Store

MODEL = 'black-forest-labs/FLUX.2-klein-9B'
REVISION = '92196c8e11f7b6cf2b7493e037d8c5345c559216'
router = APIRouter(prefix='/api/pedal', tags=['pedal-event'])
tasks = set()
active_jobs = {}


def token():
    return os.getenv('PEDAL_ACCESS_TOKEN') or os.getenv('BIKE_WORKER_TOKEN', '')


def authorize(authorization: str = Header(default='')):
    expected = token()
    if len(expected) < 32:
        raise HTTPException(503, 'BIKE_WORKER_TOKEN_REQUIRED')
    if not hmac.compare_digest(authorization.encode(), ('Bearer '+expected).encode()):
        raise HTTPException(403, 'BOOTH_ACCESS_DENIED')


@lru_cache(maxsize=1)
def store():
    return Store()


def change(fn):
    try:
        return store().mutate(fn)
    except domain.Conflict as exc:
        raise HTTPException(409, str(exc)) from None
    except (RuntimeError, httpx.HTTPError):
        raise HTTPException(503, 'EVENT_DB_UNAVAILABLE_APPLY_SQL') from None


def gpu_url():
    pod = store().read().get('gpu_pod_id')
    url = ('https://'+pod+'-8000.proxy.runpod.net') if pod else (
        os.getenv('PEDAL_GPU_API_URL') or os.getenv('GPU_API_URL', ''))
    if not re.fullmatch(r'https://[A-Za-z0-9-]+-8000\.proxy\.runpod\.net', url):
        raise RuntimeError('SET_EVENT_POD_IN_SETTINGS')
    return url


def gpu_headers():
    key = os.getenv('GPU_5090_API_KEY') or os.getenv('GPU_API_KEY', '')
    if len(key) < 32:
        raise RuntimeError('GPU_5090_API_KEY_REQUIRED')
    return {'Authorization': 'Bearer '+key}


async def gpu_health(url=None):
    async with httpx.AsyncClient(timeout=12, follow_redirects=False) as client:
        response = await client.get((url or gpu_url())+'/health', headers=gpu_headers())
        response.raise_for_status()
        info = response.json()
    if not (info.get('ok') and info.get('image_model') == MODEL and info.get('image_revision') == REVISION
            and info.get('quantization') == 'nf4' and info.get('gpu_count') == 1
            and 'RTX 5090' in info.get('gpu_name', '')):
        raise RuntimeError('EXPECTED_KLEIN9B_NF4_ONE_5090')
    return info


def finish_result(sid, result):
    if result.get('request_id') != sid or result.get('status') != 'succeeded':
        raise RuntimeError(result.get('error', 'GENERATION_NOT_SUCCEEDED'))
    energy = result.get('energy', {})
    joules = energy.get('energy_j')
    if not (energy.get('status') == 'measured' and isinstance(joules, (int, float))
            and not isinstance(joules, bool) and math.isfinite(joules) and joules > 0
            and energy.get('model_id') == MODEL and energy.get('model_revision') == REVISION
            and energy.get('scope') == 'gpu_board_generation_window'):
        raise RuntimeError('VALID_ZEUS_ENERGY_REQUIRED')
    raw = base64.b64decode(result['image_base64'], validate=True)
    if len(raw) > 8_000_000 or hashlib.sha256(raw).hexdigest() != result.get('image_sha256'):
        raise RuntimeError('IMAGE_HASH_MISMATCH')
    from PIL import Image
    with Image.open(io.BytesIO(raw)) as image:
        if image.format != 'PNG' or image.size != (1024, 1024):
            raise RuntimeError('EXPECTED_1024_PNG')
        image.verify()
    store().image(sid, result['image_base64'])
    def apply(state):
        row = state['sessions'][sid]
        if row['status'] not in ('generating', 'unknown'):
            return
        row.update(status='ready', target_wh=joules/3600, energy=energy,
                   image_sha256=result['image_sha256'], error=None)
    store().mutate(apply)


async def generate(sid, prompt, recover=False):
    sent = recover
    try:
        info = await gpu_health()
        if info.get('busy') and not recover:
            raise RuntimeError('GPU_BUSY')
        async with httpx.AsyncClient(timeout=15, follow_redirects=False) as client:
            if not recover:
                # Once only. An unconfirmed POST is never automatically resent.
                sent = True
                response = await client.post(gpu_url()+'/generate', headers=gpu_headers(),
                                             json={'request_id': sid, 'prompt': prompt})
                response.raise_for_status()
            for _ in range(240):
                response = await client.get(gpu_url()+'/requests/'+sid, headers=gpu_headers())
                response.raise_for_status()
                result = response.json()
                if result['status'] == 'succeeded':
                    await asyncio.to_thread(finish_result, sid, result)
                    return
                if result['status'] in ('failed', 'unknown'):
                    raise RuntimeError('GPU_'+result['status'].upper())
                await asyncio.sleep(1)
            raise RuntimeError('GPU_RESULT_UNCONFIRMED')
    except Exception:
        def fail(state):
            row = state['sessions'][sid]
            if row['status'] in ('generating', 'unknown'):
                row.update(status='unknown' if sent else 'failed',
                           error='GPU_RESULT_UNCONFIRMED' if sent else 'GPU_NOT_READY_CHECK_SETTINGS')
        await asyncio.to_thread(store().mutate, fail)


def launch(sid, prompt, recover=False):
    if sid in active_jobs and not active_jobs[sid].done():
        return
    task = asyncio.create_task(generate(sid, prompt, recover))
    active_jobs[sid] = task
    tasks.add(task)
    def finished(job):
        tasks.discard(job)
        if active_jobs.get(sid) is job:
            active_jobs.pop(sid, None)
    task.add_done_callback(finished)


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)


class Generation(Strict):
    request_id: UUID
    prompt: str = Field(min_length=1, max_length=2000)


class Pod(Strict):
    pod_id: str = Field(pattern=r'^[A-Za-z0-9]{5,40}$')


class Control(Strict):
    action: str = Field(pattern=r'^(scan|connect|disconnect)$')
    device_id: str = Field(default='', max_length=100)


class Ack(Strict):
    error: str = Field(default='', max_length=200)


class Measurement(Strict):
    id: UUID
    raw_wh: float = Field(ge=0, lt=1000)
    elapsed_s: float = Field(ge=0, le=604800)
    measured_s: float = Field(ge=0, le=604800)
    gap_count: int = Field(ge=0)
    status: str = Field(pattern=r'^(running|completed|interrupted)$')
    end_reason: str = Field(default='', max_length=100)


class Telemetry(Strict):
    boot_id: UUID
    seq: int = Field(ge=0)
    connected: bool
    fresh: bool
    message: str = Field(max_length=500)
    devices: list[dict] = Field(default_factory=list, max_length=20)
    voltage_v: float = Field(ge=0, le=6)
    current_a: float = Field(ge=0, le=1)
    power_w: float = Field(ge=0, le=6)
    measurement: Measurement | None = None


@router.get('/health')
def health():
    return {'ok': True, 'image_model': MODEL, 'quantization': 'nf4', 'gpu_count_required': 1,
            'station_configured': len(token()) >= 32, 'filter': 'pixel-blur-opacity', 'multiplier': 100}


@router.get('/state', dependencies=[Depends(authorize)])
def state():
    try:
        return domain.public(store().read())
    except Exception:
        raise HTTPException(503, 'EVENT_DB_UNAVAILABLE_APPLY_SQL') from None


@router.post('/generate', dependencies=[Depends(authorize)])
async def create(body: Generation):
    prompt = body.prompt.strip()
    if not prompt:
        raise HTTPException(422, 'PROMPT_REQUIRED')
    sid = str(body.request_id)
    created = change(lambda s: domain.create(s, sid, prompt))
    if created:
        launch(sid, prompt)
    return {'session_id': sid, 'created': created}


@router.post('/sessions/{sid}/recover', dependencies=[Depends(authorize)])
async def recover(sid: UUID):
    row = store().read()['sessions'].get(str(sid))
    if not row or row['status'] not in ('unknown', 'generating'):
        raise HTTPException(409, 'NO_PENDING_GPU_RESULT')
    launch(str(sid), row['prompt'], True)
    return {'recovering': True}


@router.post('/sessions/{sid}/start', dependencies=[Depends(authorize)])
def start(sid: UUID):
    return change(lambda s: domain.start(s, str(sid)))


@router.post('/sessions/{sid}/stop', dependencies=[Depends(authorize)])
def stop(sid: UUID):
    return change(lambda s: domain.stop(s, str(sid)))


@router.post('/reset', dependencies=[Depends(authorize)])
def reset():
    def apply(s):
        row = domain.active(s)
        if row and row['status'] in ('generating', 'measuring', 'stopping'):
            raise domain.Conflict('STOP_CURRENT_SESSION_FIRST')
        if row and row['status'] == 'ready':
            row['status'] = 'ended'; row['end_reason'] = 'reset_before_measurement'
        s['active'] = None
    change(apply)
    return {'reset': True}


@router.get('/sessions/{sid}/image', dependencies=[Depends(authorize)])
def image(sid: UUID):
    data = store().image(str(sid))
    if not data:
        raise HTTPException(404, 'IMAGE_NOT_READY')
    return Response(base64.b64decode(data), media_type='image/png', headers={'Cache-Control': 'private, no-store'})


@router.get('/gpu', dependencies=[Depends(authorize)])
async def gpu():
    try:
        return await gpu_health()
    except Exception:
        raise HTTPException(503, 'EXPECTED_READY_KLEIN9B_NF4_ONE_5090') from None


@router.post('/gpu/register', dependencies=[Depends(authorize)])
async def register(body: Pod):
    url = 'https://'+body.pod_id+'-8000.proxy.runpod.net'
    try:
        await gpu_health(url)
    except Exception:
        raise HTTPException(503, 'GPU_IDENTITY_OR_AUTH_FAILED') from None
    def apply(s):
        row = domain.active(s)
        if row and row['status'] in ('generating', 'measuring', 'stopping'):
            raise domain.Conflict('FINISH_SESSION_BEFORE_GPU_CHANGE')
        s['gpu_pod_id'] = body.pod_id
    change(apply)
    return {'registered': True, 'pod_id': body.pod_id}


@router.post('/station/control', dependencies=[Depends(authorize)])
def control(body: Control):
    def apply(s):
        row = domain.active(s)
        if row and row['status'] in ('measuring', 'stopping'):
            raise domain.Conflict('STOP_BEFORE_USB_CHANGE')
        return domain.command(s, body.action, device_id=body.device_id)
    return change(apply)


@router.get('/station/commands', dependencies=[Depends(authorize)])
def commands():
    return {'commands': store().read()['commands']}


@router.post('/station/commands/{cid}/ack', dependencies=[Depends(authorize)])
def ack(cid: UUID, body: Ack):
    def apply(s):
        cmd = next((c for c in s['commands'] if c['id'] == str(cid)), None)
        row = domain.active(s)
        if body.error and cmd and cmd['action'] == 'start' and row and row['id'] == cmd.get('session_id') and row['status'] in ('measuring', 'stopping'):
            row.update(status='ready', error='USB_START_FAILED_RETRY_AFTER_CONNECTION')
        s['commands'] = [c for c in s['commands'] if c['id'] != str(cid)]
    change(apply)
    return {'acked': True}


@router.post('/station/telemetry', dependencies=[Depends(authorize)])
def receive(body: Telemetry):
    change(lambda s: domain.telemetry(s, body.model_dump(mode='json')))
    return {'accepted': True}
