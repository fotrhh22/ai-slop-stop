"""Sequential isolated processes: each candidate releases CUDA memory before the next."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from gpu_worker.image_candidates import CANDIDATES

MODULES = {'z-image-turbo':'benchmarks.z_image_turbo',
           'klein-9b-nf4':'benchmarks.klein_9b_nf4',
           'qwen-image-lightning-nf4':'benchmarks.qwen_image_lightning_nf4'}


def save(path, value):
    temp=path.with_suffix('.tmp');temp.write_text(json.dumps(value,indent=2));temp.replace(path)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--config',type=Path,required=True)
    args=parser.parse_args()
    config=json.loads(args.config.read_text())
    evidence=Path('evidence');evidence.mkdir(exist_ok=False)
    state={'status':'running','results':[],'pid':os.getpid()}
    status=Path('suite-status.json');save(status,state)
    try:
        from gpu_worker.kernels import check
        check()  # Fail visibly if the prepared runtime is incomplete.
        import peft
        for name in config['candidates']:
            if name not in CANDIDATES:raise ValueError('Unknown candidate')
            row={'candidate':name,'status':'running'};state['results'].append(row);save(status,state)
            command=[sys.executable,'-m',MODULES[name],'--cases','cases.json',
                '--output',str(evidence/name),'--device',str(config['device']),
                '--repeat',str(config['repeat'])]
            if config.get('offload'):command+=['--offload']
            if config.get('quantization'):command+=['--quantization',config['quantization']]
            start=time.monotonic()
            try:
                with (evidence/(name+'.log')).open('wb') as log:
                    completed=subprocess.run(command,stdout=log,stderr=log,timeout=3600)
                row.update(status='completed' if completed.returncode==0 else 'failed',
                           exit_code=completed.returncode,elapsed_s=time.monotonic()-start)
            except subprocess.TimeoutExpired:
                row.update(status='timeout',elapsed_s=time.monotonic()-start)
            save(status,state)
        state['status']='completed' if all(r['status']=='completed' for r in state['results']) else 'completed_with_errors'
    except Exception as exc:
        state.update(status='failed',error=type(exc).__name__)
        import traceback
        traceback.print_exc()
    finally:
        save(status,state)


if __name__=='__main__':main()
