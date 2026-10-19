const assert = require('node:assert/strict');
const fs = require('node:fs');
const ts = require('typescript');
const code = ts.transpileModule(fs.readFileSync('src/utils/printReceipt.ts', 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 }
}).outputText;
const api = {};
new Function('exports', 'require', code)(api, () => ({ default: '' }));
// Preserve physical roll width and grow the page with the receipt, never A4.
assert.equal(api.receiptPageSize(80, 960), '80mm 255mm');
assert.equal(api.receiptPageSize(58, 1920), '58mm 509mm');
assert.equal(api.receiptPageSize(80, 960.1), '80mm 256mm');
for (const [width, height] of [[210, 960], [80, NaN], [58, 0], [80, 8000]]) {
  assert.throws(() => api.receiptPageSize(width, height));
}
console.log('Receipt print: 58/80mm roll dimensions, content height, rounding and oversized document guards passed');
