"""Outbound-only booth print worker. No HTTP listener, no Supabase service key."""
from __future__ import annotations
import argparse
import base64
import fcntl
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import urllib.request
import urllib.error
from uuid import UUID,uuid4
from .renderer import render_receipt,escpos_bytes

class APIError(Exception):
    def __init__(self,status):self.status=status;super().__init__(f'PRINT_API_HTTP_{status}')

def load_env(path):
    if not path.exists(): return
    for line in path.read_text().splitlines():
        if line.strip() and not line.lstrip().startswith('#') and '=' in line:
            k,v=line.split('=',1);os.environ.setdefault(k.strip(),v.strip().strip('"').strip("'"))

class Client:
    def __init__(self,base,token):
        if not (base.startswith('https://') or base.startswith('http://127.0.0.1:') or base.startswith('http://localhost:')):
            raise ValueError('USE_HTTPS_OR_LOCALHOST')
        self.base=base.rstrip('/');self.token=token
    def call(self,path,body=None):
        req=urllib.request.Request(self.base+'/api/printing'+path,
            data=None if body is None else json.dumps(body).encode(),
            headers={'Content-Type':'application/json','X-Print-Worker-Token':self.token})
        try:
            with urllib.request.urlopen(req,timeout=60) as response:
                return json.loads(response.read(12_000_000))
        except urllib.error.HTTPError as exc: raise APIError(exc.code) from None

class Journal:
    def __init__(self,path):
        self.path=path
        self.data=json.loads(path.read_text()) if path.exists() else {}
    def save(self,job,**values):
        item=self.data.setdefault(job['id'],{'lease_token':job['lease_token']})
        item.update(values)
        temp=self.path.with_suffix('.tmp')
        with temp.open('w') as stream:
            json.dump(self.data,stream);stream.flush();os.fsync(stream.fileno())
        os.replace(temp,self.path)
        fd=os.open(self.path.parent,os.O_RDONLY)
        try:os.fsync(fd)
        finally:os.close(fd)

def advance(client,job,status,**extra):
    return client.call('/jobs/'+job['id']+'/advance',{'lease_token':job['lease_token'],'status':status,**extra})

def reconcile(client,journal):
    for jid,entry in list(journal.data.items()):
        if entry.get('acked') or entry.get('state')=='PREPARED':continue
        job={'id':jid,'lease_token':entry['lease_token']}
        status=entry['state']
        if status=='SENDING':status='UNKNOWN'
        try:
            advance(client,job,status,cups_job_id=entry.get('cups_job_id'),
                error_code='WORKER_INTERRUPTED' if status=='UNKNOWN' else entry.get('error_code'))
        except APIError as exc:
            if exc.status!=409:raise
            # A stale lease cannot authorize sending again.
            print(f'{jid}: lease expired; no automatic resend',flush=True)
        journal.save(job,state=status,acked=True)

def submit(path,queue):
    # Successful command return means spool acceptance, not verified paper output.
    result=subprocess.run(['/usr/bin/lp','-d',queue,'-o','raw','-t','AI SLOP STOP',str(path)],
        capture_output=True,text=True,timeout=45,env={**os.environ,'LC_ALL':'C','LANG':'C'})
    if result.returncode:raise RuntimeError('CUPS_SUBMISSION_UNCERTAIN')
    match=re.search(r'(?<![A-Za-z0-9_.-])'+re.escape(queue)+r'-\d+(?![A-Za-z0-9_.-])',result.stdout)
    if not match:raise RuntimeError('CUPS_RESPONSE_UNCERTAIN')
    return match.group(0)

def fetch_payload(client,job):
    # This endpoint only reads the leased job. Retrying it cannot print a copy.
    for attempt in range(3):
        try:
            return client.call('/jobs/'+job['id']+'/payload',{'lease_token':job['lease_token']})
        except (APIError,urllib.error.URLError,TimeoutError,ConnectionError) as exc:
            if isinstance(exc,APIError) and exc.status<500:raise
            if attempt==2:raise
            print(f"{job['id']}: payload retry {attempt+1}/2 ({type(exc).__name__})",flush=True)
            time.sleep(attempt+1)


