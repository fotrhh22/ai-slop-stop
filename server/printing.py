"""Opt-in booth printing. Clients never receive the worker secret or service-role key."""
from __future__ import annotations
import base64
import hmac
from typing import Optional
from uuid import UUID
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field
from .config import load_settings
from .persistence import Store, PersistenceError, digest

router = APIRouter(prefix='/api/printing')

def station_for_token(token: str, *, worker=False):
    settings = load_settings()
    expected = settings.print_worker_token if worker else settings.print_pairing_token
    if not expected or len(expected) < 24:
        raise HTTPException(503, 'PRINTING_NOT_CONFIGURED')
    if not token or not hmac.compare_digest(token.encode(), expected.encode()):
        raise HTTPException(403, 'PRINT_AUTH_DENIED')
    return settings.print_station_id

def booth_station(token: str):
    return station_for_token(token) if token else None

def worker_store(token):
    station = station_for_token(token, worker=True)
    return Store(load_settings()), station

def job_for_lease(store, station, job_id, lease):
    rows = store.rows('print_jobs', id='eq.'+str(job_id), station_id='eq.'+station,
                      lease_token='eq.'+str(lease))
    if not rows: raise HTTPException(409, 'PRINT_LEASE_LOST')
    return rows[0]

@router.post('/pair')
def pair(x_print_station_token: str = Header(default='')):
    station = station_for_token(x_print_station_token)
    # Fail early if SQL has not yet been applied; do not enable a broken booth mode.
    store = Store(load_settings())
    store.rows('print_jobs', select='id', limit='1')
    store.rows('challenges', select='print_station_id', limit='1')
    return {'station_id': station}

@router.post('/claim')
def claim(x_print_worker_token: str = Header(default='')):
    store, station = worker_store(x_print_worker_token)
    rows = store.request('POST','/rest/v1/rpc/claim_print_job',json={'p_station_id':station}).json()
    return {'job': rows[0] if rows else None}

class Lease(BaseModel):
    lease_token: UUID

@router.post('/jobs/{job_id}/payload')
def payload(job_id: UUID, req: Lease, x_print_worker_token: str = Header(default='')):
    store, station = worker_store(x_print_worker_token)
    job = job_for_lease(store,station,job_id,req.lease_token)
    if job['status'] != 'RENDERING': raise HTTPException(409,'PRINT_JOB_NOT_RENDERING')
    receipt = store.receipt(job['challenge_id'])
    if not receipt or receipt['id'] != job['receipt_id'] or not receipt['verified']:
        raise HTTPException(409,'PRINT_RECEIPT_NOT_VERIFIED')
    saved = store.rows('successes',id='eq.'+job['receipt_id'])[0]
    raw = store.download('challenge-successes',saved['image_path'])
    attempt = store.attempt(job['challenge_id'],receipt['attempt_id'])
    if digest(raw) != attempt.get('generated_image_sha256'):
        raise HTTPException(409,'SUCCESS_IMAGE_MISMATCH')
    return {'receipt':receipt,'image_base64':base64.b64encode(raw).decode()}

class Advance(Lease):
    status: str = Field(pattern='^(SENDING|SENT|FAILED|UNKNOWN)$')
    cups_job_id: Optional[str] = Field(default=None,max_length=160)
    error_code: Optional[str] = Field(default=None,max_length=100)

@router.post('/jobs/{job_id}/advance')
def advance(job_id: UUID,req: Advance,x_print_worker_token: str=Header(default='')):
    store,station=worker_store(x_print_worker_token)
    if req.status=='SENT' and not req.cups_job_id:
        raise HTTPException(400,'CUPS_JOB_ID_REQUIRED')
    rows=store.request('POST','/rest/v1/rpc/advance_print_job',json={
        'p_id':str(job_id),'p_station_id':station,'p_lease_token':str(req.lease_token),
        'p_status':req.status,'p_cups_job_id':req.cups_job_id,'p_error_code':req.error_code}).json()
    if not rows: raise HTTPException(409,'PRINT_TRANSITION_REJECTED')
    return rows[0]

class Reprint(BaseModel):
    request_id: UUID

@router.post('/jobs/{job_id}/reprint')
def reprint(job_id: UUID,req: Reprint,x_print_worker_token: str=Header(default='')):
    store,station=worker_store(x_print_worker_token)
    rows=store.rows('print_jobs',id='eq.'+str(job_id),station_id='eq.'+station)
    if not rows: raise HTTPException(404,'PRINT_JOB_NOT_FOUND')
    job=rows[0]
    if job['status'] not in ('SENT','FAILED','UNKNOWN'):
        raise HTTPException(409,'PRINT_JOB_BUSY')
    data={'receipt_id':job['receipt_id'],'challenge_id':job['challenge_id'],
          'station_id':station,'request_id':str(req.request_id)}
    try: return store.insert('print_jobs',data)
    except PersistenceError as exc:
        if str(exc)!='RECORD_ALREADY_EXISTS': raise
        existing=store.rows('print_jobs',request_id='eq.'+str(req.request_id),station_id='eq.'+station)
        if not existing or existing[0]['receipt_id']!=job['receipt_id']: raise
        return existing[0]

@router.get('/jobs')
def list_jobs(x_print_worker_token: str=Header(default='')):
    store,station=worker_store(x_print_worker_token)
    rows=store.rows('print_jobs',station_id='eq.'+station,order='created_at.desc',limit='20',
                    select='id,status,cups_job_id,error_code,created_at')
    return {'jobs':rows}
