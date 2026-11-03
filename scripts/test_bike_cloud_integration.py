"""Cross-repository HTTP + SQL check. Uses ONLY disposable localhost:55437 DB.

Run after loading migrations into that local test DB. No Gemini/GPU/CUPS calls.
"""
from dataclasses import replace
import json
import base64
import os
import re
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from uuid import uuid4
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
sys.path.insert(0,str(Path(os.environ.get('BIKE_TEST_REPO',str(ROOT.parent/'bike-integration')))))
from server import campaign,printing
from server.config import load_settings
from server.persistence import Store,digest
from server.tests.test_receipts import png
from printer_bridge.renderer import render_receipt
from bike.app import create_app
from bike.protocol import Reading


def sql(statement):
    return subprocess.check_output(['psql','-h','127.0.0.1','-p','55437','-U','postgres','-d','postgres',
        '-X','-A','-t','-v','ON_ERROR_STOP=1','-c',statement],text=True).strip()


def literal(value):
    if value is None:return 'null'
    if isinstance(value,(float,int)):return str(value)
    return "'"+str(value).replace("'","''")+"'"


class SQLStore(Store):
    def __init__(self,entries):self.entries=entries
    def rows(self,table,**filters):
        assert table in ('campaign_entries','bike_results','print_jobs','successes','attempts','api_calls')
        clauses=[]
        for key,value in filters.items():
            if key in ('select','order','offset','limit'):continue
            assert re.fullmatch('[a-z_]+',key)
            if value.startswith('eq.'):clauses.append(key+'='+literal(value[3:]))
            elif value=='in.(waiting,measuring)':clauses.append("state in ('waiting','measuring')")
            else:raise AssertionError(value)
        query='select * from public.'+table+(' where '+' and '.join(clauses) if clauses else '')
        if filters.get('order'):
            query+=' order by '+','.join(p.replace('.asc',' asc').replace('.desc',' desc') for p in filters['order'].split(','))
        if filters.get('limit'):query+=' limit '+str(int(filters['limit']))
        if filters.get('offset'):query+=' offset '+str(int(filters['offset']))
        return json.loads(sql("select coalesce(json_agg(e),'[]') from ("+query+') e'))

    def download(self,bucket,path):return png()

    def request(self,method,path,**kwargs):
        function=path.rsplit('/',1)[-1]
        if path.startswith('/storage/'):
            return SimpleNamespace(json=lambda:{'signedURL':'https://storage.test/success.png'})
        assert method=='POST' and function in ('claim_campaign_entry','record_bike_end','claim_print_job')
        args=', '.join(k+' => '+literal(v) for k,v in kwargs['json'].items())
        result=json.loads(sql('select coalesce(json_agg(r),\'[]\') from public.'+function+'('+args+') r'))
        return SimpleNamespace(json=lambda:result)


def main():
    # Reuse the tested SQL fixture generator, committing only two temporary entries.
    source=(ROOT/'server/tests/sql/campaign_flow.sql').read_text().split('do $$')[0]
    station='test-'+uuid4().hex[:8]
    source=source.replace("insert into public.challenges(id,target_image_path) values(cid,'fixture');",
        "insert into public.challenges(id,target_image_path,campaign_station_id) values(cid,'fixture',"+literal(station)+");")
    source=source.replace("'fixture-hash'",literal(digest(png())))
    seeded=sql(source+"select pg_temp.make_campaign('gemini'); select pg_temp.make_campaign('zeus'); commit;")
    ids=re.findall(r'(?m)^[0-9a-f-]{36}$',seeded)
    assert len(ids)==2
    settings=replace(load_settings(),bike_worker_token='test-only-bike-token-'+'x'*32,print_worker_token='p'*32,print_station_id=station)
    cloud_app=FastAPI();cloud_app.include_router(campaign.router);cloud_app.include_router(printing.router)
    store=SQLStore(ids)
    with patch.object(campaign,'load_settings',return_value=settings),patch.object(campaign,'Store',return_value=store), \
         patch.object(printing,'load_settings',return_value=settings),patch.object(printing,'Store',return_value=store),TestClient(cloud_app) as cloud:
        class CloudClient:
            def call(self,path,body=None):
                headers={'X-Bike-Worker-Token':settings.bike_worker_token}
                response=cloud.get('/api/campaign'+path,headers=headers) if body is None else cloud.post('/api/campaign'+path,headers=headers,json=body)
                if response.status_code!=200:raise RuntimeError(response.text)
                return response.json()
        app=create_app(':memory:',CloudClient())
        with TestClient(app) as mac:
            now=[0.0];app.state.station.clock=lambda:now[0]
            receiver=app.state.receiver;receiver.set_mode('usb')
            sink=receiver.sinks['usb'];sink.connected=True;sink.device_id='test-usb';sink.device_name='VIRTUAL USB TEST'
            def sample(t):
                def feed():
                    now[0]=t;sink.ingest(Reading(5,.11))
                mac.portal.call(feed)
            sample(0)
            mac.portal.call(app.state.campaign.sync_once)
            entries=mac.get('/api/campaign/queue').json()['entries']
            chosen=[next(e for e in entries if e['energy_basis']==basis) for basis in ['tokens_only','measured_gpu']]
            for entry in chosen:
                sample(now[0]+.1)
                response=mac.post('/api/campaign/select',json={'entry_id':entry['id']});assert response.status_code==200,response.text
                response=mac.post('/api/session/start',json={'participant':'IGNORE-BROWSER','target_wh':999});assert response.status_code==200,response.text
                session=response.json();assert session['participant']==entry['participant_code']
                start=now[0]
                if entry['energy_basis']=='tokens_only':
                    assert session['target_wh'] is None
                    sample(start+.1);sample(start+1.1)
                    assert mac.post('/api/session/stop',json={}).status_code==200
                else:
                    assert abs(session['target_wh']-.15)<1e-10
                    for i in range(16):
                        sample(start+.1+i)
                        if app.state.station.session['status']!='running':break
                    assert app.state.station.session['end_reason']=='target_reached'
                sid=session['id']
                mac.portal.call(app.state.campaign.sync_once)
                # Duplicate UI Stop does not change the final record or queue another copy.
                assert mac.post('/api/session/stop',json={}).status_code==200
                mac.portal.call(app.state.campaign.sync_once)
                assert int(sql("select count(*) from public.print_jobs where bike_session_id="+literal(sid)))==1
                saved=json.loads(sql("select row_to_json(r) from public.bike_results r where id="+literal(sid)))
                assert (saved['progress_pct'] is None)==(entry['energy_basis']=='tokens_only')
                headers={'X-Print-Worker-Token':settings.print_worker_token}
                job=cloud.post('/api/printing/claim',json={},headers=headers).json()['job']
                assert job['bike_session_id']==sid
                response=cloud.post('/api/printing/jobs/'+job['id']+'/payload',json={'lease_token':job['lease_token']},headers=headers)
                assert response.status_code==200,response.text
                payload=response.json();assert payload['receipt']['verified']
                assert 'raw_energy_wh' not in payload['receipt']['bike']
                image=render_receipt(payload['receipt'],base64.b64decode(payload['image_base64']))
                assert image.width==384 and image.mode=='1'
                assert mac.post('/api/session/reset',json={}).status_code==200
                print(entry['energy_basis']+': HTTP select/start/end -> SQL -> one print job -> verified payload -> 384-dot renderer: PASS')


if __name__=='__main__':main()
