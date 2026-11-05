"""Upload benchmark-only source, launch without changing the service, collect local reports."""
import html
import io
import json
from pathlib import Path
import re
import shlex
import subprocess
import tarfile
import time
from datetime import datetime, timezone
from uuid import uuid4
from scripts import runpod as controller
from scripts.benchmark_images import report
from gpu_worker.image_candidates import CANDIDATES


def validate_cases(cases):
    if not isinstance(cases,list) or not 1<=len(cases)<=20:
        raise ValueError('Use 1–20 cases')
    for c in cases:
        if (not isinstance(c,dict) or not isinstance(c.get('id'),str) or
            not isinstance(c.get('prompt'),str) or not 1<=len(c['prompt'])<=2000 or
            not isinstance(c.get('criteria'),list) or not all(isinstance(v,str) for v in c['criteria'])):
            raise ValueError('Invalid id/prompt/criteria in benchmark cases')


def extract_evidence(data,folder):
    # Only regular evidence files; no symlinks, traversal, absolute paths or model weights.
    with tarfile.open(fileobj=io.BytesIO(data),mode='r:gz') as archive:
        members=archive.getmembers()
        if sum(m.size for m in members)>256*1024**2:raise ValueError('Evidence archive too large')
        for member in members:
            name=Path(member.name)
            if name.is_absolute() or '..' in name.parts or not name.parts or name.parts[0]!='evidence':
                raise ValueError('Unsafe evidence path')
            if member.isdir():continue
            if not member.isfile():raise ValueError('Evidence archive must contain regular files only')
            target=folder/name
            target.parent.mkdir(parents=True,exist_ok=True)
            with archive.extractfile(member) as src:target.write_bytes(src.read())


def comparison(folder):
    esc=lambda v:html.escape(str(v))
    runs=[]
    for name in CANDIDATES:
        path=folder/'evidence'/name/'results.json'
        if path.exists():
            report(path)
            runs.append((name,json.loads(path.read_text())))
    parts=['<!doctype html><meta charset="utf-8"><title>후보 이미지 비교</title>',
        '<style>body{font:15px system-ui;padding:20px}table{width:100%;table-layout:fixed}td,th{vertical-align:top;border:1px solid #ddd;padding:10px}img{width:100%}pre{white-space:pre-wrap}</style>',
        '<h1>같은 한국어 프롬프트 · 모델별 비교</h1>',
        '<p>1024×1024, 고정 시드. 모델별 스텝·양자화는 다름. 품질 자동 채점 없음. 다운로드·로딩·워밍업은 생성 시간/전력에서 제외.</p>',
        '<pre>'+esc((folder/'suite-status.json').read_text())+'</pre>']
    if runs:
        parts+=['<table><tr><th>프롬프트</th>'+''.join('<th><a href="evidence/'+esc(n)+'/report.html">'+esc(n)+'</a></th>' for n,_ in runs)+'</tr>']
        keys=[]
        for _,e in runs:
            for r in e['results']:
                key=(r['case_id'],r.get('seed'))
                if key not in keys:keys.append(key)
        for key in keys:
            parts.append('<tr><td>'+esc(key)+'</td>')
            for name,e in runs:
                row=next((r for r in e['results'] if (r['case_id'],r.get('seed'))==key),None)
                parts.append('<td>')
                if row:
                    parts.append('<p>'+esc(row['prompt'])+'</p>')
                    if row.get('image'):parts.append('<img src="evidence/'+esc(name)+'/'+esc(row['image'])+'">')
                    stats={k:row[k] for k in ['status','gpu_s','elapsed_s','energy_mwh','vram_sampled_peak'] if k in row}
                    parts.append('<pre>'+esc(json.dumps(stats,ensure_ascii=False,indent=2))+'</pre>')
                else:parts.append('실행 실패 또는 결과 없음')
                parts.append('</td>')
            parts.append('</tr>')
        parts.append('</table>')
    for name,e in runs:
        if e.get('error'):parts.append('<p>'+esc(name)+': '+esc(e['error'])+'</p>')
    (folder/'comparison.html').write_text('\n'.join(parts),encoding='utf-8')


