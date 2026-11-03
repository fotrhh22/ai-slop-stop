"""Explicit short-lived Pod setup; local backup is required before stop/edit."""
import argparse
import json
import os
import subprocess
import sys

from gpu_worker import volume


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['install', 'start'])
    parser.add_argument('--accept-ephemeral', action='store_true', required=True,
                        help='Acknowledge that stop/edit can erase this installation.')
    args = parser.parse_args()
    identity = volume.runtime_identity()
    values = volume.configuration()
    python = volume.RUNTIME / 'bin/python'
    if args.command == 'install':
        subprocess.run([sys.executable, '-m', 'venv', '--system-site-packages',
                        str(volume.RUNTIME)], check=True)
        subprocess.run([str(python), '-m', 'pip', 'install', '-r',
                        str(volume.ROOT / 'gpu_worker/requirements.lock.txt')], check=True)
        (volume.RUNTIME / 'runtime.json').write_text(json.dumps(identity))
        print('Ephemeral setup complete. Stop/edit may erase it; back up before stopping.')
    else:
        if not python.exists() or not (volume.RUNTIME / 'runtime.json').exists():
            raise RuntimeError('Run ephemeral install in this container first.')
        os.environ.update(values)
        os.chdir(volume.ROOT)
        os.execv(str(python), [str(python), '-m', 'uvicorn', 'gpu_worker.app:app',
                              '--host', '0.0.0.0', '--port', '8000', '--workers', '1'])


if __name__ == '__main__':
    main()
