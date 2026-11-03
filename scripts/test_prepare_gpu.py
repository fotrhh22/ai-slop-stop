import tempfile
import time
import os
import json
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

from scripts import prepare_gpu as prepare
from scripts import runpod as controller


def pod(status='RUNNING',gpu='4090'):
    return {'id':'pod1234','gpu':{'id':'NVIDIA GeForce RTX '+gpu,'count':1},
        'ports':['22/tcp','8000/http','8888/http'],'status':status,
        'actions':['start'] if status=='EXITED' else ['stop'],
        'ssh':{'direct':{'host':'1.2.3.4','port':12345}}}


class PrepareTests(TestCase):
    def test_start_once_then_discover_new_ssh_address(self):
        api=Mock();api.get.side_effect=[pod('EXITED'),pod()]
        result=prepare.wait_for_pod(api,'pod1234','4090',time.monotonic()+10,Mock())
        api.start.assert_called_once_with('pod1234')
        self.assertEqual(result,{'target':'root@1.2.3.4','port':12345})

    def test_running_pod_never_restarted(self):
        api=Mock();api.get.return_value=pod()
        prepare.wait_for_pod(api,'pod1234','4090',time.monotonic()+10,Mock())
        api.start.assert_not_called()

    def test_wrong_gpu_and_missing_ports_fail_before_start(self):
        for value in [pod('EXITED','5090'),{**pod('EXITED'),'ports':['22/tcp']}]:
            api=Mock();api.get.return_value=value
            with self.assertRaises(RuntimeError):prepare.wait_for_pod(api,'pod1234','4090',time.monotonic()+10,Mock())
            api.start.assert_not_called()

    def test_unknown_start_response_is_not_retried(self):
        api=Mock();api.get.return_value=pod('EXITED');api.start.side_effect=RuntimeError('response lost')
        with self.assertRaises(RuntimeError):prepare.wait_for_pod(api,'pod1234','4090',time.monotonic()+10,Mock())
        api.start.assert_called_once()

    def test_api_uses_official_v2_start_action(self):
        with patch.object(prepare,'call') as call:
            api=prepare.RunPodAPI('private');api.start('pod1234')
            call.assert_called_once_with('https://api.runpod.io/v2/pods/pod1234/action','private',{'action':'start'})

    def test_bootstrap_secret_is_only_in_ssh_stdin(self):
        pod=Mock();key='private-'+'x'*32
        controller.ensure_api_key(pod,key)
        self.assertNotIn(key,pod.run.call_args.args[0])
        self.assertIn(key.encode(),pod.run.call_args.kwargs['data'])

    def test_operator_secret_file_must_be_private(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'operator.env';path.write_text('RUNPOD_API_KEY=fake')
            path.chmod(0o644)
            with self.assertRaises(RuntimeError):prepare.read_env(path)

    def test_full_preparation_registers_only_after_setup_and_keeps_profiles_separate(self):
        with tempfile.TemporaryDirectory() as temp:
            args=SimpleNamespace(profile='open-4090',env=str(Path(temp)/'absent.env'),pod_id='pod1234',
                timeout=1,identity='~/.ssh/runpod_esg',known_hosts=None,backup=None)
            env={'RUNPOD_API_KEY':'fake-runpod','GPU_CONTROL_TOKEN':'c'*32,
                'GPU_CONTROL_API_BASE':'https://render.test','GPU_4090_API_KEY':'k'*32}
            with patch.dict(os.environ,env),patch.object(prepare,'ROOT',Path(temp)), \
                 patch.object(controller,'LOCAL',Path(temp)),patch.object(controller,'PROFILE',Path(temp)/'old.json'), \
                 patch.object(prepare,'wait_for_pod',return_value={'target':'root@1.2.3.4','port':12345}), \
                 patch.object(controller,'Pod') as pod,patch.object(controller,'up') as up, \
                 patch.object(prepare,'call') as register:
                def registered(*a,**kw):
                    up.assert_called_once()
                    return {'ready':True,'profile_id':'open-4090'}
                register.side_effect=registered
                prepare.prepare(args)
                state=json.loads((Path(temp)/'.runpod/open-4090/preparation.json').read_text())
                self.assertEqual(state['stage'],'ready')
                body=register.call_args.args[2]
                self.assertEqual(body['profile_id'],'open-4090');self.assertNotIn('key',str(body))
                pod.return_value.run.assert_called_once_with('true')

    def test_setup_failure_never_publishes_ready_or_registers(self):
        with tempfile.TemporaryDirectory() as temp:
            args=SimpleNamespace(profile='open-4090',env=str(Path(temp)/'none'),pod_id='pod1234',
                timeout=1,identity='~/.ssh/runpod_esg',known_hosts=None,backup=None)
            env={'RUNPOD_API_KEY':'fake-runpod','GPU_CONTROL_TOKEN':'c'*32,
                'GPU_CONTROL_API_BASE':'https://render.test','GPU_4090_API_KEY':'k'*32}
            with patch.dict(os.environ,env),patch.object(prepare,'ROOT',Path(temp)), \
                 patch.object(controller,'LOCAL',Path(temp)),patch.object(controller,'PROFILE',Path(temp)/'old.json'), \
                 patch.object(prepare,'wait_for_pod',return_value={'target':'root@1.2.3.4','port':12345}), \
                 patch.object(controller,'Pod'),patch.object(controller,'up',side_effect=RuntimeError('install failed')), \
                 patch.object(prepare,'call') as register:
                with self.assertRaises(RuntimeError):prepare.prepare(args)
                register.assert_not_called()
                state=json.loads((Path(temp)/'.runpod/open-4090/preparation.json').read_text())
                self.assertEqual(state['stage'],'failed_requires_operator_check')