def process(client,journal,job,directory,queue):
    started=time.monotonic()
    stage='payload'
    def timing(name,start):
        print(f"{job['id']}: stage={name} seconds={time.monotonic()-start:.2f}",flush=True)
    try:
        tick=time.monotonic();payload=fetch_payload(client,job);timing(stage,tick)
        stage='validate'
        receipt=payload['receipt']
        if receipt['id']!=job['receipt_id'] or not receipt.get('verified'):raise ValueError('RECEIPT_MISMATCH')
        stage='render';tick=time.monotonic()
        image=render_receipt(receipt,base64.b64decode(payload['image_base64'],validate=True));timing(stage,tick)
        stage='encode';tick=time.monotonic()
        png=directory/(job['id']+'.png');raw=directory/(job['id']+'.escpos')
        image.save(png)
        with raw.open('wb') as stream:stream.write(escpos_bytes(image));stream.flush();os.fsync(stream.fileno())
        journal.save(job,state='PREPARED',acked=False)
        timing(stage,tick)
    except Exception as exc:
        code='PAYLOAD_FAILED' if stage=='payload' else 'RENDER_FAILED'
        journal.save(job,state='FAILED',error_code=code,error_stage=stage,error_type=type(exc).__name__,acked=False)
        reconcile(client,journal)
        status=f' HTTP_{exc.status}' if isinstance(exc,APIError) else ''
        print(f"{job['id']}: {stage} failed ({type(exc).__name__}{status}; not sent)",flush=True);return
    # Persist intent BEFORE advancing the server or submitting to CUPS.
    journal.save(job,state='SENDING',acked=False)
    try:
        tick=time.monotonic();advance(client,job,'SENDING');timing('authorize_send',tick)
    except Exception:
        # This request might have reached the server. Do not send or reclaim locally.
        reconcile(client,journal);return
    try:
        tick=time.monotonic();cups_id=submit(raw,queue);timing('cups_submit',tick)
        journal.save(job,state='SENT',cups_job_id=cups_id,acked=False)
    except Exception:
        journal.save(job,state='UNKNOWN',error_code='CUPS_SUBMISSION_UNCERTAIN',acked=False)
    reconcile(client,journal)
    timing('total',started)
    print(f"{job['id']}: {journal.data[job['id']]['state']} (paper completion not verified)",flush=True)

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env',default='printer_bridge/.env.local')
    mode=parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--run',action='store_true',help='Poll and print booth jobs')
    mode.add_argument('--once',action='store_true',help='Claim and print at most one job')
    mode.add_argument('--status',action='store_true')
    mode.add_argument('--reprint',type=UUID,metavar='JOB_ID',help='Explicitly request another physical copy')
    args=parser.parse_args();load_env(Path(args.env));os.umask(0o077)
    base=os.environ.get('PRINT_API_BASE','https://ai-slop-stop-api.onrender.com')
    token=os.environ.get('PRINT_WORKER_TOKEN','')
    if len(token)<24:parser.error('Set PRINT_WORKER_TOKEN in private env file')
    try:poll_seconds=float(os.environ.get('PRINT_POLL_SECONDS','1'))
    except ValueError:parser.error('PRINT_POLL_SECONDS must be a number between 0.5 and 60')
    if not .5<=poll_seconds<=60:parser.error('PRINT_POLL_SECONDS must be between 0.5 and 60')
    client=Client(base,token)
    if args.status:
        print(json.dumps(client.call('/jobs'),ensure_ascii=False,indent=2));return
    if args.reprint:
        result=client.call('/jobs/'+str(args.reprint)+'/reprint',{'request_id':str(uuid4())})
        print('Reprint queued:',result['id']);return
    directory=Path(os.environ.get('PRINT_DATA_DIR',str(Path.home()/'Library/Application Support/AI Slop Print')))
    directory.mkdir(parents=True,exist_ok=True)
    lock=(directory/'worker.lock').open('a')
    try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError:parser.error('A print worker is already running')
    queue=os.environ.get('PRINT_CUPS_QUEUE','PT210_USB_BRIDGE')
    check=subprocess.run(['/usr/bin/lpstat','-p',queue],capture_output=True)
    if check.returncode:parser.error('CUPS queue missing: '+queue)
    journal=Journal(directory/'journal.json')
    print('PT-210 worker ready. Atkinson / 384 dots. Ctrl+C to stop.',flush=True)
    while True:
        job=None
        try:
            reconcile(client,journal)
            job=client.call('/claim',{})['job']
            if job:process(client,journal,job,directory,queue)
        except KeyboardInterrupt:break
        except Exception as exc:
            job=None
            print(type(exc).__name__+': '+str(exc)[:100],flush=True)
            if args.once:raise SystemExit(1)
        if args.once:break
        time.sleep(poll_seconds if job is None else 0)

if __name__=='__main__':main()
