"""Offline Korean image benchmark against a prepared worker. No automatic POST retries."""
from __future__ import annotations
import argparse
import base64
import csv
from datetime import datetime, timezone
import hashlib
import html
import json
import math
from pathlib import Path
import shlex
import statistics
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import runpod as controller

ROOT = controller.REPO


def request(base, key, path, body=None, timeout=180):
    req = urllib.request.Request(base + path,
        data=None if body is None else json.dumps(body).encode(),
        headers={'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.load(response)


def save(path, value):
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    temp.replace(path)


class MemorySampler:
    """NVML samples via one SSH stream; no CUDA context/model inference created."""
    def __init__(self, pod):
        self.pod, self.samples, self.error = pod, [], None
        self.process = None

    def start(self):
        remote = shlex.join(['timeout', '3600', 'nvidia-smi',
            '--query-gpu=index,uuid,memory.used,memory.total',
            '--format=csv,noheader,nounits', '-lms', '250'])
        self.process = subprocess.Popen(['ssh', *self.pod.options, '-p', str(self.pod.profile['port']),
            self.pod.profile['target'], remote], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        def read():
            try:
                for line in self.process.stdout:
                    fields = next(csv.reader([line]))
                    if len(fields) == 4:
                        self.samples.append({'time': time.monotonic(), 'gpu': int(fields[0]),
                            'uuid': fields[1].strip(), 'used_mib': float(fields[2]), 'total_mib': float(fields[3])})
            except (ValueError, OSError):
                self.error = 'VRAM sampling failed'
        self.thread = threading.Thread(target=read, daemon=True)
        self.thread.start()
        # Obtain an idle baseline before starting generation, with a bounded wait.
        deadline = time.monotonic() + 8
        while not self.samples and self.process.poll() is None and time.monotonic() < deadline:
            time.sleep(.1)
        if not self.samples:
            self.error = 'VRAM samples unavailable (SSH/nvidia-smi)'

    def window(self, start, finish):
        rows = [s for s in self.samples if start <= s['time'] <= finish]
        result = []
        for uuid in sorted({r['uuid'] for r in rows}):
            group = [r for r in rows if r['uuid'] == uuid]
            peak = max(group, key=lambda r: r['used_mib'])
            result.append({'gpu': peak['gpu'], 'uuid': uuid, 'peak_mib': peak['used_mib'],
                           'total_mib': peak['total_mib'], 'samples': len(group)})
        return result

    def stop(self):
        if self.process:
            self.process.terminate()
            try: self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill(); self.process.wait()
            self.thread.join(timeout=2)
            self.process.stdout.close()


def generate_case(base, key, case, folder, evidence, manifest, sampler):
    row = {'case_id': case['id'], 'prompt': case['prompt'], 'criteria': case['criteria'],
           'request_id': str(uuid4()), 'status': 'unconfirmed'}
    evidence['results'].append(row)
    save(manifest, evidence)  # Persist ID before sending; ambiguous responses are never regenerated.
    start = time.monotonic()
    try:
        payload = request(base, key, '/v1/generate',
            {'request_id': row['request_id'], 'prompt': case['prompt']})
        finish = time.monotonic()
        if payload.get('request_id') != row['request_id']:
            raise ValueError('request ID mismatch')
        if payload.get('status') != 'succeeded':
            row.update(status='failed', error=payload.get('error', 'WORKER_FAILED'), energy=payload.get('energy'))
            return row
        data = payload['image_url']
        prefix, content = data.split(',', 1)
        if prefix != 'data:image/png;base64':
            raise ValueError('unsupported image response')
        raw = base64.b64decode(content, validate=True)
        if not raw.startswith(b'\x89PNG\r\n\x1a\n'):
            raise ValueError('invalid PNG response')
        sha = hashlib.sha256(raw).hexdigest()
        if sha != payload.get('image_sha256'):
            raise ValueError('image hash mismatch')
        energy = payload.get('energy') or {}
        value = energy.get('energy_j')
        mwh = value / 3.6 if energy.get('status') == 'measured' and isinstance(value, (int, float)) and math.isfinite(value) and value > 0 else None
        image = row['request_id'] + '.png'
        (folder / image).write_bytes(raw)
        row.update(status='succeeded', image=image, image_sha256=sha, energy=energy,
                   energy_mwh=mwh, gpu_s=energy.get('duration_s'), api_s=finish-start,
                   vram_sampled_peak=sampler.window(start, finish))
    except Exception as exc:
        row['error'] = type(exc).__name__  # Do not record URLs, keys or full request/response bodies.
        raise RuntimeError('Request result unconfirmed; stopped without retry: ' + row['request_id']) from None
    finally:
        save(manifest, evidence)
    return row


def report(manifest):
    evidence = json.loads(manifest.read_text())
    rows = evidence['results']
    good = [r for r in rows if r['status'] == 'succeeded']
    times = [r.get('api_s', r.get('elapsed_s')) for r in good]
    energies = [r['energy_mwh'] for r in good if r.get('energy_mwh') is not None]
    summary = {'successful': len(good), 'attempted': len(rows),
        'mean_elapsed_s': statistics.mean(times) if times else None,
        'timing_scope': 'direct_generation' if evidence.get('mode')=='offline_generation' else 'worker_api_roundtrip',
        'mean_generation_mwh': statistics.mean(energies) if energies else None,
        'energy_measured_count': len(energies), 'quality': 'not_automatically_scored'}
    save(manifest.parent / 'summary.json', summary)
    esc = lambda v: html.escape(str(v))
    parts = ['<!doctype html><meta charset="utf-8"><title>한국어 이미지 모델 비교</title>',
        '<style>body{font:16px system-ui;max-width:1100px;margin:30px auto;padding:16px;background:#f5f5f5}article{background:white;padding:20px;margin:24px 0;border-radius:12px}img{max-width:100%;max-height:650px}pre{white-space:pre-wrap}li{margin:5px}</style>',
        '<h1>한국어 이미지 생성 결과</h1>', '<p>이미지 생성만 측정. 번역·CLIP·Qwen 판정·웹사이트·DB 호출 없음. 품질은 아래 이미지와 조건을 보고 별도로 평가.</p>',
        '<p>VRAM 표본 간격: worker API 250ms / 후보 직접 실행 50ms. 전력은 담당 GPU 생성 구간만 포함. 후보 직접 실행은 고정 시드를 사용하며 웹 전체 시간과 다름.</p>',
        '<p>상태: '+esc(evidence.get('status','worker API'))+' / '+esc(evidence.get('error',''))+'</p>',
        '<h2>모델 설정</h2><pre>'+esc(json.dumps(evidence['health'],ensure_ascii=False,indent=2))+'</pre>',
        '<h2>요약</h2><pre>'+esc(json.dumps(summary,ensure_ascii=False,indent=2))+'</pre>']
    if evidence.get('memory_error'): parts.append('<p>'+esc(evidence['memory_error'])+'</p>')
    for row in rows:
        parts.extend(['<article><h2>'+esc(row['case_id'])+'</h2><p>'+esc(row['prompt'])+'</p>',
            '<ul>'+''.join('<li>□ '+esc(c)+'</li>' for c in row['criteria'])+'</ul>'])
        if row.get('image'): parts.append('<img src="'+esc(row['image'])+'">')
        stats = {k:row[k] for k in ['status','error','api_s','elapsed_s','gpu_s','energy_mwh','vram_sampled_peak','torch_peak_allocated_mib','torch_peak_reserved_mib'] if k in row}
        parts.append('<pre>'+esc(json.dumps(stats,ensure_ascii=False,indent=2))+'</pre></article>')
    (manifest.parent/'report.html').write_text('\n'.join(parts),encoding='utf-8')
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', choices=['open-4090','open-5090'])
    parser.add_argument('--candidate', choices=['all','public','z-image-turbo','klein-9b-nf4','qwen-image-lightning-nf4'])
    parser.add_argument('--collect', help='Collect a previous candidate benchmark run ID without GPU calls')
    parser.add_argument('--device', type=int, choices=[0,1], default=0)
    parser.add_argument('--offload', action='store_true', help='Explicit CPU offload; recorded in report')
    parser.add_argument('--quantization', choices=['bf16','nf4'])
    parser.add_argument('--cases', type=Path, default=ROOT/'benchmarks/korean-image-prompts.json')
    parser.add_argument('--repeat', type=int, choices=range(1,4), default=1)
    parser.add_argument('--report', type=Path, help='Rebuild report from results.json; no GPU calls')
    args = parser.parse_args()
    if args.report:
        print(json.dumps(report(args.report.resolve()),ensure_ascii=False)); return
    if not args.profile: parser.error('--profile is required')
    if args.candidate or args.collect:
        from scripts.benchmark_candidates import run
        run(args)
        return
    cases = json.loads(args.cases.read_text(encoding='utf-8'))
    if not isinstance(cases,list) or not 1 <= len(cases) <= 20: parser.error('Use 1–20 test cases')
    for c in cases:
        if (not isinstance(c,dict) or not isinstance(c.get('prompt'),str) or not 1<=len(c['prompt'])<=2000
                or not isinstance(c.get('id'),str) or not isinstance(c.get('criteria'),list)):
            parser.error('Each case needs id, prompt (1–2000 chars), criteria list')
    profile = json.loads((ROOT/'.runpod'/args.profile/'connection.json').read_text())
    pod = controller.Pod(profile)
    key = controller.api_key(pod)
    pod_id = profile.get('pod_id','')
    if not pod_id.isalnum() or not 5<=len(pod_id)<=40: parser.error('Invalid/missing Pod ID')
    base = 'https://' + pod_id + '-8000.proxy.runpod.net'
    health = request(base,key,'/health',timeout=15)
    if health.get('ok') is not True: raise RuntimeError('Worker not ready')
    folder = ROOT/'benchmark-results'/(datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+args.profile+'-'+uuid4().hex[:6])
    folder.mkdir(parents=True,exist_ok=False)
    manifest = folder/'results.json'
    evidence = {'profile':args.profile,'health':health,'results':[]}
    save(manifest,evidence)
    sampler = MemorySampler(pod)
    try:
        sampler.start()
        evidence['memory_error'] = sampler.error
        evidence['idle_vram'] = sampler.samples[:]
        for _ in range(args.repeat):
            for case in cases:
                print('Generating:',case['id'],flush=True)
                generate_case(base,key,case,folder,evidence,manifest,sampler)
    finally:
        sampler.stop()
        evidence['memory_error'] = sampler.error
        save(manifest,evidence)
        report(manifest)
        print('Report:',folder/'report.html',flush=True)


if __name__ == '__main__':
    main()
