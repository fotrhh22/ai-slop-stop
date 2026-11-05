"""Dedicated offline candidate execution. Does not replace or stop the service worker."""
import argparse
import fcntl
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
import math
import os
import shutil
from pathlib import Path
import threading
import time
from uuid import uuid4

from gpu_worker.image_candidates import CANDIDATES, generation_kwargs, load_pipeline


def save(path, value):
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2))
    temp.replace(path)


def main(candidate_id):
    parser = argparse.ArgumentParser()
    parser.add_argument('--cases', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--device', type=int, default=0)
    parser.add_argument('--repeat', type=int, choices=range(1,4), default=1)
    parser.add_argument('--quantization', choices=['bf16','nf4'])
    parser.add_argument('--offload', action='store_true')
    parser.add_argument('--stream-cache', action='store_true')
    args = parser.parse_args()
    os.environ.setdefault('HF_HOME','/workspace/huggingface')
    folder = args.output
    folder.mkdir(parents=True,exist_ok=False)
    manifest = folder/'results.json'
    candidate = CANDIDATES[candidate_id]
    evidence = {'candidate':candidate_id, 'mode':'offline_generation',
        'health':asdict(candidate), 'results':[]}
    save(manifest,evidence)
    try:
        import torch
        import pynvml
        from zeus.monitor import ZeusMonitor
        from gpu_worker.volume import runtime_identity
        runtime_identity()
        if not 0 <= args.device < torch.cuda.device_count():
            raise ValueError('Selected CUDA device unavailable')
        torch.cuda.set_device(args.device)
        props = torch.cuda.get_device_properties(args.device)
        uuid = str(props.uuid)
        if not uuid.startswith('GPU-'): uuid = 'GPU-'+uuid
        lock_dir=Path('/workspace/ai-slop-benchmarks');lock_dir.mkdir(exist_ok=True)
        gpu_lock=(lock_dir/('.gpu-'+uuid+'.lock')).open('a')
        fcntl.flock(gpu_lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        pynvml.nvmlInit()
        handle = pynvml.nvmlDeviceGetHandleByUUID(uuid)
        others = [p.pid for p in pynvml.nvmlDeviceGetComputeRunningProcesses(handle) if p.pid != os.getpid()]
        if others:
            raise RuntimeError('GPU_BUSY_STOP_SERVICE_BEFORE_BENCHMARK')
        cases = json.loads(args.cases.read_text())
        evidence['disk_free_gib_before_load']=shutil.disk_usage('/workspace').free/1024**3
        save(manifest,evidence)
        start = time.monotonic()
        pipe, metadata = load_pipeline(candidate,args.device,args.quantization,args.offload,args.stream_cache)
        if metadata['quantization'] == 'nf4':
            if not all(getattr(getattr(pipe,c), 'is_loaded_in_4bit', False) for c in ['transformer','text_encoder']):
                raise RuntimeError('NF4_NOT_APPLIED_TO_BOTH_COMPONENTS')
        evidence['health'] = {**metadata, 'gpu_name':props.name, 'gpu_uuid':uuid,
            'device':args.device,'load_s':time.monotonic()-start,
            'scope':'generation GPU only; no judge/CLIP', 'seed_policy':'42 + case_index + repeat_index*100',
            'torch':torch.__version__}
        # One warmup outside participant windows and outside reported test energy.
        with torch.inference_mode():
            pipe(**generation_kwargs(candidate,'흰색 배경의 빨간색 사과',41,torch,'cuda:'+str(args.device)))
        torch.cuda.synchronize(args.device)
        monitor = ZeusMonitor(gpu_indices=[args.device])
        for repeat in range(args.repeat):
            for index,case in enumerate(cases):
                rid = str(uuid4()); seed = 42+index+repeat*100
                row = {'case_id':case['id'], 'prompt':case['prompt'], 'criteria':case['criteria'],
                    'request_id':rid,'seed':seed,'status':'unconfirmed'}
                evidence['results'].append(row);save(manifest,evidence)
                samples=[];stop=threading.Event()
                def sample():
                    while not stop.is_set():
                        samples.append(pynvml.nvmlDeviceGetMemoryInfo(handle).used)
                        stop.wait(.05)
                thread=threading.Thread(target=sample,daemon=True)
                torch.cuda.reset_peak_memory_stats(args.device)
                thread.start();opened=False;measurement=None
                try:
                    start=time.monotonic();monitor.begin_window(rid);opened=True
                    with torch.inference_mode():
                        image=pipe(**generation_kwargs(candidate,case['prompt'],seed,torch,'cuda:'+str(args.device))).images[0]
                    torch.cuda.synchronize(args.device)
                finally:
                    try:
                        if opened:measurement=monitor.end_window(rid)
                    finally:
                        stop.set();thread.join(timeout=2)
                elapsed=time.monotonic()-start
                joules=measurement.total_energy
                valid=math.isfinite(joules) and joules>0
                filename=rid+'.png';image.save(folder/filename,format='PNG')
                row.update(status='succeeded',image=filename,
                    image_sha256=hashlib.sha256((folder/filename).read_bytes()).hexdigest(),
                    elapsed_s=elapsed,gpu_s=measurement.time,
                    energy_mwh=joules/3.6 if valid else None,
                    energy={'status':'measured' if valid else 'unavailable','energy_j':joules if valid else None},
                    vram_sampled_peak=[{'gpu':args.device,'uuid':uuid,
                        'peak_mib':max(samples)/1024**2 if samples else None,
                        'total_mib':props.total_memory/1024**2,'samples':len(samples)}],
                    torch_peak_allocated_mib=torch.cuda.max_memory_allocated(args.device)/1024**2,
                    torch_peak_reserved_mib=torch.cuda.max_memory_reserved(args.device)/1024**2)
                save(manifest,evidence)
        evidence['status']='completed'
    except Exception as exc:
        evidence['status']='failed'
        evidence['error']=type(exc).__name__
        if str(exc) == 'GPU_BUSY_STOP_SERVICE_BEFORE_BENCHMARK': evidence['error']=str(exc)
        # Detailed stack trace stays in the private remote log, never a success result.
        import traceback
        traceback.print_exc()
        raise
    finally:
        save(manifest,evidence)
