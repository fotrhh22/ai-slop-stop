const assert = require('node:assert/strict');
const fs = require('node:fs');
const ts = require('typescript');
const source = fs.readFileSync('src/utils/thermal.ts', 'utf8');
const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
const api = {};
new Function('exports', code)(api);
const pixels = values => new Uint8ClampedArray(values.flatMap(v => [v, v, v, 255]));
const mono = (values, width, method) => api.monochromePixels(pixels(values), width, values.length / width,
  { method, brightness: 1, contrast: 1 });
const shades = rgba => Array.from(rgba).filter((_, i) => i % 4 === 0);

// Correct polarity, including constant white/black edge cases.
assert.deepEqual(shades(mono([0, 30, 100, 200], 4, 'mean')), [0, 0, 255, 255]);
for (const method of ['atkinson', 'floyd-steinberg', 'mean']) {
  assert.deepEqual(shades(mono([0, 0, 0, 0], 2, method)), [0, 0, 0, 0]);
  assert.deepEqual(shades(mono([255, 255, 255, 255], 2, method)), [255, 255, 255, 255]);
  const transparent = api.monochromePixels(new Uint8ClampedArray([0, 0, 0, 0]), 1, 1,
    { method, brightness: 1, contrast: 1 });
  assert.deepEqual(Array.from(transparent), [255, 255, 255, 255]);
}
// Small reference patterns distinguish the 6/8 Atkinson and full-error FS kernels.
assert.deepEqual(shades(mono(Array(9).fill(130), 3, 'atkinson')), [255, 0, 255, 255, 0, 0, 0, 255, 255]);
assert.deepEqual(shades(mono(Array(9).fill(130), 3, 'floyd-steinberg')), [255, 0, 255, 0, 255, 0, 255, 0, 255]);

const ramp = Array.from({ length: 4096 }, (_, i) => (i % 64) * 255 / 63);
for (const method of ['atkinson', 'floyd-steinberg', 'mean']) {
  const input = pixels(ramp), before = input.slice();
  const output = api.monochromePixels(input, 64, 64, { method, brightness: 1, contrast: 1 });
  assert.deepEqual(input, before, 'color original must remain untouched');
  for (let i = 0; i < output.length; i += 4) {
    assert.ok(output[i] === 0 || output[i] === 255, 'every pixel must be strictly black or white');
    assert.equal(output[i], output[i + 1]); assert.equal(output[i], output[i + 2]); assert.equal(output[i + 3], 255);
  }
}
assert.throws(() => api.monochromePixels(pixels([100]), 2, 1, { method: 'atkinson', brightness: 1, contrast: 1 }), /INVALID_IMAGE_SIZE/);
assert.throws(() => api.monochromePixels(pixels([100]), 1, 1, { method: 'atkinson', brightness: NaN, contrast: 1 }), /INVALID_THERMAL_OPTIONS/);
console.log('Thermal conversion: Atkinson/FS reference kernels, mean polarity, alpha, binary output, original preservation passed');
