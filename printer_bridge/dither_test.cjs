// Use the production algorithms without a browser; never edit their kernels here.
const fs=require('fs');
const path=require('path');
const ts=require('../web/node_modules/typescript');
const src=fs.readFileSync(path.join(__dirname,'../web/src/utils/thermal.ts'),'utf8');
const code=ts.transpileModule(src,{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText;
const api={};
new Function('exports','require',code)(api,()=>({default:JSON.parse(fs.readFileSync(path.join(__dirname,'../web/src/utils/blue-noise-64.json'),'utf8'))}));
const dir=path.resolve(__dirname,'../output/pt210-test');
const raw=fs.readFileSync(path.join(dir,'source.rgba'));
const width=360,height=raw.length/4/width;
for(const method of ['atkinson','floyd-steinberg','ordered-bayer','ordered-blue-noise']){
 const rgba=api.monochromePixels(new Uint8ClampedArray(raw),width,height,{method,brightness:1,contrast:1});
 fs.writeFileSync(path.join(dir,method+'.rgba'),rgba);
}
