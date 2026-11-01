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

def process(client,journal,job,directory,queue):
    try:
        payload=client.call('/jobs/'+job['id']+'/payload',{'lease_token':job['lease_token']})
        receipt=payload['receipt']
        if receipt['id']!=job['receipt_id'] or not receipt.get('verified'):raise ValueError('RECEIPT_MISMATCH')
        image=render_receipt(receipt,base64.b64decode(payload['image_base64'],validate=True))
        png=directory/(job['id']+'.png');raw=directory/(job['id']+'.escpos')
        image.save(png)
        with raw.open('wb') as stream:stream.write(escpos_bytes(image));stream.flush();os.fsync(stream.fileno())
        journal.save(job,state='PREPARED',acked=False)
    except Exception:
        journal.save(job,state='FAILED',error_code='RENDER_FAILED',acked=False)
        reconcile(client,journal)
        print(f"{job['id']}: render failed (not sent)",flush=True);return
    # Persist intent BEFORE advancing the server or submitting to CUPS.
    journal.save(job,state='SENDING',acked=False)
    try:
        advance(client,job,'SENDING')
    except Exception:
        # This request might have reached the server. Do not send or reclaim locally.
        reconcile(client,journal);return
    try:
        cups_id=submit(raw,queue)
        journal.save(job,state='SENT',cups_job_id=cups_id,acked=False)
    except Exception:
        journal.save(job,state='UNKNOWN',error_code='CUPS_SUBMISSION_UNCERTAIN',acked=False)
    reconcile(client,journal)
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
        try:
            reconcile(client,journal)
            job=client.call('/claim',{})['job']
            if job:process(client,journal,job,directory,queue)
        except KeyboardInterrupt:break
        except Exception as exc:
            print(type(exc).__name__+': '+str(exc)[:100],flush=True)
            if args.once:raise SystemExit(1)
        if args.once:break
        time.sleep(5)

if __name__=='__main__':main()
