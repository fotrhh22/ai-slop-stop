import unittest
from dataclasses import replace
from unittest.mock import patch,MagicMock
from uuid import uuid4
from fastapi import FastAPI
from fastapi.testclient import TestClient
from server import printing,challenges
from server.config import load_settings

class PrintingTests(unittest.TestCase):
    def setUp(self):
        settings=replace(load_settings(),print_pairing_token='p'*32,print_worker_token='w'*32)
        self.config=patch.object(printing,'load_settings',return_value=settings);self.config.start()
        self.addCleanup(self.config.stop)
        self.store=MagicMock();self.store.rows.return_value=[]
        self.store_patch=patch.object(printing,'Store',return_value=self.store);self.store_patch.start()
        self.addCleanup(self.store_patch.stop)
        app=FastAPI();app.include_router(printing.router);app.include_router(challenges.router)
        self.client=TestClient(app)
    def test_pairing_does_not_expose_worker_secret(self):
        r=self.client.post('/api/printing/pair',headers={'X-Print-Station-Token':'p'*32})
        self.assertEqual(r.status_code,200)
        self.assertEqual(r.json(),{'station_id':'booth-1'})
        self.assertNotIn('w'*32,r.text)
    def test_pairing_secret_cannot_claim_jobs(self):
        r=self.client.post('/api/printing/claim',headers={'X-Print-Worker-Token':'p'*32})
        self.assertEqual(r.status_code,403);self.store.request.assert_not_called()
    def test_public_cannot_claim(self):
        self.assertEqual(self.client.post('/api/printing/claim').status_code,403)
    def test_claim_uses_database_atomic_rpc(self):
        job={'id':str(uuid4()),'status':'RENDERING'}
        self.store.request.return_value.json.return_value=[job]
        r=self.client.post('/api/printing/claim',headers={'X-Print-Worker-Token':'w'*32})
        self.assertEqual(r.json()['job'],job)
        self.store.request.assert_called_once_with('POST','/rest/v1/rpc/claim_print_job',json={'p_station_id':'booth-1'})
    def test_sent_requires_spool_job_id(self):
        r=self.client.post('/api/printing/jobs/'+str(uuid4())+'/advance',
            headers={'X-Print-Worker-Token':'w'*32},json={'lease_token':str(uuid4()),'status':'SENT'})
        self.assertEqual(r.status_code,400);self.store.request.assert_not_called()
    def test_expired_or_wrong_lease_cannot_fetch_image(self):
        r=self.client.post('/api/printing/jobs/'+str(uuid4())+'/payload',
            headers={'X-Print-Worker-Token':'w'*32},json={'lease_token':str(uuid4())})
        self.assertEqual(r.status_code,409);self.store.receipt.assert_not_called()
    def test_unverified_receipt_cannot_print(self):
        rid=str(uuid4())
        self.store.rows.return_value=[{'status':'RENDERING','challenge_id':str(uuid4()),'receipt_id':rid}]
        self.store.receipt.return_value={'id':rid,'verified':False}
        r=self.client.post('/api/printing/jobs/'+str(uuid4())+'/payload',
            headers={'X-Print-Worker-Token':'w'*32},json={'lease_token':str(uuid4())})
        self.assertEqual(r.status_code,409);self.store.download.assert_not_called()
    def test_ongoing_job_cannot_be_reprinted(self):
        self.store.rows.return_value=[{'status':'SENDING'}]
        r=self.client.post('/api/printing/jobs/'+str(uuid4())+'/reprint',
            headers={'X-Print-Worker-Token':'w'*32},json={'request_id':str(uuid4())})
        self.assertEqual(r.status_code,409);self.store.insert.assert_not_called()
    def test_unpaired_challenge_has_no_print_query(self):
        with patch.object(challenges,'session',return_value=(None,self.store,{})):
            r=self.client.get('/api/challenges/'+str(uuid4())+'/print-job')
        self.assertEqual(r.json(),{'enabled':False,'job':None})
        self.store.rows.assert_not_called()

if __name__=='__main__':unittest.main()
