"""Mac-side router comparison: identical saved image pairs, no new generation or DB writes."""
import argparse
import base64
from dataclasses import replace
from datetime import datetime,timezone
import hashlib
import html
import io
import json
import os
from pathlib import Path
import shlex
import statistics
import subprocess
import sys
import tarfile
import time
from uuid import uuid4
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts import runpod as controller
from benchmarks.router_pairs import GOALS,PAIRS

ROOT=controller.REPO


def save(path,data):
    temp=path.with_suffix('.tmp');temp.write_text(json.dumps(data,ensure_ascii=False,indent=2));temp.replace(path)


def build_dataset(source,folder):
    from PIL import Image
    images=folder/'images';images.mkdir()
    manifest={name:json.loads((source/'evidence'/name/'results.json').read_text()) for name in ['klein-9b-nf4','klein-4b-bf16','z-image-turbo','qwen-image-lightning-nf4']}
    files={};pairs=[]
    def asset(model,case):
        key=(model,case)
        if key not in files:
            row=next(r for r in manifest[model]['results'] if r['case_id']==case)
            original=source/'evidence'/model/row['image']
            name=model+'--'+case+'.jpg'
            with Image.open(original) as image:
                image=image.convert('RGB');image.thumbnail((512,512),Image.Resampling.LANCZOS);image.save(images/name,format='JPEG',quality=85)
            files[key]='images/'+name
        return files[key]
    for id,goal,model,case,expected,reason in PAIRS:
        target=asset('klein-9b-nf4',goal);generated=asset(model,case)
        pairs.append({'id':id,'goal':goal,'target':target,'generated':generated,
            'expected':expected,'label_reason':reason,**GOALS[goal],
            'target_sha256':hashlib.sha256((folder/target).read_bytes()).hexdigest(),
            'generated_sha256':hashlib.sha256((folder/generated).read_bytes()).hexdigest()})
    save(folder/'dataset.json',pairs)
    return pairs


def gemini(folder,dataset):
    # Read existing local configuration, never send the key to the Pod.
    for file in [ROOT.parents[1]/'.env.local',ROOT.parents[1]/'.env']:
        if file.exists():
            for line in file.read_text().splitlines():
                s=line.strip()
                if s and not s.startswith('#') and '=' in s:
                    k,v=s.split('=',1);os.environ.setdefault(k.strip(),v.strip().strip('"').strip("'"))
    result={'mode':'gemini_original','results':[],'energy':'not_measurable','vram':'not_measurable'}
    path=folder/'gemini-results.json'
    try:
        from server.config import load_settings
        from server.gemini_client import route_verdict
        settings=load_settings();settings=replace(settings,router_model=settings.gemini_router_model)
        result['model']=settings.router_model
        if not settings.gemini_configured:raise RuntimeError('GEMINI_KEY_MISSING')
        for case in dataset:
            row={'mode':'gemini_original','pair_id':case['id'],'expected':case['expected'],
                'request_id':str(uuid4()),'status':'unconfirmed'}
            result['results'].append(row);save(path,result)
            class Recorder:
                def start(self):return row['request_id']
                def finish(self,cid,response=None,error=None):
                    if response:
                        usage=getattr(response,'usage_metadata',None)
                        row['tokens']={k:getattr(usage,k,None) for k in ['prompt_token_count','candidates_token_count','thoughts_token_count','total_token_count']}
                        row['model_version']=getattr(response,'model_version',None)
            start=time.monotonic()
            try:
                verdict=route_verdict(settings,(folder/case['target']).read_bytes(),'image/jpeg',
                    (folder/case['generated']).read_bytes(),'image/jpeg',recorder=Recorder())
                row.update(status='succeeded',verdict=verdict['verdict'],verdict_raw=verdict,elapsed_s=time.monotonic()-start)
            except Exception as exc:
                row.update(status='unconfirmed',error=type(exc).__name__,elapsed_s=time.monotonic()-start)
                row['error_category']=next((name for name in ['NOT_FOUND','PERMISSION_DENIED','UNAUTHENTICATED','RESOURCE_EXHAUSTED','INVALID_ARGUMENT','ROUTER_TIMEOUT'] if name in str(exc)),'UNCONFIRMED')
                save(path,result)
                raise RuntimeError('GEMINI_REQUEST_UNCONFIRMED_STOPPED_WITHOUT_RETRY') from None
            save(path,result)
        result['status']='completed'
    except Exception as exc:
        result.update(status='failed',error=type(exc).__name__)
    save(path,result)


