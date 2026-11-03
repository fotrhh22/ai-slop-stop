import io
import importlib.util
import json
from pathlib import Path
import sqlite3
import tarfile
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from gpu_worker import pod_setup

spec = importlib.util.spec_from_file_location('runpod_controller', Path(__file__).with_name('runpod.py'))
controller = importlib.util.module_from_spec(spec)
spec.loader.exec_module(controller)


class ControllerTests(unittest.TestCase):
    def test_parses_console_command_without_executing_shell(self):
        self.assertEqual(controller.parse_connection('ssh root@1.2.3.4 -p 12345 -i ~/.ssh/id_ed25519'),
                         {'target': 'root@1.2.3.4', 'port': 12345})
        with self.assertRaises(ValueError):
            controller.parse_connection('echo not-ssh')

    def test_healthy_worker_is_never_restarted_or_overwritten(self):
        pod = Mock()
        pod.helper.return_value = {'healthy': True, 'pod_id': 'test-pod'}
        with patch.object(controller, 'backup'), patch.object(controller, 'public_health'):
            controller.up(pod, {}, SimpleNamespace(backup=None, timeout=5))
        self.assertEqual(pod.helper.call_args_list[0].args, ('inspect',))
        self.assertEqual(pod.helper.call_count, 1)
        pod.scp.assert_not_called()
        pod.run.assert_not_called()

    def test_busy_or_wrong_auth_port_is_not_replaced(self):
        pod = Mock()
        pod.helper.return_value = {'healthy': False, 'port_open': True}
        with self.assertRaisesRegex(RuntimeError, 'no restart'):
            controller.up(pod, {}, SimpleNamespace(backup=None, timeout=5))
        pod.scp.assert_not_called()
        pod.run.assert_not_called()

    def test_stale_setup_state_does_not_prevent_recovery(self):
        pod = Mock()
        states = iter([
            {'healthy': False, 'port_open': False, 'has_config': False,
             'stage': 'loading_models', 'setup_running': False},
            {'healthy': True, 'stage': 'ready', 'pod_id': 'test-pod'},
        ])
        pod.helper.side_effect = lambda action, *args: next(states) if action == 'inspect' else {'setup_pid': 123}
        with patch.object(controller.subprocess, 'check_output', return_value=b'code'), \
             patch.object(controller, 'backup'), patch.object(controller, 'public_health'):
            controller.up(pod, {}, SimpleNamespace(backup=None, timeout=5))
        self.assertIn('launch', [call.args[0] for call in pod.helper.call_args_list])


class RestoreTests(unittest.TestCase):
    def test_restore_missing_files_and_preserve_existing_secret_and_ledger(self):
        with tempfile.TemporaryDirectory() as temp:
            workspace = Path(temp)
            root = workspace / 'ai-slop-stop'
            root.mkdir()
            source = workspace / 'source.sqlite3'
            with sqlite3.connect(source) as db:
                db.execute('create table requests(id text)')
                db.execute("insert into requests values('saved-request')")
            archive = workspace / 'rescue.tar.gz'
            secret = b'GPU_API_KEY=private-test-key\n'
            with tarfile.open(archive, 'w:gz') as tar:
                info = tarfile.TarInfo('ai-slop-stop/worker.env')
                info.size = len(secret)
                tar.addfile(info, io.BytesIO(secret))
                tar.add(source, arcname='ai-slop/requests.sqlite3')
            with patch.object(pod_setup, 'ROOT', root), patch.object(pod_setup, 'WORKSPACE', workspace):
                pod_setup.restore(archive)
                (root / 'worker.env').write_text('GPU_API_KEY=current-key\n')
                with sqlite3.connect(workspace / 'ai-slop/requests.sqlite3') as db:
                    db.execute("insert into requests values('new-request')")
                second = pod_setup.restore(archive)
            self.assertEqual((root / 'worker.env').read_text(), 'GPU_API_KEY=current-key\n')
            self.assertEqual(len(second['preserved_existing']), 2)
            with sqlite3.connect(workspace / 'ai-slop/requests.sqlite3') as db:
                self.assertEqual(db.execute('select count(*) from requests').fetchone()[0], 2)


if __name__ == '__main__':
    unittest.main()