def run(args):
    profile=json.loads((controller.REPO/'.runpod'/args.profile/'connection.json').read_text())
    pod=controller.Pod(profile)
    last_run=controller.REPO/'.runpod'/args.profile/'last-benchmark.json'
    collect=args.collect
    if collect=='latest':collect=json.loads(last_run.read_text())['run_id']
    run_id=collect or (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid4().hex[:8])
    if not re.fullmatch(r'[a-zA-Z0-9-]{8,80}',run_id):raise ValueError('Invalid benchmark run ID')
    remote='/workspace/ai-slop-benchmarks/'+run_id
    if not args.collect:
        cases=json.loads(args.cases.read_text(encoding='utf-8'));validate_cases(cases)
        candidates=([name for name in CANDIDATES if name!='klein-4b-bf16'] if args.candidate=='all' else
            ['z-image-turbo','qwen-image-lightning-nf4'] if args.candidate=='public' else [args.candidate])
        last_run.write_text(json.dumps({'run_id':run_id,'profile':args.profile}))
        last_run.chmod(0o600)
        print('Run ID:',run_id,flush=True)
        print('Resume collection: ./runpod benchmark --profile '+args.profile+' --collect '+run_id,flush=True)
        # Does not include worker.env, operator env, SSH keys, image caches or service databases.
        source=subprocess.check_output(['git','archive','--format=tar','HEAD','gpu_worker','benchmarks'],cwd=controller.REPO)
        extractor="import sys,tarfile;from pathlib import Path;p=Path("+repr(remote)+");p.mkdir(parents=True,exist_ok=False);tarfile.open(fileobj=sys.stdin.buffer,mode='r|').extractall(p,filter='data')"
        pod.run(shlex.join(['python','-c',extractor]),data=source)
        config={'candidates':candidates,'device':args.device,'repeat':args.repeat,
                'offload':args.offload,'quantization':args.quantization}
        setup="import sys,json,pathlib,subprocess; p=pathlib.Path("+repr(remote)+");v=json.load(sys.stdin);(p/'cases.json').write_text(json.dumps(v['cases'],ensure_ascii=False));(p/'config.json').write_text(json.dumps(v['config'])); log=(p/'suite.log').open('ab'); proc=subprocess.Popen(['/opt/ai-slop-venv/bin/python','-u','-m','gpu_worker.candidate_suite','--config','config.json'],cwd=p,stdin=subprocess.DEVNULL,stdout=log,stderr=log,start_new_session=True);print('Benchmark PID',proc.pid)"
        print(pod.run(shlex.join(['python','-c',setup]),data=json.dumps({'cases':cases,'config':config}).encode()).decode())
        last=None;deadline=time.monotonic()+11000
        while time.monotonic()<deadline:
            state=pod.run(shlex.join(['python','-c',
                "import pathlib; p=pathlib.Path("+repr(remote+'/suite-status.json')+");print(p.read_text() if p.exists() else '{}')"])).decode()
            value=json.loads(state)
            if state!=last:print(state.strip(),flush=True);last=state
            if value.get('status') in ['completed','completed_with_errors','failed']:break
            time.sleep(5)
        else:raise RuntimeError('Benchmark wait expired; collect using the run ID. No automatic retry.')
    folder=controller.REPO/'benchmark-results'/('candidates-'+run_id)
    folder.mkdir(parents=True,exist_ok=True)
    status=pod.run(shlex.join(['cat',remote+'/suite-status.json']))
    (folder/'suite-status.json').write_bytes(status)
    (folder/'suite.log').write_bytes(pod.run(shlex.join(['cat',remote+'/suite.log'])))
    (folder/'suite.log').chmod(0o600)
    # Fetch even failed runs; no inference is retried by collection.
    data=pod.run(shlex.join(['tar','-czf','-','-C',remote,'evidence']))
    extract_evidence(data,folder)
    comparison(folder)
    print('Report:',folder/'comparison.html',flush=True)