def summarize(rows,dataset):
    labels={c['id']:c['expected'] for c in dataset};good=[r for r in rows if r.get('status')=='succeeded' and r.get('verdict') in ['PASS','FAIL']]
    accuracy=sum(r['verdict']==labels[r['pair_id']] for r in good)/len(dataset) if len(rows)==len(dataset) else None
    times=[r['elapsed_s'] for r in good]
    energy=[r['energy']['energy_j']/3.6 for r in good if (r.get('energy') or {}).get('status')=='measured']
    def peak(key):
        values=[r[key] for r in good if r.get(key) is not None];return max(values) if values else None
    return {'attempted':len(rows),'valid_verdicts':len(good),'errors':len(rows)-len(good),
        'accuracy':accuracy,'false_pass':sum(r['verdict']=='PASS' and labels[r['pair_id']]=='FAIL' for r in good),
        'false_fail':sum(r['verdict']=='FAIL' and labels[r['pair_id']]=='PASS' for r in good),
        'mean_elapsed_s':statistics.mean(times) if times else None,
        'min_elapsed_s':min(times) if times else None,'max_elapsed_s':max(times) if times else None,
        'mean_gpu_window_s':statistics.mean(r['energy']['duration_s'] for r in good if r.get('energy')) if energy else None,
        'mean_mwh':statistics.mean(energy) if energy else None,
        'peak_vram_gib':peak('peak_vram_gib'),'peak_allocated_gib':peak('peak_allocated_gib'),'peak_reserved_gib':peak('peak_reserved_gib')}


def report(folder):
    dataset=json.loads((folder/'dataset.json').read_text());q=json.loads((folder/'router-results.json').read_text()) if (folder/'router-results.json').exists() else {'results':[]}
    g=json.loads((folder/'gemini-results.json').read_text()) if (folder/'gemini-results.json').exists() else {'results':[]}
    groups={name:[r for r in q['results'] if r['mode']==name] for name in ['qwen_original','qwen_checklist']};groups['gemini_original']=g['results']
    summary={name:summarize(rows,dataset) for name,rows in groups.items()};save(folder/'summary.json',summary)
    esc=lambda value:html.escape(str(value))
    parts=['<!doctype html><meta charset="utf-8"><title>Router comparison</title><style>body{font:15px system-ui;margin:24px}table{border-collapse:collapse;width:100%;table-layout:fixed}td,th{border:1px solid #ddd;padding:8px;vertical-align:top}img{max-width:100%}pre{white-space:pre-wrap}</style>',
        '<h1>동일 이미지 쌍 · 판정 비교</h1><p>기대 라벨은 호출 전 고정한 육안 평가 기준. 4 PASS / 6 FAIL. 이미지 생성 없음. 체크리스트 방식은 목표별 조건 제공과 서버 집계까지 포함하므로 모델 자체만의 비교가 아님.</p>',
        '<p>Gemini의 GPU 전력/VRAM은 미계측. Qwen은 512px 입력·BF16·동일 가중치·동일 커널로 측정. 평균은 유효한 응답 기준, 오류는 별도 표시.</p>',
        '<h2>요약</h2><pre>'+esc(json.dumps(summary,ensure_ascii=False,indent=2))+'</pre>',
        '<h2>GPU 실행 상태</h2><pre>'+esc(json.dumps({k:v for k,v in q.items() if k!='results'},ensure_ascii=False,indent=2))+'</pre>']
    for case in dataset:
        parts+=['<h2>'+esc(case['id'])+' · 예상 '+esc(case['expected'])+'</h2><p>'+esc(case['label_reason'])+'</p>',
            '<table><tr><td>TARGET<br><img src="'+esc(case['target'])+'"></td><td>GENERATED<br><img src="'+esc(case['generated'])+'"></td></tr></table>',
            '<table><tr>'+''.join('<th>'+esc(name)+'</th>' for name in groups)+'</tr><tr>']
        for rows in groups.values():
            row=next((r for r in rows if r['pair_id']==case['id']),{})
            parts+=['<td><pre>'+esc(json.dumps(row,ensure_ascii=False,indent=2))+'</pre></td>']
        parts+=['</tr></table>']
    (folder/'report.html').write_text('\n'.join(parts),encoding='utf-8');return summary


