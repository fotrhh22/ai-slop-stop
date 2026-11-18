"""Gemini REST inference with a persistent no-repeat request ledger."""
import base64
import hashlib
import io
import json
import os
from time import perf_counter

import httpx
from PIL import Image
from .gpu import GPUClient, GPUError
from .study import CONTENT


class GeminiClient(GPUClient):
    def __init__(self, config, store):
        super().__init__('gemini', config)
        self.store = store
        self.key = os.getenv('POC_GEMINI_API_KEY', os.getenv('GEMINI_API_KEY', ''))
        self.image_model = config['profiles']['gemini']['image_model']
        self.router_model = config['profiles']['gemini']['router_model']

    async def health(self):
        if not self.key:
            raise GPUError('GEMINI_CONFIGURATION_REQUIRED')
        return {'ok': True, 'mode': 'gemini', 'runtime_id': 'gemini-' + hashlib.sha256((self.image_model+'|'+self.router_model).encode()).hexdigest(), 'image_model': self.image_model, 'router_model': self.router_model}

    async def call(self, rid, model, payload):
        fingerprint = hashlib.sha256(json.dumps([model,payload],sort_keys=True).encode()).hexdigest()
        with self.store.db(write=True) as db:
            old = db.execute('SELECT * FROM poc_api_calls WHERE id=?',(rid,)).fetchone()
            if old:
                if old['fingerprint'] != fingerprint:
                    raise GPUError('GEMINI_REQUEST_CONFLICT')
                if old['status'] != 'done':
                    raise GPUError(old['error'] or 'GEMINI_RESULT_UNCONFIRMED')
                return json.loads(old['response'])
            db.execute("INSERT INTO poc_api_calls(id,fingerprint,status) VALUES(?,?,'running')",(rid,fingerprint))
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(120,connect=10)) as client:
                res = await client.post('https://generativelanguage.googleapis.com/v1beta/models/'+model+':generateContent',headers={'x-goog-api-key':self.key},json=payload)
            if not res.is_success:
                raise GPUError('GEMINI_HTTP_'+str(res.status_code))
            data = res.json()
            with self.store.db(write=True) as db:
                db.execute("UPDATE poc_api_calls SET status='done',response=? WHERE id=?",(json.dumps(data),rid))
            return data
        except Exception as exc:
            error = str(exc) if isinstance(exc,GPUError) else 'GEMINI_RESULT_UNCONFIRMED'
            with self.store.db(write=True) as db:
                db.execute("UPDATE poc_api_calls SET status='error',error=? WHERE id=?",(error,rid))
            raise GPUError(error) from None

    @staticmethod
    def parts(data):
        return [p for c in data.get('candidates',[]) for p in (c.get('content') or {}).get('parts',[]) if not p.get('thought')]

    async def generate(self, rid, prompt, seed):
        start=perf_counter()
        data=await self.call(rid,self.image_model,{'contents':[{'role':'user','parts':[{'text':prompt}]}], 'generationConfig':{'responseModalities':['IMAGE'],'imageConfig':{'aspectRatio':'1:1'},'thinkingConfig':{'thinkingLevel':'MINIMAL'}}})
        for part in self.parts(data):
            inline=part.get('inlineData') or part.get('inline_data')
            if inline and inline.get('data'):
                raw=base64.b64decode(inline['data'],validate=True)
                if len(raw)>12_000_000:
                    raise GPUError('GEMINI_INVALID_IMAGE')
                with Image.open(io.BytesIO(raw)) as im:
                    if im.width*im.height>16_000_000:
                        raise GPUError('GEMINI_INVALID_IMAGE')
                    out=io.BytesIO();im.convert('RGB').save(out,format='PNG');size=list(im.size)
                return out.getvalue(),{'mode':'gemini','model':self.image_model,'model_version':data.get('modelVersion'),'usage':data.get('usageMetadata',{}),'seed_requested':seed,'seed_controlled':False,'image_size':size,'generation_s':perf_counter()-start}
        raise GPUError('GEMINI_NO_IMAGE')

    async def route(self, rid, raw, prompt):
        start=perf_counter()
        def image(raw):
            with Image.open(io.BytesIO(raw)) as im:
                im=im.convert('RGB');im.thumbnail((512,512));out=io.BytesIO();im.save(out,format='JPEG',quality=90)
            return {'inlineData':{'mimeType':'image/jpeg','data':base64.b64encode(out.getvalue()).decode()}}
        data=await self.call(rid,self.router_model,{'systemInstruction':{'parts':[{'text':(CONTENT/'server/evaluate.md').read_text()}]},'contents':[{'role':'user','parts':[{'text':'TARGET:'},image((CONTENT/'public/target-a.png').read_bytes()),{'text':'GENERATED:'},image(raw)]}], 'generationConfig':{'responseMimeType':'text/x.enum','responseSchema':{'type':'STRING','enum':['PASS','FAIL']},'temperature':0.2,'thinkingConfig':{'thinkingLevel':'MINIMAL'}}})
        text=''.join(p.get('text','') for p in self.parts(data)).strip()
        if text not in ('PASS', 'FAIL'):
            raise GPUError('GEMINI_INVALID_VERDICT')
        verdict={'verdict':text}
        return verdict,{'mode':'gemini','model':self.router_model,'model_version':data.get('modelVersion'),'usage':data.get('usageMetadata',{}),'output_format':'pass-fail-v1','router_s':perf_counter()-start}
