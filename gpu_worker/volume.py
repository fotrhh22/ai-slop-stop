"""Portable Pod lifecycle. No cloud resources are created by this utility.

Run with the template's Python. Install/start require a separately mounted disk;
the RunPod console must additionally confirm it is the intended Network Volume.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import secrets
import sqlite3
import subprocess
import sys
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = ROOT.parent
RUNTIME = Path('/opt/ai-slop-venv')  # Rebuild on each new container; weights stay on volume.


def check_mount(workspace=WORKSPACE):
    mount = json.loads(subprocess.check_output(
        ['findmnt', '-J', '-T', str(workspace), '-o', 'TARGET,SOURCE,FSTYPE'], text=True
    ))['filesystems'][0]
    if (Path(mount['target']).resolve() != workspace.resolve()
            or mount['fstype'] in {'overlay', 'tmpfs', 'ramfs'}):
        raise RuntimeError(f'Persistent mount required at {workspace}; found '
                           f"{mount['target']} ({mount['fstype']}). Installation refused.")
    print(json.dumps({'workspace': str(workspace), 'mount': mount,
                      'note': 'Confirm Network Volume ID and datacenter in RunPod console.'}))


def configuration():
    path = ROOT / 'worker.env'
    if not path.exists():
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, 'w') as stream:
            stream.write(f'GPU_API_KEY={secrets.token_hex(32)}\n'
                         'GPU_IMAGE_MODEL=black-forest-labs/FLUX.2-klein-4B\n'
                         'GPU_MODEL_REVISION=e7b7dc27f91deacad38e78976d1f2b499d76a294\n'
                         'GPU_ROUTER_MODEL=Qwen/Qwen3.5-4B\n'
                         'GPU_ROUTER_REVISION=851bf6e806efd8d0a36b00ddf55e13ccb7b8cd0a\n')
    os.chmod(path, 0o600)
    values = dict(line.split('=', 1) for line in path.read_text().splitlines()
                  if line and not line.startswith('#') and '=' in line)
    if len(values.get('GPU_API_KEY', '')) < 32:
        raise RuntimeError('Missing/invalid API key. Existing configuration was preserved.')
    # Preserve credentials and model revisions; derive paths for the new container.
    values.update(HF_HOME=str(WORKSPACE / 'huggingface'),
                  GPU_LEDGER_PATH=str(WORKSPACE / 'ai-slop/requests.sqlite3'),
                  TOKENIZERS_PARALLELISM='false')
    return values


def runtime_identity():
    import torch
    if sys.version_info[:2] != (3, 12) or torch.__version__ != '2.8.0+cu128' or torch.version.cuda != '12.8':
        raise RuntimeError('Use the validated PyTorch 2.8.0 CUDA 12.8 template; '
                           'other runtimes require a separate GPU acceptance test.')
    if torch.cuda.device_count() not in (1, 2):
        raise RuntimeError('One or two CUDA GPUs are required.')
    names = [torch.cuda.get_device_name(i) for i in range(torch.cuda.device_count())]
    if len(set(names)) != 1:
        raise RuntimeError('Use matching GPU models in the same Pod.')
    return {'python': sys.version.split()[0], 'torch': torch.__version__,
            'cuda': torch.version.cuda, 'gpu': torch.cuda.get_device_name(0),
            'gpu_count': torch.cuda.device_count()}



def install_dependencies(python):
    """Both persistent and ephemeral Pods use the same constrained fast-path install."""
    constraints = str(ROOT / 'gpu_worker/runtime.constraints.txt')
    pip = [str(python), '-m', 'pip', 'install', '-c', constraints]
    subprocess.run(pip + ['-r', str(ROOT / 'gpu_worker/requirements.lock.txt')], check=True)
    env = os.environ.copy()
    env.setdefault('MAX_JOBS', '2')
    subprocess.run(pip + ['--no-build-isolation', '-r',
        str(ROOT / 'gpu_worker/kernel-requirements.lock.txt')], check=True, env=env)
    # A successful pip install is insufficient when a CUDA extension cannot import.
    subprocess.run([str(python), '-m', 'gpu_worker.kernels'], cwd=ROOT, check=True)


def install():
    check_mount()
    identity = runtime_identity()
    configuration()
    subprocess.run([sys.executable, '-m', 'venv', '--system-site-packages', str(RUNTIME)], check=True)
    install_dependencies(RUNTIME / 'bin/python')
    (RUNTIME / 'runtime.json').write_text(json.dumps(identity, indent=2))
    print('Runtime installed. Start the worker next; credentials were not printed.')


def start():
    check_mount()
    runtime_identity()
    python = RUNTIME / 'bin/python'
    if not python.exists() or not (RUNTIME / 'runtime.json').exists():
        raise RuntimeError('Run the install command in this container first.')
    os.environ.update(configuration())
    os.chdir(ROOT)
    # Foreground process: use the Pod startup command or a supervisor to own it.
    # app.py also locks the ledger, rejecting another worker using this volume.
    os.execv(str(python), [str(python), '-m', 'uvicorn', 'gpu_worker.app:app',
                          '--host', '0.0.0.0', '--port', '8000', '--workers', '1'])


def snapshot_ledger(source, destination):
    """SQLite online backup is consistent even while the API is serving requests."""
    with sqlite3.connect(source.as_uri() + '?mode=ro', uri=True) as src:
        with sqlite3.connect(destination) as dst:
            src.backup(dst)
            if dst.execute('pragma integrity_check').fetchone()[0] != 'ok':
                raise RuntimeError('Ledger backup failed integrity check.')


def backup(output, with_cache=False):
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    # No mount guard here: this command must also rescue the current ephemeral Pod.
    # Exclusive create avoids overwriting an earlier backup; archive includes a secret.
    fd = os.open(output, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        with os.fdopen(fd, 'wb') as stream, tempfile.TemporaryDirectory() as temp:
            with tarfile.open(fileobj=stream, mode='w:gz') as archive:
                for name in ['gpu_worker', 'server', 'worker.env', 'docs']:
                    path = ROOT / name
                    if path.exists():
                        archive.add(path, arcname=f'ai-slop-stop/{name}',
                                    filter=lambda info: None if '__pycache__' in Path(info.name).parts else info)
                ledger = WORKSPACE / 'ai-slop/requests.sqlite3'
                if ledger.exists():
                    snap = Path(temp) / 'requests.sqlite3'
                    snapshot_ledger(ledger, snap)
                    archive.add(snap, arcname='ai-slop/requests.sqlite3')
                if with_cache:
                    cache = WORKSPACE / 'huggingface'
                    if not cache.exists():
                        raise RuntimeError('Requested model cache is missing.')
                    archive.add(cache, arcname='huggingface')
                manifest = Path(temp) / 'manifest.json'
                manifest.write_text(json.dumps({'created_at': datetime.now(timezone.utc).isoformat(),
                    'includes_model_cache': with_cache, 'contains_credentials': True,
                    'ledger_semantics': 'Running requests become unknown on next worker start; never auto-retry.'}))
                archive.add(manifest, arcname='ai-slop-backup-manifest.json')
    except BaseException:
        output.unlink(missing_ok=True)
        raise
    print(f'Backup saved: {output}; private archive, keep mode 600 and do not commit/upload publicly.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    for command in ['check', 'install', 'start']:
        commands.add_parser(command)
    export = commands.add_parser('backup')
    export.add_argument('--output', type=Path, required=True)
    export.add_argument('--with-cache', action='store_true')
    args = parser.parse_args()
    if args.command == 'backup':
        backup(args.output, args.with_cache)
    else:
        {'check': check_mount, 'install': install, 'start': start}[args.command]()


if __name__ == '__main__':
    main()