def run(args):
    profile=json.loads((ROOT/'.runpod'/args.profile/'connection.json').read_text());pod=controller.Pod(profile)
    run_id=args.collect or (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid4().hex[:8])
    import re
    if not re.fullmatch(r'[a-zA-Z0-9-]{8,80}',run_id):raise ValueError('Invalid run ID')
    folder=ROOT/'benchmark-results'/('router-'+run_id);remote='/workspace/ai-slop-router-benchmarks/'+run_id
    if not args.collect:
        folder.mkdir(parents=True,exist_ok=False);dataset=build_dataset(args.source,folder)
        print('Router run ID:',run_id,flush=True)
        print('Expected labels fixed: 4 PASS / 6 FAIL. Images:',len(list((folder/'images').glob('*'))),flush=True)
        source=subprocess.check_output(['git','archive','--format=tar','HEAD','gpu_worker','server','benchmarks'],cwd=ROOT)
        extract="import sys,tarfile;from pathlib import Path;p=Path("+repr(remote)+");p.mkdir(parents=True,exist_ok=False);tarfile.open(fileobj=sys.stdin.buffer,mode='r|').extractall(p,filter='data')"
        pod.run(shlex.join(['python','-c',extract]),data=source)
        buf=io.BytesIO()
        with tarfile.open(fileobj=buf,mode='w:gz') as tar:
            tar.add(folder/'dataset.json',arcname='dataset.json');tar.add(folder/'images',arcname='images')
        extract="import sys,tarfile;tarfile.open(fileobj=sys.stdin.buffer,mode='r|gz').extractall("+repr(remote)+",filter='data')"
        pod.run(shlex.join(['python','-c',extract]),data=buf.getvalue())
        launch="import os,subprocess;from pathlib import Path;p=Path("+repr(remote)+");log=(p/'router.log').open('ab');env=os.environ.copy();env.update(OMP_NUM_THREADS='4',MKL_NUM_THREADS='4',TORCHINDUCTOR_COMPILE_THREADS='2');proc=subprocess.Popen(['/opt/ai-slop-venv/bin/python','-u','-m','gpu_worker.router_benchmark','--dataset','dataset.json'],cwd=p,env=env,stdin=subprocess.DEVNULL,stdout=log,stderr=log,start_new_session=True);print('ROUTER_PID',proc.pid)"
        print(pod.run(shlex.join(['python','-c',launch])).decode(),flush=True)
        gemini(folder,dataset)
        print('Gemini status:',json.loads((folder/'gemini-results.json').read_text())['status'],flush=True)
        previous=None;deadline=time.monotonic()+3600
        while time.monotonic()<deadline:
            source="from pathlib import Path;p=Path("+repr(remote+'/router-status.json')+");print(p.read_text() if p.exists() else '{}')"
            state=json.loads(pod.run(shlex.join(['python','-c',source])))
            if state!=previous:print(json.dumps(state),flush=True);previous=state
            if state.get('status') in ['completed','failed']:break
            time.sleep(5)
        else:raise RuntimeError('Timed out; collect using '+run_id+' without resending requests')
    for name in ['router-results.json','router-status.json','router.log']:
        (folder/name).write_bytes(pod.run(shlex.join(['cat',remote+'/'+name])))
    summary=report(folder);print(json.dumps(summary,ensure_ascii=False,indent=2));print('Report:',folder/'report.html',flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--profile',choices=['open-4090','open-5090'],required=True)
    parser.add_argument('--source',type=Path,default=ROOT/'benchmark-results/review-5090-four-models-20261105');parser.add_argument('--collect')
    run(parser.parse_args())
