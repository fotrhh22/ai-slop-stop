"""Offline Mac CLIP audit of saved router pairs. No env files, HTTP polling or API calls."""
import argparse
import csv
from datetime import datetime, timezone
import hashlib
import html
import importlib.metadata
import json
from pathlib import Path
import platform
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def read_json(path):
    return json.loads(path.read_text())


def classification(expected, verdict, status):
    if status != 'succeeded' or verdict not in ('PASS', 'FAIL'):
        return 'UNAVAILABLE'
    if expected == 'PASS' and verdict == 'FAIL':
        return 'FALSE_FAIL'
    if expected == 'FAIL' and verdict == 'PASS':
        return 'FALSE_PASS'
    return 'CORRECT'


def checked_image(source, relative, expected_hash):
    path = (source / relative).resolve()
    if not path.is_relative_to(source.resolve()):
        raise ValueError('Image path escapes source directory')
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected_hash:
        raise ValueError('Input image hash mismatch: ' + relative)
    return raw


def judge_rows(source):
    rows = []
    for name in ('router-results.json', 'gemini-results.json'):
        path = source / name
        if path.exists():
            rows.extend(read_json(path)['results'])
    keys = [(r['pair_id'], r['mode']) for r in rows]
    if len(keys) != len(set(keys)):
        raise ValueError('Duplicate pair/mode results')
    return {(r['pair_id'], r['mode']): r for r in rows}


