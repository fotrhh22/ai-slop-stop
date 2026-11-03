from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from server import profiles, challenges
from server.config import Settings
from server.persistence import Store
from server.tests.test_receipts import png
import base64


def health(gpu='4090'):
    return {'ok': True, 'provider': 'zeus', 'router_ready': True,
        'image_model': 'flux', 'router_model': 'qwen', 'image_revision': 'a'*40,
        'router_revision': 'b'*40, 'gpu_name': 'NVIDIA GeForce RTX '+gpu,
        'gpu_uuid': 'GPU-'+gpu, 'router_quantization': 'int8' if gpu=='4090' else 'bf16'}


def identity(gpu='4090'):
    return {k:v for k,v in health(gpu).items() if k not in ('ok','provider','router_ready')}


class ProfilesTests(TestCase):
    def setUp(self):
        self.settings = Settings(gemini_api_key='gemini-private', image_model='legacy-image',
            router_model='legacy-router', router_provider='gemini', public_app_origin='',
            max_requests_per_minute=100, port=8000, gpu_control_token='control-'+'x'*32,
            gpu_profiles={'open-'+gpu:{'key':gpu+'k'*32,'image_model':'flux','router_model':'qwen'} for gpu in ('4090','5090')})

    def test_probe_authenticates_and_checks_gpu_models_revisions(self):
        response = Mock(); response.json.return_value = health()
        with patch.object(profiles.httpx, 'get', return_value=response) as get:
            self.assertEqual(profiles.probe(self.settings, 'open-4090', 'pod1234'), identity())
            self.assertEqual(get.call_args.args[0], 'https://pod1234-8000.proxy.runpod.net/health')
            self.assertEqual(get.call_args.kwargs['headers']['Authorization'], 'Bearer '+self.settings.gpu_profiles['open-4090']['key'])
            self.assertFalse(get.call_args.kwargs['follow_redirects'])
            for field, value in [('gpu_uuid',''),('image_model','wrong'),('image_revision','main'),
                                 ('gpu_name','RTX 5090'),('router_quantization','bf16')]:
                response.json.return_value = {**health(),field:value}
                with self.assertRaises(HTTPException): profiles.probe(self.settings,'open-4090','pod1234')

    def test_invalid_pod_cannot_contact_arbitrary_url(self):
        with patch.object(profiles.httpx,'get') as get:
            with self.assertRaises(HTTPException): profiles.probe(self.settings,'open-4090','localhost/redirect')
            get.assert_not_called()

    def test_catalog_and_registration_do_not_expose_keys(self):
        app=FastAPI();app.include_router(profiles.router)
        with patch.object(profiles,'load_settings',return_value=self.settings), \
             patch.object(profiles,'runtime',return_value={}), patch.object(profiles,'Store') as store, \
             patch.object(profiles,'probe',return_value=identity()) as probe, TestClient(app) as client:
            catalog=client.get('/api/model-profiles').json()
            self.assertEqual(catalog['default_profile'],'gemini')
            self.assertEqual([p['ready'] for p in catalog['profiles']],[True,True,True])
            self.assertNotIn('private',str(catalog));self.assertNotIn('kkkk',str(catalog))
            body={'runtime_id':str(uuid4()),'profile_id':'open-4090','pod_id':'pod1234'}
            self.assertEqual(client.post('/api/gpu-control/register',json=body).status_code,403)
            probe.assert_not_called()
            response=client.post('/api/gpu-control/register',json=body,headers={'X-Gpu-Control-Token':self.settings.gpu_control_token})
            self.assertEqual(response.status_code,200)
            self.assertEqual(store.return_value.request.call_args.kwargs['json']['p_identity'],identity())

    def test_stopped_unregistered_profiles_stay_disabled(self):
        with patch.object(profiles,'load_settings',return_value=self.settings), \
             patch.object(profiles,'Store'),patch.object(profiles,'runtime',side_effect=HTTPException(503,'not ready')):
            self.assertEqual([p['ready'] for p in profiles.catalog()['profiles']],[True,False,False])

    def test_default_gemini_clears_gpu_route_and_has_no_wh(self):
        selected=profiles.select(replace(self.settings,image_provider='zeus',gpu_api_url='old-url',gpu_api_key='old-key'),Mock(),'gemini')
        self.assertEqual(selected.image_provider,'gemini');self.assertEqual(selected.router_provider,'gemini')
        self.assertEqual(selected.gpu_api_url,'');self.assertEqual(selected.gpu_api_key,'')
        self.assertEqual(self.settings.image_model,'legacy-image')

    def test_open_profile_inherits_clip_observe_setting(self):
        settings=replace(self.settings,clip_mode='observe')
        row={'id':str(uuid4()),'pod_id':'pod4090','identity':identity()}
        with patch.object(profiles,'runtime',return_value=row):
            selected=profiles.select(settings,Mock(),'open-4090')
        self.assertEqual(selected.clip_mode,'observe')
        self.assertEqual(selected.router_provider,'zeus')

    def test_existing_session_remains_on_original_runtime_after_new_binding(self):
        original=str(uuid4());new=str(uuid4());store=Mock()
        def rows(table,**filters):
            if table=='gpu_profile_bindings':return [{'runtime_id':new}]
            return [{'id':filters['id'][3:],'pod_id':'oldpod1' if filters['id']=='eq.'+original else 'newpod1','identity':identity()}]
        store.rows.side_effect=rows
        with patch.object(profiles,'probe',return_value=identity()):
            frozen=profiles.select(self.settings,store,'open-4090',{'runtime_id':original,'gpu_identity':identity()})
            fresh=profiles.select(self.settings,store,'open-4090')
        self.assertIn('oldpod1-',frozen.gpu_api_url);self.assertIn('newpod1-',fresh.gpu_api_url)
        self.assertEqual(frozen.runtime_id,original)

    def test_parallel_profile_selection_has_no_global_provider_or_key_mutation(self):
        # Runtime lookup itself is deterministic; execute both settings construction paths concurrently.
        rows={p:{'id':str(uuid4()),'pod_id':'pod'+p[-4:],'identity':identity(p[-4:])} for p in profiles.OPEN_IDS}
        with patch.object(profiles,'runtime',side_effect=lambda s,base,p,r=None:rows[p]),ThreadPoolExecutor(2) as pool:
            a,b=list(pool.map(lambda p:profiles.select(self.settings,Mock(),p),profiles.OPEN_IDS))
        self.assertNotEqual(a.gpu_api_key,b.gpu_api_key)
        self.assertEqual(a.gpu_identity['gpu_name'],'NVIDIA GeForce RTX 4090')
        self.assertEqual(b.gpu_identity['gpu_name'],'NVIDIA GeForce RTX 5090')
        self.assertEqual(self.settings.profile_id,'')

    def test_completed_receipt_authorization_does_not_require_running_pod(self):
        snapshot={'profile_id':'open-4090','runtime_id':str(uuid4()),'gpu_identity':identity(),
            'image_provider':'zeus','router_provider':'zeus','image_model':'flux','router_model':'qwen'}
        store=Mock();store.authorize.return_value={'profile_snapshot':snapshot}
        with patch.object(challenges,'load_settings',return_value=self.settings),patch.object(challenges,'Store',return_value=store), \
             patch.object(profiles,'select',side_effect=AssertionError('must not contact GPU')):
            selected,_,_=challenges.session(uuid4(),'token')
            self.assertEqual(selected.image_model,'flux')

    def test_measurement_from_different_runtime_is_rejected(self):
        settings=replace(self.settings,gpu_identity=identity())
        item=SimpleNamespace(gpu_uuid='GPU-5090',model_revision='a'*40)
        with self.assertRaises(ValueError):profiles.measurement_matches(settings,item,'generate')

    def test_http_challenge_creation_persists_separate_server_selected_profiles(self):
        saved=[]
        class CaptureStore:
            create_challenge=Store.create_challenge
            def __init__(self,settings):self.settings=settings
            def upload(self,*args):pass
            def insert(self,table,data):saved.append(data);return data
        rows={p:{'id':str(uuid4()),'pod_id':'pod'+p[-4:],'identity':identity(p[-4:])} for p in profiles.OPEN_IDS}
        app=FastAPI();app.include_router(challenges.router)
        target='data:image/png;base64,'+base64.b64encode(png()).decode()
        with patch.object(challenges,'load_settings',return_value=self.settings), \
             patch.object(challenges,'Store',side_effect=CaptureStore), \
             patch.object(profiles,'runtime',side_effect=lambda s,base,p,r=None:rows[p]),TestClient(app) as client:
            for profile_id in ('gemini','open-4090','open-5090'):
                response=client.post('/api/challenges',json={'target_image':target,'profile_id':profile_id})
                self.assertEqual(response.status_code,200,response.text)
                self.assertEqual(response.json()['profile']['profile_id'],profile_id)
                self.assertNotIn('kkkk',response.text);self.assertNotIn('proxy.runpod',response.text)
            response=client.post('/api/challenges',json={'target_image':target,'profile_id':'http://evil.test'})
            self.assertEqual(response.status_code,422)
        self.assertEqual([row['profile_snapshot']['image_provider'] for row in saved],['gemini','zeus','zeus'])
        self.assertNotEqual(saved[1]['profile_snapshot']['runtime_id'],saved[2]['profile_snapshot']['runtime_id'])
        self.assertEqual(self.settings.profile_id,'')
