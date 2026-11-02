// Exercise actual generation client through lost HTTP responses, without a GPU.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const ts = require('typescript');
const { randomUUID } = require('node:crypto');
const source = fs.readFileSync('src/api/client.ts', 'utf8')
  .replaceAll('import.meta.env', '({ VITE_USE_MOCK: "false", VITE_API_BASE: "https://api.test" })');
const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
function client(fetch) {
  const exports = {};
  const schedule = (fn, ms) => ms < 5000 ? setTimeout(fn, 0) : setTimeout(fn, ms);
  new Function('exports', 'fetch', 'crypto', 'setTimeout', 'clearTimeout', 'require', code)(
    exports, fetch, { randomUUID }, schedule, clearTimeout, () => ({ pairingToken: () => '' }));
  return exports;
}
const response = (body, status = 200) => ({ ok: status < 400, status, json: async () => body });
const challenge = { id: 'challenge', accessToken: 'session-token' };
async function main() {
  const starts = []; let polls = 0;
  const energy = {energy_wh: 0.1234, measured_generation_calls: 1};
  const api = client(async (url, options) => {
    assert.equal(options.headers['X-Challenge-Token'], 'session-token');
    if (options.method === 'POST') {
      starts.push(JSON.parse(options.body).attempt_id);
      if (starts.length === 1) throw new TypeError('accepted but response lost');
      return response({job_id:'attempt',status:'running'},202);
    }
    assert.ok(url.endsWith('/generations/attempt'));
    if (++polls === 1) throw new TypeError('poll response lost');
    return response({job_id:'attempt',status:'done',result:{imageUrl:'data:image/png;base64,test',energy}});
  });
  const result = await api.generateImage('a bicycle',challenge,'attempt');
  assert.deepEqual(starts,['attempt','attempt']);
  assert.deepEqual(result.energy, energy);
  assert.equal(polls,2);
  let calls = 0;
  await assert.rejects(client(async () => {
    calls++; return response({detail:'GENERATION_BUSY'},503);
  }).generateImage('test',challenge,'attempt'), /GENERATION_BUSY/);
  assert.equal(calls,1,'busy response must not requeue a GPU job');
  await assert.rejects(client(async () => response({job_id:'attempt',status:'error',error:'GPU_RESULT_UNCONFIRMED'},202))
    .generateImage('test',challenge,'attempt'), /GPU_RESULT_UNCONFIRMED/);
  console.log('Generation client: repeat-safe start, polling recovery, energy payload, busy and unknown outcomes passed');
}
main().catch(error => { console.error(error); process.exitCode=1; });
