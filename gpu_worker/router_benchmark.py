"""Offline Qwen router benchmark; reuses the production Runner and records per-call peaks."""
import argparse
import base64
import json
import math
import os
from pathlib import Path
import threading
import time
from uuid import uuid4


def save(path,data):
    temp=path.with_suffix('.tmp');temp.write_text(json.dumps(data,ensure_ascii=False,indent=2));temp.replace(path)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--dataset',type=Path,required=True);args=parser.parse_args()
    root=Path.cwd();state=root/'router-status.json';output=root/'router-results.json'
    evidence={'status':'loading','results':[],'warmups':[]}
    save(state,{'status':'loading'});save(output,evidence)
    try:
        os.environ.update(GPU_ROUTER_MODEL='Qwen/Qwen3.5-4B',
            GPU_ROUTER_REVISION='851bf6e806efd8d0a36b00ddf55e13ccb7b8cd0a',
            GPU_ROUTER_QUANTIZATION='bf16',GPU_ROUTER_DEVICE='0',GPU_ROUTER_MAX_PIXELS='262144')
        os.environ.setdefault('HF_HOME',str(root/'hf-cache'))
        import torch
        import pynvml
        torch.set_num_threads(4)
        from gpu_worker.volume import runtime_identity
        from gpu_worker.app import QwenRunner,RouteRequest
        from benchmarks.router_pairs import IMPROVED,aggregate
        runtime_identity();pynvml.nvmlInit()
        uuid=str(torch.cuda.get_device_properties(0).uuid)
        if not uuid.startswith('GPU-'):uuid='GPU-'+uuid
        handle=pynvml.nvmlDeviceGetHandleByUUID(uuid)
        if any(p.pid!=os.getpid() for p in pynvml.nvmlDeviceGetComputeRunningProcesses(handle)):
            raise RuntimeError('GPU_BUSY')
        dataset=json.loads(args.dataset.read_text())
        judge=QwenRunner();original=judge.system
        evidence['metadata']={'model':judge.model_id,'revision':judge.revision,'quantization':judge.quantization,
            'gpu_name':judge.gpu_name,'gpu_uuid':judge.gpu_uuid,'max_pixels_per_image':judge.max_pixels,
            'max_new_tokens':256,'thinking':False,'do_sample':False,'cpu_threads':4,
            'kernels':judge.fast_path_versions,'input_long_edge':512,'original_prompt':original,
            'warmup_note':'QwenRunner initialization probe plus one 512px pair per prompt mode; excluded from summary'}
        def req(case):
            def image(name):return 'data:image/jpeg;base64,'+base64.b64encode((root/name).read_bytes()).decode()
            return RouteRequest(request_id=uuid4(),target_image=image(case['target']),generated_image=image(case['generated']))
        for mode in ['qwen_original','qwen_checklist']:
            def system(case):return original if mode=='qwen_original' else original+'\n'+IMPROVED.format(checks=case['checks'],required=', '.join(case['required']))
            judge.system=system(dataset[0]);warm=judge.run(req(dataset[0]));evidence['warmups'].append({'mode':mode,'status':warm['status'],'error':warm.get('error')})
            if warm['status']!='succeeded':raise RuntimeError('ROUTER_WARMUP_FAILED')
            for case in dataset:
                judge.system=system(case);request=req(case)
                row={'mode':mode,'pair_id':case['id'],'expected':case['expected'],'request_id':str(request.request_id),'status':'unconfirmed'}
                evidence['results'].append(row);save(output,evidence);save(state,{'status':'running','mode':mode,'pair':case['id'],'completed':len(evidence['results'])-1})
                torch.cuda.reset_peak_memory_stats(0);samples=[];stop=threading.Event()
                def sample():
                    while not stop.is_set():samples.append(pynvml.nvmlDeviceGetMemoryInfo(handle).used);stop.wait(.05)
                thread=threading.Thread(target=sample,daemon=True);thread.start();start=time.monotonic()
                try:result=judge.run(request)
                finally:stop.set();thread.join(timeout=2)
                row.update(status=result['status'],elapsed_s=time.monotonic()-start,error=result.get('error'),
                    verdict_raw=result.get('verdict'),energy=result.get('energy'),
                    peak_vram_gib=max(samples)/1024**3 if samples else None,
                    peak_allocated_gib=torch.cuda.max_memory_allocated(0)/1024**3,
                    peak_reserved_gib=torch.cuda.max_memory_reserved(0)/1024**3,
                    image_tokens=getattr(judge,'vision_tokens',None))
                if result['status']=='succeeded':
                    row['verdict']=aggregate(result['verdict'],case['required']) if mode=='qwen_checklist' else result['verdict']['verdict']
                save(output,evidence)
        evidence['status']='completed'
    except Exception as exc:
        evidence.update(status='failed',error=type(exc).__name__)
        import traceback;traceback.print_exc()
    finally:
        save(output,evidence);save(state,{'status':evidence['status'],'error':evidence.get('error')})


if __name__=='__main__':main()
