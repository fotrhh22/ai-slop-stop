"""Delete abandoned sessions, then drain a durable Storage deletion outbox."""
import asyncio
from collections import defaultdict
from contextlib import suppress
import logging
from uuid import UUID

from .persistence import Store

log = logging.getLogger('uvicorn.error')


def clean_once(settings):
    store = Store(settings)
    removed = store.request('POST','/rest/v1/rpc/expire_abandoned_challenges',
        json={'p_retention_hours':settings.unpassed_retention_hours,'p_limit':100}).json()
    deleted = drain_storage_jobs(store)
    if removed or deleted:
        log.info('Session cleanup: sessions=%s storage_jobs=%s',removed,deleted)
    return {'sessions_deleted':removed,'storage_jobs_deleted':deleted}


def drain_storage_jobs(store):
    jobs = store.rows('storage_cleanup_jobs',order='created_at.asc,id.asc',limit='200')
    buckets = defaultdict(list)
    for job in jobs:
        # Restrict deletion to known per-session objects, never arbitrary prefixes.
        try:
            UUID(job['id'])
            cid, name = job['object_path'].split('/')
            UUID(cid)
            if name != 'target': UUID(name)
            if job['bucket'] not in ('challenge-targets','challenge-successes'): raise ValueError('bucket')
        except (ValueError, KeyError, TypeError):
            log.warning('Session cleanup: invalid Storage deletion job retained')
            continue
        buckets[job['bucket']].append(job)
    deleted = 0
    for bucket, batch in buckets.items():
        try:
            store.request('DELETE','/storage/v1/object/'+bucket,
                json={'prefixes':[job['object_path'] for job in batch]})
            # If this acknowledgement fails, retrying the same Storage delete is safe.
            store.request('DELETE','/rest/v1/storage_cleanup_jobs',
                params={'id':'in.('+','.join(job['id'] for job in batch)+')'})
            deleted += len(batch)
        except Exception:
            log.warning('Session cleanup: Storage deletion pending; retry on next sweep')
    return deleted


async def run(settings):
    while True:
        try:
            await asyncio.to_thread(clean_once,settings)
        except Exception:
            log.warning('Session cleanup unavailable; check migration/connectivity; retry on next sweep')
        await asyncio.sleep(settings.cleanup_interval_seconds)


async def stop(task):
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task
