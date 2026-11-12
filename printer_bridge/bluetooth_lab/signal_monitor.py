"""Observe the selected printer's BLE advertisement RSSI; never print or infer meters."""
from __future__ import annotations
import argparse
import asyncio
from collections import deque
import csv
from datetime import datetime,timezone
import json
import os
from pathlib import Path
import statistics
import time

ROOT=Path(__file__).resolve().parent


class SignalWindow:
    def __init__(self,seconds=5):
        self.seconds=seconds
        self.samples=deque()
        self.last_sample=None
        self.total=0

    def add(self,rssi,now):
        if isinstance(rssi,bool) or not isinstance(rssi,(int,float)) or not -127<=rssi<=20:return
        self.samples.append((now,float(rssi)));self.last_sample=now;self.total+=1

    def report(self,now):
        while self.samples and self.samples[0][0]<now-self.seconds:self.samples.popleft()
        values=[value for _,value in self.samples]
        return {'samples':len(values),'total_samples':self.total,
                'median_dbm':round(statistics.median(values),1) if values else None,
                'min_dbm':min(values) if values else None,'max_dbm':max(values) if values else None,
                'last_sample_age_s':round(now-self.last_sample,1) if self.last_sample is not None else None,
                'advertisement_state':'receiving' if values else 'no_recent_advertisement'}


async def monitor(device_id,duration,distance,output):
    from bleak import BleakScanner
    window=SignalWindow()
    def detected(device,advertisement):
        if device.address.lower()==device_id.lower():window.add(advertisement.rssi,time.monotonic())
    output.parent.mkdir(parents=True,exist_ok=True)
    fields=['utc','distance_m','samples','total_samples','median_dbm','min_dbm','max_dbm','last_sample_age_s','advertisement_state']
    with output.open('w',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=fields);writer.writeheader()
        print(json.dumps({'mode':'advertisement_rssi','duration_s':duration,'distance_m':distance,'csv':str(output),
                          'note':'RSSI is not distance. Advertising can stop during a connection; missing ads do not prove disconnection.'}),flush=True)
        async with BleakScanner(detection_callback=detected):
            deadline=time.monotonic()+duration
            while time.monotonic()<deadline:
                await asyncio.sleep(min(1,max(0,deadline-time.monotonic())))
                record={'utc':datetime.now(timezone.utc).isoformat(),'distance_m':distance,**window.report(time.monotonic())}
                writer.writerow(record);stream.flush()
                print(json.dumps(record,ensure_ascii=False),flush=True)
    return window.total


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--device',help='Printer BLE UUID, defaults to ble.local')
    parser.add_argument('--duration',type=float,default=30)
    parser.add_argument('--distance-m',type=float,help='Actual measured distance; optional label for this run')
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    if not 1<=args.duration<=600:parser.error('duration must be 1..600 seconds')
    if args.distance_m is not None and not 0<=args.distance_m<=1000:parser.error('distance-m must be 0..1000')
    if not args.device:
        try:args.device=json.loads((ROOT/'ble.local').read_text())['device']
        except (OSError,ValueError,KeyError):parser.error('Specify --device or create ble.local')
    output=args.output or ROOT/'runs'/('signal-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.csv')
    os.umask(0o077)
    try:asyncio.run(monitor(args.device,args.duration,args.distance_m,output))
    except KeyboardInterrupt:parser.exit(130,'Signal monitoring stopped. Samples already recorded remain in CSV.\n')
    except Exception as exc:parser.exit(1,type(exc).__name__+': '+str(exc)+'\n')


if __name__=='__main__':main()
