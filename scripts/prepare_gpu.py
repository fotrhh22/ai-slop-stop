#!/usr/bin/env python3
"""Explicit operator command: start an existing Pod, provision, verify, register.

Never creates, edits, terminates or silently substitutes a paid Pod. SSH key/host
trust remain operator prerequisites. No cloud calls happen on participant select.
"""
import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import sys
import time
from types import SimpleNamespace
import urllib.error
import urllib.request
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import runpod as controller
from server.gpu_defaults import shared_gpu_key

ROOT = controller.REPO


def read_env(path):
    if not path.is_file():
        return
    if path.stat().st_mode & 0o077:
        raise RuntimeError('Operator env must be private: chmod 600 ' + str(path))
    for line in path.read_text().splitlines():
        if line.strip() and not line.lstrip().startswith('#') and '=' in line:
            key, value = line.split('=', 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def call(url, token, body=None, header='Authorization'):
    request = urllib.request.Request(url,
        data=None if body is None else json.dumps(body).encode(),
        headers={header: ('Bearer ' if header == 'Authorization' else '') + token,
                 'Content-Type': 'application/json'})
    operation = ('Render GPU registration' if header == 'X-Gpu-Control-Token' else
                 'RunPod status GET' if body is None else 'RunPod action POST')
    # Status reads are safe to retry. Never repeat Pod start or registration POSTs.
    attempts = 3 if body is None else 1
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            if body is None and exc.code in (429, 502, 503, 504) and attempt+1 < attempts:
                time.sleep(attempt+1)
                continue
            raise RuntimeError(operation+' failed (HTTP '+str(exc.code)+'); no action was automatically retried.') from None
        except (OSError, ValueError) as exc:
            reason = getattr(exc, 'reason', exc)
            cause = type(reason).__name__
            if body is None and attempt+1 < attempts:
                time.sleep(attempt+1)
                continue
            suffix = ('Status read failed after '+str(attempts)+' attempts; check current Pod state.' if body is None else
                      'POST sent once; outcome may be unconfirmed. Check current state before retrying.')
            raise RuntimeError(operation+' response unconfirmed ('+cause+'). '+suffix) from None


class RunPodAPI:
    def __init__(self, key):
        self.key = key

    def get(self, pod_id):
        return call('https://api.runpod.io/v2/pods/' + pod_id, self.key)

    def start(self, pod_id):
        return call('https://api.runpod.io/v2/pods/' + pod_id + '/action', self.key, {'action': 'start'})


def validate_pod(pod, pod_id, gpu):
    if pod.get('id') != pod_id:
        raise RuntimeError('Pod identity mismatch.')
    if 'RTX ' + gpu not in pod.get('gpu', {}).get('id', '') or pod.get('gpu', {}).get('count') not in (1, 2):
        raise RuntimeError('Selected Pod must have one or two RTX ' + gpu + ' GPUs.')
    if not {'22/tcp', '8000/http'}.issubset(set(pod.get('ports', []))):
        raise RuntimeError('Set 22/tcp and 8000/http before installation. No Pod edit was attempted.')


def connection(pod):
    direct = (pod.get('ssh') or {}).get('direct') or {}
    host, port = direct.get('host'), direct.get('port')
    if not host:
        for item in (pod.get('runtime') or {}).get('ports', []):
            if item.get('private') == 22 and item.get('type') == 'tcp':
                host, port = item.get('ip'), item.get('public')
                break
    if not host or not port:
        return None
    return controller.parse_connection('ssh root@' + host + ' -p ' + str(port))


def wait_for_pod(api, pod_id, gpu, deadline, stage):
    pod = api.get(pod_id)
    validate_pod(pod, pod_id, gpu)
    status = pod.get('status')
    if status in ('EXITED', 'ERROR'):
        if 'start' not in pod.get('actions', []):
            raise RuntimeError('Pod cannot start; check available hardware in RunPod.')
        stage('pod_start_requested')
        api.start(pod_id)  # exactly once; timeout must not blindly repeat this POST
    elif status not in ('RUNNING', 'STARTING', 'PROVISIONING'):
        raise RuntimeError('Unsupported Pod status; no action was sent.')
    while time.monotonic() < deadline:
        pod = api.get(pod_id)
        validate_pod(pod, pod_id, gpu)
        if pod.get('status') in ('EXITED', 'ERROR'):
            raise RuntimeError('Pod failed to start; GPU may be unavailable. No replacement was created.')
        direct = connection(pod)
        if pod.get('status') == 'RUNNING' and direct:
            return direct
        stage('waiting_for_ssh_address')
        time.sleep(5)
    raise RuntimeError('Pod wait expired. No replacement was created; inspect RunPod billing/status.')


def prepare(args):
    gpu = args.profile[-4:]
    read_env(Path(args.env).expanduser())
    # Both GPU profiles intentionally reuse the event's 5090 API credential.
    key = shared_gpu_key()
    if key:
        os.environ['GPU_4090_API_KEY'] = os.environ['GPU_5090_API_KEY'] = key
    pod_id = args.pod_id or os.environ.get('GPU_' + gpu + '_POD_ID', '')
    if not re.fullmatch(r'[a-zA-Z0-9]{5,40}', pod_id):
        raise RuntimeError('Set a valid Pod ID for this profile.')
    required = ['RUNPOD_API_KEY', 'GPU_CONTROL_TOKEN', 'GPU_CONTROL_API_BASE', 'GPU_' + gpu + '_API_KEY']
    if any(not os.environ.get(k) for k in required):
        raise RuntimeError('Missing operator configuration; see docs/runpod-operator.env.example.')
    base = os.environ['GPU_CONTROL_API_BASE'].rstrip('/')
    if not base.startswith('https://') or len(os.environ['GPU_CONTROL_TOKEN']) < 32 or len(os.environ['GPU_' + gpu + '_API_KEY']) < 32:
        raise RuntimeError('Control endpoint must use HTTPS and tokens must be at least 32 characters.')
    controller.LOCAL = ROOT / '.runpod' / args.profile
    controller.PROFILE = controller.LOCAL / 'connection.json'
    controller.LOCAL.mkdir(parents=True, exist_ok=True, mode=0o700)
    state_path = controller.LOCAL / 'preparation.json'
    last_stage = None

    def stage(name):
        nonlocal last_stage
        value = {'profile_id': args.profile, 'pod_id': pod_id, 'stage': name, 'time': time.time()}
        if name == 'failed_requires_operator_check':
            value['failed_at'] = last_stage
        state_path.write_text(json.dumps(value))
        last_stage = name
        print('Preparation:', name, flush=True)

    with (controller.LOCAL / 'controller.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('This profile is already being prepared.') from None
        try:
            stage('checking_pod')
            direct = wait_for_pod(RunPodAPI(os.environ['RUNPOD_API_KEY']), pod_id, gpu,
                                  time.monotonic() + args.timeout, stage)
            profile = json.loads(controller.PROFILE.read_text()) if controller.PROFILE.exists() else {}
            profile.update(direct, pod_id=pod_id,
                identity=str(Path(args.identity).expanduser()))
            if args.known_hosts:
                profile['known_hosts'] = str(Path(args.known_hosts).expanduser())
            controller.save_profile(profile)
            pod = controller.Pod(profile)
            stage('checking_ssh_trust')
            pod.run('true')  # strict host-key validation; never accept an unknown host silently
            stage('install_restore_and_model_load')
            controller.up(pod, profile, SimpleNamespace(backup=args.backup, timeout=args.timeout,
                api_key=os.environ['GPU_' + gpu + '_API_KEY']))
            stage('registering_authenticated_runtime')
            response = call(base + '/api/gpu-control/register', os.environ['GPU_CONTROL_TOKEN'],
                {'profile_id': args.profile, 'pod_id': pod_id, 'runtime_id': str(uuid4())},
                header='X-Gpu-Control-Token')
            if response.get('ready') is not True or response.get('profile_id') != args.profile:
                raise RuntimeError('Runtime registration unconfirmed; no ready state was reported.')
            stage('ready')
            print('Model profile ready. Render GPU_API_URL editing is not needed.', flush=True)
        except Exception:
            stage('failed_requires_operator_check')
            raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', required=True, choices=['open-4090', 'open-5090'])
    parser.add_argument('--pod-id')
    parser.add_argument('--env', default=str(ROOT / '.runpod/operator.env'))
    parser.add_argument('--identity', default='~/.ssh/runpod_esg')
    parser.add_argument('--known-hosts')
    parser.add_argument('--backup')
    parser.add_argument('--timeout', type=int, default=1800)
    args = parser.parse_args()
    try:
        prepare(args)
    except Exception as exc:
        # Never echo URLs, HTTP response bodies, SSH stdin or key-bearing args.
        print('GPU preparation stopped: ' + (str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__))
        state_path = ROOT / '.runpod' / args.profile / 'preparation.json'
        if state_path.exists():
            failed_at = json.loads(state_path.read_text()).get('failed_at')
            if failed_at:
                print('Failed stage: '+failed_at)
        print('Check Pod state. No automatic action retry or shutdown was performed.')
        if isinstance(exc, RuntimeError) and str(exc).startswith('SSH'):
            print('Verify the SSH public key and host fingerprint for this Pod.')
        raise SystemExit(1)


if __name__ == '__main__':
    main()
