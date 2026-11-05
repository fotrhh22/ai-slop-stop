"""CPU-only checks for the storage guard and private SQLite rescue archive."""
import json
from pathlib import Path
import sqlite3
import stat
import tarfile
import tempfile
import unittest
from unittest.mock import patch

from gpu_worker import volume


class VolumeTests(unittest.TestCase):
    def test_shared_install_preserves_runtime_and_verifies_extensions(self):
        with patch.object(volume.subprocess, 'run') as run:
            volume.install_dependencies(Path('/test/venv/bin/python'))
        calls = run.call_args_list
        self.assertEqual(len(calls), 3)
        self.assertIn('-c', calls[0].args[0])
        self.assertTrue(calls[0].args[0][-1].endswith('requirements.lock.txt'))
        self.assertIn('--no-build-isolation', calls[1].args[0])
        self.assertTrue(calls[1].args[0][-1].endswith('kernel-requirements.lock.txt'))
        self.assertEqual(calls[2].args[0], ['/test/venv/bin/python', '-m', 'gpu_worker.kernels'])
        self.assertTrue(all(c.kwargs['check'] for c in calls))

    def test_failed_extension_install_does_not_report_success(self):
        import subprocess
        with patch.object(volume.subprocess, 'run', side_effect=[None, subprocess.CalledProcessError(1, 'pip')]) as run:
            with self.assertRaises(subprocess.CalledProcessError):
                volume.install_dependencies(Path('/test/python'))
        self.assertEqual(run.call_count, 2)

    def test_refuses_workspace_on_container_root(self):
        result = json.dumps({'filesystems': [{'target': '/', 'source': 'overlay', 'fstype': 'overlay'}]})
        with patch.object(volume.subprocess, 'check_output', return_value=result):
            with self.assertRaisesRegex(RuntimeError, 'Installation refused'):
                volume.check_mount(Path('/workspace'))

    def test_accepts_separate_workspace_mount(self):
        result = json.dumps({'filesystems': [{'target': '/workspace', 'source': 'volume', 'fstype': 'fuse'}]})
        with patch.object(volume.subprocess, 'check_output', return_value=result):
            volume.check_mount(Path('/workspace'))

    def test_backup_preserves_ledger_secret_and_private_mode_without_cache(self):
        with tempfile.TemporaryDirectory() as temp:
            workspace = Path(temp)
            root = workspace / 'ai-slop-stop'
            root.mkdir()
            (root / 'worker.env').write_text('GPU_API_KEY=private-test-key\n')
            (workspace / 'ai-slop').mkdir()
            ledger = workspace / 'ai-slop/requests.sqlite3'
            with sqlite3.connect(ledger) as db:
                db.execute('create table requests(id text, result text)')
                db.execute('insert into requests values(?,?)', ('id1', 'running'))
            output = workspace / 'rescue.tar.gz'
            with patch.object(volume, 'ROOT', root), patch.object(volume, 'WORKSPACE', workspace):
                volume.backup(output)
                with self.assertRaises(FileExistsError):
                    volume.backup(output)
            self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)
            with tarfile.open(output) as archive:
                self.assertEqual(archive.extractfile('ai-slop-stop/worker.env').read(), b'GPU_API_KEY=private-test-key\n')
                self.assertFalse(any(n.startswith('huggingface/') for n in archive.getnames()))
                snapshot = workspace / 'snapshot.sqlite3'
                snapshot.write_bytes(archive.extractfile('ai-slop/requests.sqlite3').read())
            with sqlite3.connect(snapshot) as db:
                self.assertEqual(db.execute('select * from requests').fetchall(), [('id1', 'running')])


if __name__ == '__main__':
    unittest.main()