def write_outputs(out, metadata, pairs, joined):
    payload = {'metadata': metadata, 'pairs': pairs, 'judgments': joined}
    (out / 'results.json').write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    with (out / 'results.csv').open('w', newline='', encoding='utf-8-sig') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(joined[0]))
        writer.writeheader()
        writer.writerows(joined)
    summaries = []
    for mode in sorted({r['mode'] for r in joined}):
        rows = [r for r in joined if r['mode'] == mode]
        valid_pass = [r for r in rows if r['expected'] == 'PASS' and r['classification'] != 'UNAVAILABLE']
        valid_fail = [r for r in rows if r['expected'] == 'FAIL' and r['classification'] != 'UNAVAILABLE']
        ff = sum(r['classification'] == 'FALSE_FAIL' for r in rows)
        fp = sum(r['classification'] == 'FALSE_PASS' for r in rows)
        summaries.append({'mode': mode, 'false_fail': ff, 'valid_expected_pass': len(valid_pass),
                          'false_fail_rate': ff / len(valid_pass) if valid_pass else None,
                          'false_pass': fp, 'valid_expected_fail': len(valid_fail),
                          'false_pass_rate': fp / len(valid_fail) if valid_fail else None,
                          'unavailable': sum(r['classification'] == 'UNAVAILABLE' for r in rows)})
    # Descriptive threshold audit only: CLIP PASS iff cosine >= threshold.
    # These are in-sample counts, NOT a calibrated deployment rule.
    sweep = []
    for cutoff in sorted({-1.0, 1.0, *(p['cosine'] for p in pairs)}):
        sweep.append({'threshold': cutoff,
                      'false_fail': sum(p['expected'] == 'PASS' and p['cosine'] < cutoff for p in pairs),
                      'false_pass': sum(p['expected'] == 'FAIL' and p['cosine'] >= cutoff for p in pairs)})
    (out / 'summary.json').write_text(json.dumps({'judges': summaries, 'clip_threshold_audit': sweep,
        'warning': 'Small existing labeled sample; no selected threshold, no automatic verdict overrides.'},
        ensure_ascii=False, indent=2))
    (out / 'threshold-audit.csv').write_text('threshold,false_fail,false_pass\n' + ''.join(
        f"{r['threshold']},{r['false_fail']},{r['false_pass']}\n" for r in sweep))
    esc = lambda value: html.escape(str(value))
    parts = ['<!doctype html><meta charset="utf-8"><title>Local CLIP audit</title>',
        '<style>body{font:16px system-ui;max-width:1100px;margin:30px auto;padding:15px}img{width:240px;max-width:45%}'
        'table{border-collapse:collapse;width:100%}td,th{border:1px solid #ccc;padding:8px;text-align:left}'
        '.FALSE_FAIL{background:#ffe0dc}article{margin:32px 0;border-top:1px solid #aaa}</style>',
        '<h1>Mac 로컬 CLIP · 기존 판정 비교</h1>',
        '<p>정답→FAIL을 우선 검토. 기존 예상 라벨과 응답은 변경하지 않음. 코사인은 정답 확률/일치율이 아님. '
        'CLIP 임계값은 이 소표본으로 확정하지 않으며 자동 판정 변경도 하지 않음.</p>',
        '<table><tr><th>모드</th><th>정답→FAIL / 정답 표본</th><th>오답→PASS / 오답 표본</th><th>미확인</th></tr>']
    for row in summaries:
        parts.append(f"<tr><td>{esc(row['mode'])}</td><td>{row['false_fail']}/{row['valid_expected_pass']}</td>"
                     f"<td>{row['false_pass']}/{row['valid_expected_fail']}</td><td>{row['unavailable']}</td></tr>")
    parts.append('</table>')
    # Existing false FAIL first, then other expected PASS, then expected FAIL.
    priority = {r['pair_id'] for r in joined if r['classification'] == 'FALSE_FAIL'}
    for pair in sorted(pairs, key=lambda p: (p['id'] not in priority, p['expected'] != 'PASS', p['id'])):
        parts.append(f"<article><h2>{esc(pair['id'])} · 기준 {esc(pair['expected'])} · CLIP {pair['cosine']:.6f}</h2>"
                     f"<p>{esc(pair['label_reason'])}</p><img src='{esc(pair['target'])}' alt='TARGET'> "
                     f"<img src='{esc(pair['generated'])}' alt='GENERATED'><table>")
        for row in [r for r in joined if r['pair_id'] == pair['id']]:
            parts.append(f"<tr class='{esc(row['classification'])}'><td>{esc(row['mode'])}</td>"
                         f"<td>{esc(row['verdict'])} · {esc(row['classification'])}</td><td>{esc(row['reason'])}</td></tr>")
        parts.append('</table></article>')
    parts.append('<pre>' + esc(json.dumps(metadata, ensure_ascii=False, indent=2)) + '</pre>')
    (out / 'report.html').write_text('\n'.join(parts))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path, help='Saved router benchmark directory')
    parser.add_argument('--device', choices=('auto', 'cpu', 'mps'), default='auto')
    parser.add_argument('--cache', type=Path, default=ROOT / '.cache/clip')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    source = args.source.resolve()
    dataset = read_json(source / 'dataset.json')
    if not dataset or len({p['id'] for p in dataset}) != len(dataset):
        parser.error('Dataset must contain unique pair IDs')
    for pair in dataset:
        if pair['expected'] not in ('PASS', 'FAIL'):
            parser.error('Invalid expected verdict')
        for key in ('target', 'generated'):
            checked_image(source, pair[key], pair[key + '_sha256'])
    judges = judge_rows(source)
    expected_by_id = {p['id']: p['expected'] for p in dataset}
    for (pair_id, _), row in judges.items():
        if pair_id not in expected_by_id or row.get('expected') != expected_by_id[pair_id]:
            parser.error('Judge result does not match original dataset labels')
    modes = sorted({mode for _, mode in judges})
    if not modes:
        parser.error('No existing judge results')
    from clip_worker.worker import Encoder, MODEL, PIPELINE
    started = time.perf_counter()
    encoder = Encoder(args.device, str(args.cache.resolve()))
    load_s = time.perf_counter() - started
    out = args.output or source / ('clip-local-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
    out = out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    (out / 'images').mkdir()
    metadata = {'status': 'running', 'created_at': datetime.now(timezone.utc).isoformat(), 'source': str(source),
        'dataset_sha256': hashlib.sha256((source / 'dataset.json').read_bytes()).hexdigest(),
        'model': MODEL, 'pipeline': PIPELINE, 'device': encoder.device, 'platform': platform.platform(),
        'python': platform.python_version(), 'torch': encoder.torch.__version__, 'model_load_s': load_s,
        'judge_files_sha256': {name: hashlib.sha256((source / name).read_bytes()).hexdigest()
                              for name in ('router-results.json', 'gemini-results.json') if (source / name).exists()},
        'input': 'Exact saved 512px JPEG bytes from router comparison, no extra transport transform',
        'measurement': 'First encounter encodes image; later shared images use existing Encoder vector cache',
        'policy': 'Review false FAIL first; no automatic PASS/FAIL, no API calls, no Render, no Pod'}
    metadata['clip_version'] = importlib.metadata.version('clip')
    weights = args.cache / 'ViT-B-32.pt'
    metadata['weights_sha256'] = hashlib.sha256(weights.read_bytes()).hexdigest()
    pairs, joined = [], []
    for pair in dataset:
        raw = [checked_image(source, pair[key], pair[key + '_sha256']) for key in ('target', 'generated')]
        for key, data in zip(('target', 'generated'), raw):
            name = pair[key + '_sha256'] + '.jpg'
            (out / 'images' / name).write_bytes(data)
        started = time.perf_counter()
        cosine = encoder.compare(*raw)
        record = {**pair, 'cosine': cosine, 'elapsed_ms': (time.perf_counter() - started) * 1000,
                  'target': 'images/' + pair['target_sha256'] + '.jpg',
                  'generated': 'images/' + pair['generated_sha256'] + '.jpg'}
        pairs.append(record)
        for mode in modes:
            judge = judges.get((pair['id'], mode), {})
            status, verdict = judge.get('status'), judge.get('verdict')
            raw_verdict = judge.get('verdict_raw') or {}
            joined.append({'pair_id': pair['id'], 'goal': pair['goal'], 'expected': pair['expected'],
                'label_reason': pair['label_reason'], 'cosine': cosine, 'mode': mode,
                'status': status or 'missing', 'verdict': verdict, 'raw_verdict': raw_verdict.get('verdict'),
                'request_id': judge.get('request_id'),
                'criteria_json': json.dumps(raw_verdict.get('criteria'), ensure_ascii=False),
                'classification': classification(pair['expected'], verdict, status),
                'reason': raw_verdict.get('reason', ''), 'target_sha256': pair['target_sha256'],
                'generated_sha256': pair['generated_sha256']})
        write_outputs(out, metadata, pairs, joined)
        print(json.dumps({'pair': pair['id'], 'cosine': cosine}, ensure_ascii=False), flush=True)
    metadata['status'] = 'completed'
    metadata['pair_count'] = len(pairs)
    write_outputs(out, metadata, pairs, joined)
    print('Report: ' + str(out / 'report.html'))


if __name__ == '__main__':
    main()
