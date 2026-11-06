#!/usr/bin/env python3
"""Mac controller: ./runpod up --ssh 'ssh root@IP -p PORT' [--backup PRIVATE_ARCHIVE]."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import fcntl
import json
import os
from pathlib import Path
import re
import secrets
import shlex
import subprocess
import sys
import time
import urllib.request
from uuid import uuid4

REPO = Path(__file__).resolve().parents[1]
LOCAL = REPO / '.runpod'
PROFILE = LOCAL / 'connection.json'
REMOTE_ROOT = '/workspace/ai-slop-stop'


def save_profile(profile):
    LOCAL.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(PROFILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as stream:
        json.dump(profile, stream, indent=2)


def parse_connection(command):
    parts = shlex.split(command)
    if not parts or parts.pop(0) != 'ssh':
        raise ValueError('Paste a direct TCP command starting with ssh.')
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument('target')
    parser.add_argument('-p', type=int, default=22)
    parser.add_argument('-i')  # RunPod template key is ignored; our key is selected separately.
    args = parser.parse_args(parts)
    if not re.fullmatch(r'root@[a-zA-Z0-9.:-]+', args.target) or not 1 <= args.p <= 65535:
        raise ValueError('Use the SSH over exposed TCP command: ssh root@HOST -p PORT.')
    return {'target': args.target, 'port': args.p}


class Pod:
    def __init__(self, profile):
        self.profile = profile
        self.options = ['-o', 'BatchMode=yes', '-o', 'IdentitiesOnly=yes',
                        '-o', 'StrictHostKeyChecking=yes', '-o', 'ConnectTimeout=15',
                        '-i', str(Path(profile['identity']).expanduser())]
        if profile.get('known_hosts'):
            self.options += ['-o', 'UserKnownHostsFile=' + profile['known_hosts']]

    def run(self, command, data=None):
        process = subprocess.run(['ssh', *self.options, '-p', str(self.profile['port']),
                                  self.profile['target'], command], input=data,
                                 stdout=subprocess.PIPE)
        if process.returncode:
            raise RuntimeError(f'SSH command failed (exit {process.returncode}); see the SSH message above.')
        return process.stdout

    def helper(self, action, *args):
        source = (REPO / 'gpu_worker/pod_setup.py').read_text()
        command = shlex.join(['python', '-c', source, action, *args])
        return json.loads(self.run(command))

    def scp(self, source, destination, upload=True):
        remote = self.profile['target'] + ':' + destination
        subprocess.run(['scp', *self.options, '-P', str(self.profile['port']),
                        *([str(source), remote] if upload else [remote, str(source)])], check=True)


def backup(pod, profile):
    folder = LOCAL / 'backups'
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    name = 'ai-slop-rescue-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid4().hex[:8] + '.tar.gz'
    remote = '/tmp/' + name
    command = f'cd {REMOTE_ROOT} && python -m gpu_worker.volume backup --output {shlex.quote(remote)}'
    pod.run(command)
    expected = pod.run(shlex.join(['sha256sum', remote])).decode().split()[0]
    path = folder / name
    # Create privately before SCP so secrets never land in a world-readable file.
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(fd)
    pod.scp(path, remote, upload=False)
    path.chmod(0o600)
    if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
        raise RuntimeError('Backup checksum mismatch; remote backup retained.')
    path.with_suffix('.sha256').write_text(expected + '  ' + name + '\n')
    pod.run(shlex.join(['rm', '--', remote]))
    profile['backup'] = str(path)
    save_profile(profile)
    print('Private backup verified:', path, flush=True)
    return path


def api_key(pod):
    command = "python -c " + shlex.quote(
        "from pathlib import Path; p=Path('/workspace/ai-slop-stop/worker.env'); "
        "v=dict(s.split('=',1) for s in p.read_text().splitlines() if '=' in s); "
        "print(v['GPU_API_KEY'],end='')")
    value = pod.run(command).decode().strip()
    if len(value) < 32:
        raise RuntimeError('No valid API key; nothing was copied.')
    return value


def ensure_api_key(pod, key):
    if len(key) < 32:
        raise RuntimeError('Profile API key must have at least 32 characters.')
    # Secret travels over SSH stdin, never command arguments or stdout.
    source = '''import json,sys,hmac,os,socket
from pathlib import Path
from gpu_worker.volume import configuration
p=Path('/workspace/ai-slop-stop/worker.env')
existed=p.exists()
key=json.load(sys.stdin)['key']
values=configuration()
if existed and not hmac.compare_digest(values['GPU_API_KEY'],key):
    try:
        sock=socket.create_connection(('127.0.0.1',8000),timeout=1)
    except OSError:
        pass
    else:
        sock.close()
        raise RuntimeError('Cannot synchronize key while port 8000 is active')
values['GPU_API_KEY']=key
fd=os.open(p,os.O_WRONLY|os.O_TRUNC)
with os.fdopen(fd,'w') as stream: stream.write(''.join(k+'='+v+'\\n' for k,v in values.items()))
'''
    pod.run(shlex.join(['bash', '-lc', 'cd ' + REMOTE_ROOT + ' && python -c ' + shlex.quote(source)]),
            data=json.dumps({'key': key}).encode())


def public_health(pod, profile, status):
    pod_id = status.get('pod_id') or profile.get('pod_id')
    if not pod_id or not re.fullmatch(r'[a-zA-Z0-9_-]+', pod_id):
        raise RuntimeError('Pod ID missing; rerun with --pod-id from the Connect screen.')
    profile['pod_id'] = pod_id
    save_profile(profile)
    url = f'https://{pod_id}-8000.proxy.runpod.net'
    request = urllib.request.Request(url + '/health', headers={
        'Authorization': 'Bearer ' + api_key(pod), 'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(request, timeout=30) as response:
        health = json.load(response)
    from server.gpu_defaults import IMAGE_MODEL, IMAGE_REVISION
    if (not health.get('ok') or not health.get('router_ready') or
            health.get('image_model') != IMAGE_MODEL or health.get('image_revision') != IMAGE_REVISION or
            health.get('image_quantization') != 'nf4'):
        raise RuntimeError('Public GPU API is not ready.')
    print('HTTPS authentication: OK\nGPU_API_URL=' + url, flush=True)
    print('Existing API key preserved if restored. Use ./runpod copy-key to copy it privately.')


def up(pod, profile, args):
    status = pod.helper('inspect')
    if status['healthy']:
        from server.gpu_defaults import IMAGE_MODEL, IMAGE_REVISION
        health = status.get('health', {})
        if (health.get('image_model') != IMAGE_MODEL or health.get('image_revision') != IMAGE_REVISION
                or health.get('image_quantization') != 'nf4'):
            raise RuntimeError('Existing worker is not Klein 9B NF4. Stop it safely before migration; no restart performed.')
        if getattr(args, 'api_key', None) and not secrets.compare_digest(api_key(pod), args.api_key):
            raise RuntimeError('Running worker uses a different API key. Stop it safely before synchronizing credentials.')
        print('Worker already healthy; installation and running requests left untouched.', flush=True)
    else:
        if status['port_open']:
            raise RuntimeError('Port 8000 is occupied but health failed. Use ./runpod logs; no restart performed.')
        if not status.get('setup_running'):
            # Only committed runtime source; no local env, images, ledger, or SSH keys.
            source = subprocess.check_output(['git', 'archive', '--format=tar', 'HEAD',
                'gpu_worker', 'server/__init__.py', 'server/energy.py', 'server/verdict.py',
                'server/images.py', 'server/gpu_defaults.py', 'server/prompts/evaluate.md'], cwd=REPO)
            extract = "import sys,tarfile; from pathlib import Path; " \
                      "p=Path('/workspace/ai-slop-stop'); p.mkdir(parents=True,exist_ok=True); " \
                      "tarfile.open(fileobj=sys.stdin.buffer,mode='r|').extractall(p,filter='data')"
            pod.run(shlex.join(['python', '-c', extract]), data=source)
            selected = args.backup or profile.get('backup')
            if selected:
                path = Path(selected).expanduser().resolve()
                if not path.is_file():
                    raise RuntimeError('Selected backup does not exist; installation stopped.')
                private = '/tmp/ai-slop-import-' + uuid4().hex
                pod.run(shlex.join(['mkdir', '-m', '700', private]))
                remote = private + '/rescue.tar.gz'
                pod.scp(path, remote)
                try:
                    print(pod.helper('restore', '--archive', remote), flush=True)
                finally:
                    pod.run(shlex.join(['rm', '-f', '--', remote]))
                    pod.run(shlex.join(['rmdir', '--', private]))
            elif not status['has_config']:
                print('No configuration backup: using the profile key; request history starts fresh.'
                      if getattr(args, 'api_key', None) else
                      'No configuration backup: a new API key will be created. Update Render afterward.', flush=True)
            if getattr(args, 'api_key', None):
                ensure_api_key(pod, args.api_key)
            print(pod.helper('launch'), flush=True)
        else:
            print('Setup already in progress; waiting without launching a duplicate.', flush=True)
        deadline = time.monotonic() + args.timeout
        previous, last_report = None, 0
        while time.monotonic() < deadline:
            status = pod.helper('inspect')
            stage = status.get('stage', 'starting')
            if stage != previous or time.monotonic() - last_report >= 30:
                print('Stage:', stage, flush=True)
                previous, last_report = stage, time.monotonic()
            if status['healthy']:
                break
            if stage == 'failed':
                raise RuntimeError('Setup failed. Run ./runpod logs --setup, then ./runpod logs.')
            time.sleep(5)
        else:
            raise RuntimeError('Readiness wait expired. Remote setup may still run; inspect logs before retrying.')
    if getattr(args, 'api_key', None) and api_key(pod) != args.api_key:
        raise RuntimeError('Worker key does not match the selected profile; nothing was registered.')
    backup(pod, profile)
    public_health(pod, profile, status)


def main():
    global LOCAL, PROFILE
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['up', 'status', 'logs', 'backup', 'copy-key'])
    parser.add_argument('--ssh', help='Paste current SSH over exposed TCP command; -i is replaced by --identity.')
    parser.add_argument('--identity', help='Default: ~/.ssh/runpod_esg')
    parser.add_argument('--known-hosts', help='Optional existing known_hosts file; verification is never disabled.')
    parser.add_argument('--pod-id')
    parser.add_argument('--profile', choices=['open-4090', 'open-5090'], help='Use the isolated connection and backups for this model profile.')
    parser.add_argument('--backup', help='Private rescue archive for a new/empty Pod.')
    parser.add_argument('--timeout', type=int, default=900)
    parser.add_argument('--setup', action='store_true', help='Read setup log instead of worker log.')
    args = parser.parse_args()
    if args.profile:
        LOCAL = REPO / '.runpod' / args.profile
        PROFILE = LOCAL / 'connection.json'
    profile = json.loads(PROFILE.read_text()) if PROFILE.exists() else {}
    if args.ssh:
        connection = parse_connection(args.ssh)
        if any(profile.get(k) != v for k, v in connection.items()):
            profile.pop('pod_id', None)
        profile.update(connection)
    if 'target' not in profile:
        parser.error('First run needs --ssh "ssh root@IP -p PORT". Register the public key and verify host fingerprint first.')
    profile['identity'] = str(Path(args.identity or profile.get('identity', '~/.ssh/runpod_esg')).expanduser())
    for flag in ['known_hosts', 'pod_id']:
        if getattr(args, flag):
            profile[flag] = getattr(args, flag)
    save_profile(profile)
    print(f"Connecting: {profile['target']}:{profile['port']}; identity={profile['identity']}", flush=True)
    pod = Pod(profile)
    if args.command == 'up':
        with (LOCAL / 'controller.lock').open('a') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise RuntimeError('Another local setup is running. Use ./runpod status to inspect it.')
            up(pod, profile, args)
    elif args.command == 'status':
        print(json.dumps(pod.helper('inspect'), ensure_ascii=False, indent=2))
    elif args.command == 'backup':
        backup(pod, profile)
    elif args.command == 'copy-key':
        subprocess.run(['pbcopy'], input=api_key(pod).encode(), check=True)
        print('API key copied to clipboard; paste directly into Render. Do not paste into chat.')
    else:
        path = REMOTE_ROOT + ('/setup.log' if args.setup else '/worker.log')
        print(pod.run(shlex.join(['tail', '-n', '60', path])).decode(), end='')


if __name__ == '__main__':
    try:
        main()
    except (ValueError, RuntimeError, OSError, subprocess.CalledProcessError) as exc:
        print(f'RunPod setup stopped: {exc}', file=sys.stderr)
        print('For SSH failures, verify the current Connect command, public key, and host fingerprint manually.', file=sys.stderr)
        sys.exit(1)
