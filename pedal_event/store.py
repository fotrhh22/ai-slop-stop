"""Persistent event state. Reuses Supabase service credentials; SQLite for local development."""
import copy
import json
import os
import sqlite3
import threading
from pathlib import Path

import httpx


def initial():
    return {'active': None, 'sessions': {}, 'station': {}, 'commands': [], 'gpu_pod_id': ''}


class Store:
    def __init__(self, path=None):
        self.url = '' if path else os.getenv('SUPABASE_URL', '').rstrip('/')
        self.key = os.getenv('SUPABASE_SECRET_KEY', '')
        self.lock = threading.RLock()
        if self.url:
            if not self.key:
                raise RuntimeError('SUPABASE_SECRET_KEY_REQUIRED')
            self.client = httpx.Client(base_url=self.url + '/rest/v1', timeout=12,
                headers={'apikey': self.key, 'Authorization': 'Bearer ' + self.key})
        else:
            if os.getenv('RENDER'):
                raise RuntimeError('RENDER_REQUIRES_SUPABASE_PERSISTENCE')
            path = path or os.getenv('PEDAL_DB_PATH', 'data/pedal.sqlite3')
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            self.db = sqlite3.connect(str(path), check_same_thread=False)
            self.db.execute('PRAGMA journal_mode=WAL')
            self.db.executescript('CREATE TABLE IF NOT EXISTS event_state(id INTEGER PRIMARY KEY,payload TEXT);'
                                 'CREATE TABLE IF NOT EXISTS images(id TEXT PRIMARY KEY,png TEXT);')
            self.db.execute('INSERT OR IGNORE INTO event_state VALUES(1,?)', (json.dumps(initial()),))
            self.db.commit()

    def request(self, method, path, **kwargs):
        result = self.client.request(method, path, **kwargs)
        if result.status_code >= 400:
            raise RuntimeError('PEDAL_DB_SCHEMA_OR_REQUEST_ERROR')
        return result

    def read(self):
        with self.lock:
            if self.url:
                rows = self.request('GET', '/pedal_event_state', params={'id': 'eq.1', 'select': 'payload,revision'}).json()
                if len(rows) != 1:
                    raise RuntimeError('APPLY_PEDAL_MIGRATION')
                return rows[0]['payload']
            return json.loads(self.db.execute('SELECT payload FROM event_state WHERE id=1').fetchone()[0])

    def mutate(self, fn):
        with self.lock:
            if self.url:
                for _ in range(8):
                    rows = self.request('GET', '/pedal_event_state', params={'id': 'eq.1', 'select': 'payload,revision'}).json()
                    if not rows:
                        raise RuntimeError('APPLY_PEDAL_MIGRATION')
                    row = rows[0]
                    value = copy.deepcopy(row['payload'])
                    result = fn(value)
                    changed = self.request('PATCH', '/pedal_event_state',
                        params={'id': 'eq.1', 'revision': 'eq.' + str(row['revision'])},
                        headers={'Prefer': 'return=representation'},
                        json={'payload': value, 'revision': row['revision'] + 1}).json()
                    if changed:
                        return result
                raise RuntimeError('PEDAL_STATE_BUSY')
            try:
                self.db.execute('BEGIN IMMEDIATE')
                value = json.loads(self.db.execute('SELECT payload FROM event_state WHERE id=1').fetchone()[0])
                result = fn(value)
                self.db.execute('UPDATE event_state SET payload=? WHERE id=1', (json.dumps(value),))
                self.db.commit()
                return result
            except Exception:
                self.db.rollback()
                raise

    def image(self, sid, png=None):
        with self.lock:
            if self.url:
                if png is not None:
                    self.request('POST', '/pedal_event_images', headers={'Prefer': 'resolution=merge-duplicates'},
                                 json={'id': sid, 'png_base64': png})
                    return
                rows = self.request('GET', '/pedal_event_images', params={'id': 'eq.' + sid, 'select': 'png_base64'}).json()
                return rows[0]['png_base64'] if rows else None
            if png is not None:
                self.db.execute('INSERT OR REPLACE INTO images VALUES(?,?)', (sid, png)); self.db.commit()
                return
            row = self.db.execute('SELECT png FROM images WHERE id=?', (sid,)).fetchone()
            return row[0] if row else None
