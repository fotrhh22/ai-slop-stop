from dataclasses import replace
import unittest
from unittest.mock import MagicMock,patch
from uuid import uuid4
from fastapi import FastAPI
from fastapi.testclient import TestClient
from server import campaign
from server.config import load_settings
from server.persistence import PersistenceError


class CampaignTests(unittest.TestCase):
    def setUp(self):
        self.store=MagicMock()
        self.settings=replace(load_settings(),bike_worker_token='b'*32,print_station_id='booth-1')
        self.patches=[patch.object(campaign,'load_settings',return_value=self.settings),patch.object(campaign,'Store',return_value=self.store)]
        for p in self.patches:p.start();self.addCleanup(p.stop)
        app=FastAPI();app.include_router(campaign.router);self.client=TestClient(app)
        self.headers={'X-Bike-Worker-Token':'b'*32}

    def test_public_and_print_token_cannot_read_queue(self):
        self.assertEqual(self.client.get('/api/campaign/entries').status_code,403)
        self.assertEqual(self.client.get('/api/campaign/entries',headers={'X-Print-Worker-Token':'b'*32}).status_code,403)
        self.store.rows.assert_not_called()

    def test_queue_is_station_scoped(self):
        self.store.rows.return_value=[]
        self.assertEqual(self.client.get('/api/campaign/entries',headers=self.headers).status_code,200)
        self.assertEqual(self.store.rows.call_args.kwargs['station_id'],'eq.booth-1')
        self.assertEqual(self.store.rows.call_args.kwargs['order'],'created_at.desc,id.desc')

    def test_delete_requires_station_auth_and_uses_server_station(self):
        entry=str(uuid4())
        self.assertEqual(self.client.post('/api/campaign/entries/'+entry+'/delete').status_code,403)
        self.store.request.assert_not_called()
        self.store.request.return_value.json.return_value=True
        with patch('server.cleanup.drain_storage_jobs') as drain:
            r=self.client.post('/api/campaign/entries/'+entry+'/delete',headers=self.headers)
        self.assertEqual(r.status_code,200,r.text)
        self.assertTrue(r.json()['deleted'])
        self.assertEqual(self.store.request.call_args.kwargs['json'],{'p_entry_id':entry,'p_station_id':'booth-1'})
        drain.assert_called_once_with(self.store)

    def test_queue_remains_readable_before_reference_migration(self):
        self.store.rows.side_effect=[PersistenceError('SUPABASE_SCHEMA_UPDATE_REQUIRED'),[]]
        self.assertEqual(self.client.get('/api/campaign/entries',headers=self.headers).status_code,200)
        self.assertNotIn('reference_target',self.store.rows.call_args.kwargs['select'])

    def test_end_uses_atomic_rpc_without_trusting_scaled_values(self):
        sid,entry=str(uuid4()),str(uuid4())
        payload={'session_id':sid,'entry_id':entry,'raw_wh':0.0005,'elapsed_s':20,'measured_s':10,
          'gap_count':0,'mode':'usb','end_reason':'manual_stop','started_at':'2026-11-03T00:00:00Z','ended_at':'2026-11-03T00:00:20Z'}
        self.store.request.return_value.json.return_value=[{'id':sid,'raw_energy_wh':0.0005,'campaign_energy_wh':0.05,'progress_pct':None}]
        r=self.client.post('/api/campaign/bike-ends',json=payload,headers=self.headers)
        self.assertEqual(r.status_code,200,r.text)
        self.assertNotIn('raw_energy_wh',r.json()['result'])
        params=self.store.request.call_args.kwargs['json']
        self.assertEqual(params['p_station_id'],'booth-1')
        self.assertEqual(params['p_raw_wh'],0.0005)
        self.assertEqual(self.client.post('/api/campaign/bike-ends',json={**payload,'campaign_energy_wh':500},headers=self.headers).status_code,422)
        self.assertEqual(self.client.post('/api/campaign/bike-ends',json={**payload,'mode':'demo'},headers=self.headers).status_code,422)


if __name__=='__main__':unittest.main()
