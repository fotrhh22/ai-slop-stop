"""Single-booth state transitions. Energy authority stays on the server."""
import time
from uuid import uuid4


class Conflict(ValueError):
    pass


def active(state):
    return state['sessions'].get(state.get('active'))


def command(state, action, **values):
    item = {'id': str(uuid4()), 'action': action, 'created_at': time.time(), **values}
    state['commands'].append(item)
    state['commands'] = state['commands'][-30:]
    return item


def create(state, sid, prompt):
    if sid in state['sessions']:
        if state['sessions'][sid]['prompt'] != prompt:
            raise Conflict('REQUEST_ID_CONFLICT')
        return False
    old = active(state)
    if old and old['status'] in ('generating', 'ready', 'measuring', 'stopping'):
        raise Conflict('FINISH_OR_RESET_CURRENT_SESSION')
    session = {'id': sid, 'prompt': prompt, 'status': 'generating', 'created_at': time.time(),
        'target_wh': None, 'raw_wh': 0.0, 'campaign_wh': 0.0, 'progress': 0.0,
        'elapsed_s': 0.0, 'measured_s': 0.0, 'gap_count': 0, 'multiplier': 100,
        'error': None, 'end_reason': None}
    state['sessions'][sid] = session
    state['active'] = sid
    # Metadata retention only. Images remain available until explicit SQL cleanup.
    if len(state['sessions']) > 100:
        oldest = sorted(state['sessions'], key=lambda k: state['sessions'][k]['created_at'])
        for key in oldest[:len(oldest)-100]:
            if key != sid:
                del state['sessions'][key]
    return True


def start(state, sid):
    row = active(state)
    if not row or row['id'] != sid:
        raise Conflict('SESSION_NOT_ACTIVE')
    if row['status'] == 'measuring':
        return row
    if row['status'] != 'ready' or not row.get('target_wh'):
        raise Conflict('IMAGE_AND_MEASURED_TARGET_REQUIRED')
    station = state.get('station', {})
    if not station.get('fresh') or not station.get('connected') or time.time()-station.get('received_at', 0) > 4:
        raise Conflict('FRESH_USB_DATA_REQUIRED')
    row['status'] = 'measuring'
    row['measurement_boot'] = station['boot_id']
    command(state, 'start', session_id=sid, target_wh=row['target_wh'])
    return row


def stop(state, sid):
    row = active(state)
    if not row or row['id'] != sid or row['status'] not in ('measuring', 'stopping'):
        raise Conflict('NO_RUNNING_MEASUREMENT')
    if row['status'] == 'measuring':
        command(state, 'stop', session_id=sid)
        row['status'] = 'stopping'
    return row


def telemetry(state, message):
    old = state.get('station', {})
    if old.get('boot_id') == message['boot_id'] and message['seq'] <= old.get('seq', -1):
        return
    station = {k: v for k, v in message.items() if k != 'measurement'}
    station['received_at'] = time.time()
    state['station'] = station
    row = active(state)
    measured = message.get('measurement')
    if not row or row['status'] not in ('measuring', 'stopping'):
        return
    if row.get('measurement_boot') != message['boot_id']:
        row.update(status='interrupted', end_reason='bridge_restarted', error='MEASUREMENT_INTERRUPTED')
        return
    if not measured or measured['id'] != row['id']:
        return
    raw, elapsed, duration = measured['raw_wh'], measured['elapsed_s'], measured['measured_s']
    if raw < row['raw_wh'] or elapsed < row['elapsed_s'] or duration > elapsed + .01 or raw > 6*duration/3600 + 1e-8:
        raise Conflict('MEASUREMENT_INCONSISTENT')
    row.update(raw_wh=raw, campaign_wh=raw*row['multiplier'], elapsed_s=elapsed,
               measured_s=duration, gap_count=measured['gap_count'])
    row['progress'] = min(1., row['campaign_wh']/row['target_wh'])
    if row['progress'] >= 1:
        row.update(status='completed', end_reason='target_reached')
        command(state, 'stop', session_id=row['id'])
    elif measured['status'] != 'running':
        row.update(status='interrupted' if measured['status'] == 'interrupted' else 'ended',
                   end_reason=measured.get('end_reason') or 'manual_stop')


def public(state):
    row = active(state)
    station = dict(state.get('station', {}))
    station['online'] = time.time()-station.get('received_at', 0) <= 4
    if not station['online']:
        station['fresh'] = False
    return {'session': row, 'station': station, 'multiplier': 100,
            'gpu_pod_id': state.get('gpu_pod_id', ''),
            'history': [{'id': r['id'], 'status': r['status'], 'created_at': r['created_at']}
                        for r in sorted(state['sessions'].values(), key=lambda r:r['created_at'], reverse=True)[:8]]}
