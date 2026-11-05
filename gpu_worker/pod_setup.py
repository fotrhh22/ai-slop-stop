"""SSH-driven provisioning helper. Never prints credentials or performs generation retries."""
from __future__ import annotations

import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import socket
import sqlite3
import subprocess
import sys
import tarfile
import time
import urllib.request

ROOT = Path('/workspace/ai-slop-stop')
WORKSPACE = ROOT.parent
STATE = ROOT / '.setup-status.json'


def state(stage):
    STATE.write_text(json.dumps({'stage': stage, 'time': time.time()}))


def inspect():
    pod = os.environ.get('RUNPOD_POD_ID', '')
    if not pod:
        for path in [Path('/etc/rp_environment'), Path('/etc/environment')]:
            if path.exists():
                match = re.search(r'RUNPOD_POD_ID=[\x22\x27]?([a-zA-Z0-9_-]+)', path.read_text())
                if match:
                    pod = match.group(1)
                    break
    result = {'pod_id': pod, 'healthy': False, 'port_open': False,
              'has_config': (ROOT / 'worker.env').exists(),
              'has_ledger': (WORKSPACE / 'ai-slop/requests.sqlite3').exists()}
    result['setup_running'] = False
    pid_file = ROOT / '.setup.pid'
    if pid_file.exists():
        pid = pid_file.read_text().strip()
        process = Path('/proc') / pid / 'cmdline'
        try:
            result['setup_running'] = pid.isdigit() and b'gpu_worker.pod_setup' in process.read_bytes()
        except OSError:
            pass
    if STATE.exists():
        result.update(json.loads(STATE.read_text()))
    try:
        with socket.create_connection(('127.0.0.1', 8000), timeout=2):
            result['port_open'] = True
    except OSError:
        pass
    if result['has_config'] and result['port_open']:
        values = dict(s.split('=', 1) for s in (ROOT / 'worker.env').read_text().splitlines() if '=' in s)
        request = urllib.request.Request('http://127.0.0.1:8000/health',
            headers={'Authorization': 'Bearer ' + values.get('GPU_API_KEY', '')})
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                health = json.load(response)
            result['healthy'] = bool(health.get('ok') and health.get('router_ready'))
            result['health'] = health
        except Exception as exc:
            result['health_error'] = type(exc).__name__
    return result


def restore(archive_path):
    """Restore only missing configuration/ledger; never replace an existing ledger."""
    restored, kept = [], []
    with tarfile.open(archive_path) as archive:
        selected = []
        for member, destination in [('ai-slop-stop/worker.env', ROOT / 'worker.env'),
                                    ('ai-slop/requests.sqlite3', WORKSPACE / 'ai-slop/requests.sqlite3')]:
            try:
                info = archive.getmember(member)
            except KeyError:
                continue
            if not info.isfile():
                raise RuntimeError('Backup configuration/ledger must be regular files.')
            if destination.exists():
                kept.append(member)
            else:
                selected.append((member, destination, archive.extractfile(info).read()))
        for member, destination, content in selected:
            destination.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(fd, 'wb') as stream:
                stream.write(content)
            if member.endswith('.sqlite3'):
                with sqlite3.connect(destination) as db:
                    if db.execute('pragma integrity_check').fetchone()[0] != 'ok':
                        destination.unlink()
                        raise RuntimeError('Restored ledger failed integrity check.')
            restored.append(member)
    return {'restored': restored, 'preserved_existing': kept}


def provision():
    os.chdir(ROOT)
    with (ROOT / '.setup.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('Another setup is already running.')
        try:
            if inspect()['port_open']:
                raise RuntimeError('Port 8000 is already in use; no worker was restarted.')
            from gpu_worker import volume
            state('checking_runtime')
            # Exit the probe process before loading models, freeing its CUDA context.
            probe = json.loads(subprocess.check_output([sys.executable, '-c',
                'import json,torch; from gpu_worker.volume import runtime_identity; '
                'print(json.dumps({"identity":runtime_identity(),'
                '"memory":torch.cuda.get_device_properties(0).total_memory}))'], text=True))
            identity, memory = probe['identity'], probe['memory']
            if not any(name in identity['gpu'] for name in ['RTX 4090', 'RTX 5090']):
                raise RuntimeError('This setup is validated only for RTX 4090/5090.')
            values = volume.configuration()
            values['GPU_IMAGE_DEVICE'] = '0'
            values['GPU_ROUTER_DEVICE'] = '1' if identity['gpu_count'] == 2 else '0'
            values['GPU_ROUTER_QUANTIZATION'] = 'int8' if memory < 28 * 1024**3 else 'bf16'
            env = ROOT / 'worker.env'
            fd = os.open(env, os.O_WRONLY | os.O_TRUNC)
            with os.fdopen(fd, 'w') as stream:
                stream.write(''.join(f'{k}={v}\n' for k, v in values.items()))
            env.chmod(0o600)
            state('installing')
            subprocess.run([sys.executable, '-m', 'gpu_worker.ephemeral', 'install', '--accept-ephemeral'], check=True)
            state('loading_models')
            with (ROOT / 'worker.log').open('ab') as log:
                worker = subprocess.Popen([sys.executable, '-m', 'gpu_worker.ephemeral', 'start', '--accept-ephemeral'],
                    stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
            (ROOT / 'worker.pid').write_text(str(worker.pid))
            while worker.poll() is None:
                if inspect()['healthy']:
                    state('ready')
                    return
                time.sleep(3)
            raise RuntimeError('Worker exited; inspect worker.log.')
        except Exception:
            state('failed')
            raise


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['inspect', 'restore', 'launch', 'provision'])
    parser.add_argument('--archive')
    args = parser.parse_args()
    if args.command == 'inspect':
        print(json.dumps(inspect()))
    elif args.command == 'restore':
        print(json.dumps(restore(args.archive)))
    elif args.command == 'launch':
        ROOT.mkdir(parents=True, exist_ok=True)
        state('starting')
        with (ROOT / 'setup.log').open('ab') as log:
            process = subprocess.Popen([sys.executable, '-m', 'gpu_worker.pod_setup', 'provision'], cwd=ROOT,
                stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
        (ROOT / '.setup.pid').write_text(str(process.pid))
        print(json.dumps({'setup_pid': process.pid}))
    else:
        provision()


if __name__ == '__main__':
    main()
