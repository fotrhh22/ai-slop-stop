"""Station-scoped outbound Mac integration. No Supabase credential reaches the Mac."""
import hmac
from datetime import datetime
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from .config import load_settings
from .persistence import Store, PersistenceError

router = APIRouter(prefix='/api/campaign')


def station_store(token):
    settings = load_settings()
    expected = settings.bike_worker_token
    if len(expected) < 32:
        raise HTTPException(503, 'BIKE_NOT_CONFIGURED')
    if not token or not hmac.compare_digest(token.encode(), expected.encode()):
        raise HTTPException(403, 'BIKE_AUTH_DENIED')
    return Store(settings), settings.print_station_id


@router.get('/entries')
def entries(x_bike_worker_token: str = Header(default='')):
    store, station = station_store(x_bike_worker_token)
    fields='id,receipt_id,participant_code,energy_basis,target_wh,multiplier,profile_snapshot,state,bike_session_id,created_at'
    filters={'station_id':'eq.'+station,'state':'in.(waiting,measuring)','order':'created_at.desc,id.desc','limit':'200'}
    try:
        rows=store.rows('campaign_entries',**filters,select=fields+',reference_target_wh,reference_target_source')
    except PersistenceError as exc:
        # Keep the queue readable while the new migration is being applied.
        if str(exc)!='SUPABASE_SCHEMA_UPDATE_REQUIRED':raise
        rows=store.rows('campaign_entries',**filters,select=fields)
    return {'entries': rows, 'station_id': station}


class Claim(BaseModel):
    session_id: UUID


@router.post('/entries/{entry_id}/delete')
def delete_entry(entry_id: UUID, background_tasks: BackgroundTasks, x_bike_worker_token: str = Header(default='')):
    store, station = station_store(x_bike_worker_token)
    deleted = store.request('POST','/rest/v1/rpc/delete_waiting_participant',json={
        'p_entry_id':str(entry_id),'p_station_id':station}).json()
    if deleted is True:
        from .cleanup import drain_storage_jobs
        background_tasks.add_task(drain_storage_jobs,store)
    return {'deleted':bool(deleted)}


@router.post('/entries/{entry_id}/claim')
def claim(entry_id: UUID, body: Claim, x_bike_worker_token: str = Header(default='')):
    store, station = station_store(x_bike_worker_token)
    rows = store.request('POST', '/rest/v1/rpc/claim_campaign_entry', json={
        'p_entry_id': str(entry_id), 'p_station_id': station, 'p_session_id': str(body.session_id)}).json()
    if not rows:
        raise HTTPException(409, 'CAMPAIGN_ENTRY_NOT_FOUND')
    return {'entry': rows[0]}


class BikeEnd(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    session_id: UUID
    entry_id: UUID
    raw_wh: float = Field(ge=0, lt=1000000)
    elapsed_s: float = Field(ge=0, le=604800)
    measured_s: float = Field(ge=0, le=604800)
    gap_count: int = Field(ge=0)
    mode: Literal['usb', 'ble']
    end_reason: Literal['target_reached', 'manual_stop', 'interrupted']
    started_at: datetime
    ended_at: datetime


@router.post('/bike-ends')
def ended(body: BikeEnd, x_bike_worker_token: str = Header(default='')):
    store, station = station_store(x_bike_worker_token)
    params = {'p_' + key: value for key, value in body.model_dump(mode='json').items()}
    params['p_station_id'] = station
    rows = store.request('POST', '/rest/v1/rpc/record_bike_end', json=params).json()
    if not rows:
        raise HTTPException(409, 'BIKE_RESULT_INVALID')
    result = {k: v for k, v in rows[0].items() if k != 'raw_energy_wh'}
    return {'result': result, 'print_queued': True}
