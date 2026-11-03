from dataclasses import replace
from unittest import TestCase
from unittest.mock import Mock, patch
from uuid import uuid4

from fastapi.testclient import TestClient
from server import cleanup, main
from server.config import Settings


class CleanupTests(TestCase):
    def setUp(self):
        self.settings=Settings(gemini_api_key='',image_model='i',router_model='r',router_provider='gemini',
            public_app_origin='',max_requests_per_minute=20,port=8000)
        self.store=Mock()
        self.store.request.return_value.json.return_value=1
        self.job={'id':str(uuid4()),'bucket':'challenge-targets','object_path':str(uuid4())+'/target'}
        self.store.rows.return_value=[self.job]

    def test_deletes_bytes_via_storage_api_before_acknowledging_queue(self):
        with patch.object(cleanup,'Store',return_value=self.store):
            result=cleanup.clean_once(self.settings)
        self.assertEqual(result,{'sessions_deleted':1,'storage_jobs_deleted':1})
        calls=self.store.request.call_args_list
        self.assertEqual(calls[0].kwargs['json'],{'p_retention_hours':24,'p_limit':100})
        self.assertEqual(calls[1].args,('DELETE','/storage/v1/object/challenge-targets'))
        self.assertEqual(calls[1].kwargs['json'],{'prefixes':[self.job['object_path']]})
        self.assertEqual(calls[2].args,('DELETE','/rest/v1/storage_cleanup_jobs'))

    def test_storage_failure_keeps_job_for_next_sweep(self):
        self.store.request.side_effect=[Mock(json=lambda:1),RuntimeError('temporary storage outage')]
        with patch.object(cleanup,'Store',return_value=self.store):
            self.assertEqual(cleanup.clean_once(self.settings)['storage_jobs_deleted'],0)
        self.assertEqual(self.store.request.call_count,2)
        self.assertNotIn('/rest/v1/storage_cleanup_jobs',[c.args[1] for c in self.store.request.call_args_list])

    def test_lost_ack_retries_same_object_without_new_session_deletion_job(self):
        self.store.request.side_effect=[Mock(json=lambda:1),Mock(),RuntimeError('lost ack'),
                                       Mock(json=lambda:0),Mock(),Mock()]
        with patch.object(cleanup,'Store',return_value=self.store):
            self.assertEqual(cleanup.clean_once(self.settings)['storage_jobs_deleted'],0)
            self.assertEqual(cleanup.clean_once(self.settings)['storage_jobs_deleted'],1)
        paths=[c.kwargs['json']['prefixes'] for c in self.store.request.call_args_list if c.args[1].startswith('/storage/')]
        self.assertEqual(paths,[[self.job['object_path']]]*2)

    def test_arbitrary_bucket_and_path_are_never_deleted(self):
        self.store.rows.return_value=[{**self.job,'bucket':'private-secrets'},
            {**self.job,'object_path':'../other/file'},{**self.job,'object_path':str(uuid4())+'/'}]
        with patch.object(cleanup,'Store',return_value=self.store):
            self.assertEqual(cleanup.clean_once(self.settings)['storage_jobs_deleted'],0)
        self.assertEqual(self.store.request.call_count,1)

    def test_startup_runs_cleanup_and_shutdown_cancels_loop(self):
        settings=replace(self.settings,supabase_url='https://test.invalid',supabase_secret_key='fake')
        import asyncio
        from threading import Event
        started=Event();ended=Event()
        async def fake_run(config):
            self.assertIs(config,settings);started.set()
            try:await asyncio.Event().wait()
            finally:ended.set()
        with patch.object(main,'settings',settings),patch.object(cleanup,'run',side_effect=fake_run):
            with TestClient(main.app):self.assertTrue(started.wait(1))
        self.assertTrue(ended.is_set())

    def test_explicitly_disabled_cleanup_does_not_start(self):
        settings=replace(self.settings,session_cleanup_enabled=False,supabase_url='https://test.invalid',supabase_secret_key='fake')
        with patch.object(main,'settings',settings),patch.object(cleanup,'run') as run:
            with TestClient(main.app):pass
            run.assert_not_called()
