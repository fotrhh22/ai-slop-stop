const assert = require('node:assert/strict');
const fs = require('node:fs');
const ts = require('typescript');
const code = ts.transpileModule(fs.readFileSync('src/utils/tokenUsage.ts', 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS }
}).outputText;
const api = {};
new Function('exports', code)(api);
assert.equal(api.tokenUsageText(0, 3, 3), '미계측'); // observed FLUX receipt
assert.equal(api.tokenUsageText(0, 1, 0), '0'); // actually reported zero
assert.equal(api.tokenUsageText(150, 4, 3), '150'); // known Qwen + unknown FLUX
assert.equal(api.tokenUsageText(undefined, 1, 0), '미계측');
console.log('Token usage: unreported totals stay distinct from reported zero.');
