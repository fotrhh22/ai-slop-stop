"""Public model catalog and authenticated operator runtime registration.

Only RunPod proxy URLs derived from a validated Pod ID are contacted. Credentials
stay in Render; immutable runtime snapshots prevent participant routing changes.
"""
from dataclasses import replace
import hmac
import re
from uuid import UUID

import httpx
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from .config import load_settings
from .persistence import Store

router = APIRouter(prefix='/api')
OPEN_IDS = ('open-4090', 'open-5090')


def endpoint(pod_id):
    if not re.fullmatch(r'[a-zA-Z0-9]{5,40}', pod_id):
        raise HTTPException(400, 'POD_ID_INVALID')
    return 'https://' + pod_id + '-8000.proxy.runpod.net'


def definition(settings, profile_id):
    if profile_id not in OPEN_IDS:
        raise HTTPException(400, 'PROFILE_INVALID')
    config = settings.gpu_profiles.get(profile_id, {})
    if len(config.get('key', '')) < 32:
        raise HTTPException(503, 'PROFILE_NOT_CONFIGURED')
    return config


def probe(settings, profile_id, pod_id):
    config = definition(settings, profile_id)
    try:
        response = httpx.get(endpoint(pod_id) + '/health',
            headers={'Authorization': 'Bearer ' + config['key']},
            timeout=httpx.Timeout(8, connect=4), follow_redirects=False)
        response.raise_for_status()
        health = response.json()
        if not isinstance(health, dict):
            raise ValueError('health response')
        if not (health.get('ok') is True and health.get('router_ready') is True and
                health.get('provider') == 'zeus' and
                health.get('image_model') == config['image_model'] and
                health.get('router_model') == config['router_model'] and
                'RTX ' + profile_id[-4:] in health.get('gpu_name', '') and
                re.fullmatch(r'GPU-[a-zA-Z0-9-]+', health.get('gpu_uuid', '')) and
                all(re.fullmatch(r'[a-f0-9]{40}', health.get(k, ''))
                    for k in ('image_revision', 'router_revision')) and
                health.get('router_quantization') == ('int8' if profile_id == 'open-4090' else 'bf16')):
            raise ValueError('identity')
        return {k: health[k] for k in ('image_model', 'router_model', 'image_revision',
            'router_revision', 'gpu_name', 'gpu_uuid', 'router_quantization')}
    except (httpx.HTTPError, ValueError, TypeError):
        raise HTTPException(503, 'GPU_NOT_READY_OR_IDENTITY_MISMATCH') from None


def runtime(store, settings, profile_id, runtime_id=None):
    if runtime_id is None:
        bindings = store.rows('gpu_profile_bindings', profile_id='eq.' + profile_id,
            station_id='eq.' + settings.print_station_id)
        if not bindings:
            raise HTTPException(503, 'PROFILE_NOT_READY')
        runtime_id = bindings[0]['runtime_id']
    rows = store.rows('gpu_runtimes', id='eq.' + str(runtime_id),
        profile_id='eq.' + profile_id, station_id='eq.' + settings.print_station_id)
    if not rows:
        raise HTTPException(503, 'PROFILE_RUNTIME_UNAVAILABLE')
    row = rows[0]
    health = probe(settings, profile_id, row['pod_id'])
    if health != row['identity']:
        raise HTTPException(503, 'PROFILE_RUNTIME_CHANGED')
    return row


def select(settings, store, profile_id, snapshot=None):
    if profile_id == 'gemini':
        if not settings.gemini_configured:
            raise HTTPException(503, 'GEMINI_NOT_CONFIGURED')
        return replace(settings, profile_id='gemini', image_provider='gemini', router_provider='gemini',
            image_model=(snapshot or {}).get('image_model', settings.gemini_image_model),
            router_model=(snapshot or {}).get('router_model', settings.gemini_router_model),
            gpu_api_url='', gpu_api_key='', gpu_identity={}, runtime_id='')
    row = runtime(store, settings, profile_id, (snapshot or {}).get('runtime_id'))
    identity = row['identity']
    if snapshot and snapshot.get('gpu_identity') != identity:
        raise HTTPException(503, 'PROFILE_RUNTIME_CHANGED')
    return replace(settings, profile_id=profile_id, image_provider='zeus', router_provider='zeus',
        image_model=identity['image_model'], router_model=identity['router_model'],
        gpu_api_url=endpoint(row['pod_id']), gpu_api_key=definition(settings, profile_id)['key'],
        gpu_identity=identity, runtime_id=row['id'])


def measurement_matches(settings, measurement, operation):
    identity = settings.gpu_identity
    if identity and (measurement.gpu_uuid != identity['gpu_uuid'] or
            measurement.model_revision != identity['image_revision' if operation == 'generate' else 'router_revision']):
        raise ValueError('GPU measurement runtime identity mismatch')


@router.get('/model-profiles')
def catalog():
    settings = load_settings()
    result = [{'id': 'gemini', 'label': 'Gemini', 'ready': settings.gemini_configured,
        'image_model': settings.gemini_image_model, 'router_model': settings.gemini_router_model,
        'energy_basis': 'tokens_only'}]
    for profile_id in OPEN_IDS:
        config = settings.gpu_profiles.get(profile_id, {})
        row = {'id': profile_id, 'label': '오픈모델 · ' + profile_id[-4:], 'ready': False,
            'image_model': config.get('image_model', 'black-forest-labs/FLUX.2-klein-4B'),
            'router_model': config.get('router_model', 'Qwen/Qwen3.5-4B'), 'energy_basis': 'measured_gpu'}
        try:
            definition(settings, profile_id)
            runtime(Store(settings), settings, profile_id)
            row['ready'] = True
        except Exception:
            # Missing migration, stopped Pod, auth error and unavailable GPU all fail closed.
            pass
        result.append(row)
    return {'default_profile': 'gemini', 'profiles': result}


def operator(settings, token):
    if len(settings.gpu_control_token) < 32:
        raise HTTPException(503, 'GPU_CONTROL_NOT_CONFIGURED')
    if not token or not hmac.compare_digest(token.encode(), settings.gpu_control_token.encode()):
        raise HTTPException(403, 'GPU_CONTROL_DENIED')


class Registration(BaseModel):
    runtime_id: UUID
    profile_id: str = Field(pattern=r'^open-(4090|5090)$')
    pod_id: str = Field(pattern=r'^[a-zA-Z0-9]{5,40}$')


@router.post('/gpu-control/register')
def register(body: Registration, x_gpu_control_token: str = Header(default='')):
    settings = load_settings()
    operator(settings, x_gpu_control_token)
    health = probe(settings, body.profile_id, body.pod_id)
    Store(settings).request('POST', '/rest/v1/rpc/register_gpu_runtime', json={
        'p_id': str(body.runtime_id), 'p_profile_id': body.profile_id, 'p_pod_id': body.pod_id,
        'p_station_id': settings.print_station_id, 'p_identity': health})
    return {'ready': True, 'profile_id': body.profile_id, 'runtime_id': str(body.runtime_id)}
