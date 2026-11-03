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
