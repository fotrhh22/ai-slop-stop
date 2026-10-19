// Exercise the real API client without a browser or any external requests.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const ts = require('typescript');
const { randomUUID } = require('node:crypto');
const source = fs.readFileSync('src/api/client.ts', 'utf8')
  .replaceAll('import.meta.env', '({ VITE_USE_MOCK: "false", VITE_API_BASE: "https://api.test" })');
const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;

function client(fetch) {
  const exports = {};
  // Scheduled waits are immediate; request abort timers must not fire early.
  const schedule = (fn, ms) => ms < 5000 ? setTimeout(fn, 0) : setTimeout(fn, ms);
  new Function('exports', 'fetch', 'crypto', 'setTimeout', 'clearTimeout', code)(
    exports, fetch, { randomUUID }, schedule, clearTimeout);
  return exports;
}
const response = (body, status = 200) => ({ ok: status < 400, status, json: async () => body });
const challenge = { id: 'challenge', accessToken: 'session-token' };
const attempt = { id: 'attempt', imageUrl: 'data:image/png;base64,test' };

async function main() {
  let starts = [], polls = 0;
  const api = client(async (url, options) => {
    assert.equal(options.headers['X-Challenge-Token'], 'session-token');
    if (options.method === 'POST') {
      starts.push(JSON.parse(options.body).job_id);
      if (starts.length === 1) throw new TypeError('response lost after acceptance');
      return response({ job_id: starts[0], status: 'running' }, 202);
    }
    if (++polls === 1) throw new TypeError('temporary connection loss');
    assert.ok(polls <= 3, 'must finish rather than poll forever');
    return response(polls === 2 ? { job_id: starts[0], status: 'running' }
      : { job_id: starts[0], status: 'done', result: { verdict: 'FAIL', reason: '구도가 다릅니다.' } });
  });
  const result = await api.evaluate(challenge, attempt);
  assert.equal(result.verdict, 'FAIL');
  assert.equal(starts.length, 2);
  assert.equal(starts[0], starts[1], 'start retry must reuse the idempotency ID');
  assert.equal(polls, 3);

  let requests = 0;
  const timeoutApi = client(async () => {
    requests++;
    return response({ job_id: 'job', status: 'error', error: 'ROUTER_TIMEOUT' }, 202);
  });
  await assert.rejects(timeoutApi.evaluate(challenge, attempt), /Gemini 판정 응답 대기 시간/);
  assert.equal(requests, 1, 'provider timeout must not start another paid evaluation');

  const passApi = client(async () => response({ job_id: 'job', status: 'done', result: {
    verdict: 'PASS', reason: '일치합니다.', receipt_pending: true,
  } }, 202));
  assert.equal((await passApi.evaluate(challenge, attempt)).receipt_pending, true);
  console.log('Evaluation client: lost start response, polling recovery, timeout, PASS handoff passed');
}
main().catch(error => { console.error(error); process.exitCode = 1; });
