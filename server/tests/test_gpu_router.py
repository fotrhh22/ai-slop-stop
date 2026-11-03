"""Qwen verdict transport/telemetry tests; no model weights or GPU required."""
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from uuid import uuid4
import httpx
from fastapi.testclient import TestClient
from gpu_worker.app import create_app
from server.energy import energy_summary
from server.gemini_client import GeminiError
from server.persistence import CallRecorder
from server.remote_router import route_verdict
from server.tests.test_gpu_generation import settings, telemetry
from server.tests.test_receipts import MemoryStore, png

VERDICT = {"verdict":"PASS","reason":"주요 피사체와 구도가 일치합니다.","criteria":{key:"MATCH" for key in ("subject","composition","color","background")}}

def router_energy(rid):
    data=telemetry(rid)
    data.update(scope="gpu_board_router_window",width=None,height=None,steps=None,seed=None,
                model_id="Qwen/Qwen3.5-4B",input_tokens=100,output_tokens=50,max_new_tokens=256,thinking=False)
    return data

class RouterTests(unittest.TestCase):
    def setUp(self):
        self.settings=replace(settings(),router_provider="zeus",router_model="Qwen/Qwen3.5-4B")
        self.store=MemoryStore()
        self.recorder=CallRecorder(self.store,str(uuid4()),str(uuid4()),"router",self.settings.router_model,provider="zeus")

    def response(self,url,**kwargs):
        rid=kwargs["json"]["request_id"]
        return httpx.Response(200,request=httpx.Request("POST",url),json={"request_id":rid,"status":"succeeded",
            "verdict":VERDICT,"energy":router_energy(rid),"input_tokens":100,"output_tokens":50,"error":None})

    def test_remote_verdict_energy_tokens_and_partial_total(self):
        with patch('server.remote_router.httpx.post',side_effect=self.response) as post:
            result=route_verdict(self.settings,png(),'image/png',png(),'image/png',self.recorder)
        self.assertEqual(result,VERDICT)
        self.assertEqual(post.call_count,1)
        call=self.store.tables['api_calls'][0]
        self.assertEqual(call['total_tokens'],150)
        self.assertEqual(call['energy_measurement']['scope'],'gpu_board_router_window')
        summary=energy_summary([{**call,'operation':'router'},{'operation':'generate','energy_measurement':telemetry(str(uuid4()))}])
        self.assertEqual(summary['router_energy_wh'],1)
        self.assertEqual(summary['total_gpu_energy_wh'],2)
        self.assertEqual(summary['unmeasured_router_calls'],0)

    def test_timeout_stays_unknown_without_retry(self):
        with patch('server.remote_router.httpx.post',side_effect=httpx.ReadTimeout('timeout')) as post:
            with self.assertRaisesRegex(GeminiError,'UNCONFIRMED'):
                route_verdict(self.settings,png(),'image/png',png(),'image/png',self.recorder)
        self.assertEqual(post.call_count,1)
        self.assertEqual(self.store.tables['api_calls'][0]['status'],'unknown')

    def test_int8_worker_result_is_accepted(self):
        def response(url, **kwargs):
            payload = self.response(url, **kwargs).json()
            payload['router_quantization'] = 'int8'
            return httpx.Response(200, request=httpx.Request('POST', url), json=payload)
        with patch('server.remote_router.httpx.post', side_effect=response):
            result = route_verdict(self.settings, png(), 'image/png', png(), 'image/png', self.recorder)
        self.assertEqual(result, VERDICT)
        self.assertEqual(self.store.tables['api_calls'][0]['status'], 'succeeded')

    def test_explicit_auth_rejection_is_failed_without_retry(self):
        response = httpx.Response(403, json={'detail': 'GPU_AUTH_DENIED'},
            request=httpx.Request('POST', 'https://gpu.test/v1/route'))
        with patch('server.remote_router.httpx.post', return_value=response) as post:
            with self.assertRaisesRegex(GeminiError, 'GPU_AUTH_DENIED'):
                route_verdict(self.settings, png(), 'image/png', png(), 'image/png', self.recorder)
        self.assertEqual(post.call_count, 1)
        self.assertEqual(self.store.tables['api_calls'][0]['status'], 'failed')

    def test_bad_json_is_failed_but_measured(self):
        def response(url,**kwargs):
            rid=kwargs['json']['request_id']
            return httpx.Response(200,request=httpx.Request('POST',url),json={'request_id':rid,'status':'failed',
                'verdict':None,'energy':router_energy(rid),'error':'GPU_ROUTER_BAD_JSON','input_tokens':100,'output_tokens':50})
        with patch('server.remote_router.httpx.post',side_effect=response):
            with self.assertRaisesRegex(GeminiError,'GPU_ROUTER_FAILED'):
                route_verdict(self.settings,png(),'image/png',png(),'image/png',self.recorder)
        self.assertEqual(self.store.tables['api_calls'][0]['status'],'failed')
        self.assertEqual(self.store.tables['api_calls'][0]['energy_measurement']['energy_j'],3600)

    def test_worker_router_replay_and_shared_lock(self):
        import threading
        judge,runner=Mock(),Mock()
        entered,release=threading.Event(),threading.Event()
        def slow(request):
            entered.set()
            if not release.wait(3): raise RuntimeError('test timeout')
            rid=str(request.request_id)
            return {'request_id':rid,'status':'succeeded','verdict':VERDICT,'energy':router_energy(rid)}
        judge.run.side_effect=slow
        headers={'Authorization':'Bearer '+'k'*32}
        rid=str(uuid4())
        body={'request_id':rid,'target_image':'fixture','generated_image':'fixture'}
        with tempfile.TemporaryDirectory() as folder:
            with TestClient(create_app(runner,Path(folder)/'requests.sqlite3','k'*32,judge=judge)) as client:
                t=threading.Thread(target=lambda:client.post('/v1/route',json=body,headers=headers))
                t.start()
                try:
                    self.assertTrue(entered.wait(2))
                    busy=client.post('/v1/generate',json={'request_id':str(uuid4()),'prompt':'bicycle'},headers=headers)
                    self.assertEqual(busy.status_code,429)
                    runner.run.assert_not_called()
                finally:
                    release.set();t.join(3)
                self.assertEqual(client.post('/v1/route',json=body,headers=headers).json()['verdict'],VERDICT)
                self.assertEqual(judge.run.call_count,1)
