"""Shared-service isolation and lifecycle checks, mock inference only."""
import os
import tempfile
import time
import unittest
from unittest.mock import patch
from fastapi import FastAPI
from fastapi.testclient import TestClient
from server.poc_integration import create_poc_app

class IntegrationTests(unittest.TestCase):
    def test_disabled_does_not_require_poc_configuration(self):
        with patch.dict(os.environ, {"POC_ENABLED": "false"}):
            self.assertIsNone(create_poc_app())

    def test_runpod_requires_namespaced_credentials(self):
        with patch.dict(os.environ, {"POC_ENABLED": "true", "POC_GPU_MODE": "runpod",
                                     "GPU_API_URL": "https://campaign.example", "GPU_API_KEY": "c"*40}, clear=True):
            with self.assertRaisesRegex(RuntimeError, 'POC_GPU_API_URL'):
                create_poc_app()

    def test_mounted_flow_starts_queue_preserves_campaign_and_checks_origin(self):
        from contextlib import asynccontextmanager
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {
            "POC_ENABLED": "true", "POC_GPU_MODE": "mock", "POC_TEST_MODE": "true",
            "POC_DB_PATH": temp+"/poc.sqlite3", "POC_MEDIA_PATH": temp+"/images",
            "POC_ALLOWED_ORIGINS": "https://study.example", "POC_MOCK_DELAY": "0.001",
            "DATABASE_URL": "postgresql://campaign-must-not-be-used", "POC_DATABASE_URL": "",
        }):
            sub = create_poc_app()
            self.assertEqual(sub.state.store.url, '')
            @asynccontextmanager
            async def lifespan(app):
                async with sub.router.lifespan_context(sub):
                    yield
            parent = FastAPI(lifespan=lifespan)
            parent.mount('/poc', sub)
            @parent.get('/health')
            def health():return {'campaign': True}
            with TestClient(parent) as client:
                self.assertEqual(client.get('/health').json(), {'campaign': True})
                self.assertTrue(client.get('/poc/health').json()['ok'])
                self.assertEqual(client.get('/poc/content/target-a.png').status_code, 200)
                self.assertEqual(client.post('/poc/api/poc/sessions', json={'consent': True}).status_code, 403)
                headers={'X-Poc-Request': '1', 'Origin': 'https://evil.example'}
                self.assertEqual(client.post('/poc/api/poc/sessions', json={'consent': True}, headers=headers).status_code,403)
                headers['Origin']='https://study.example'
                self.assertEqual(client.post('/poc/api/poc/sessions', json={'consent': True}, headers=headers).status_code,200)
                self.assertIn('aimi_poc_session', client.cookies)
                pre={'ai_frequency':2,'image_experience':1,'education':1,'error_strategy':1,'difficulties':[1], 'writing':[2]*4}
                client.put('/poc/api/poc/surveys/pre',json=pre,headers=headers).raise_for_status()
                client.post('/poc/api/poc/assessments/pre/attempts',json={'prompt':'연습실패'},headers=headers).raise_for_status()
                for _ in range(100):
                    state=client.get('/poc/api/poc/session').json()
                    if state['state']=='pre_result':break
                    time.sleep(.02)
                self.assertEqual(state['state'],'pre_result')
                self.assertEqual(state['jobs'][0]['verdict'],'FAIL')
            self.assertTrue(sub.state.worker.stopping)

    def test_namespaced_gpu_wins(self):
        from aimi_poc.api.gpu import GPUClient
        with patch.dict(os.environ, {'POC_GPU_API_URL':'https://study.example','POC_GPU_API_KEY':'p'*40,
                                     'GPU_API_URL':'https://campaign.example','GPU_API_KEY':'c'*40}):
            gpu=GPUClient('mock',{})
            self.assertEqual(gpu.url,'https://study.example')
            self.assertEqual(gpu.key,'p'*40)
