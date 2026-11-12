"""Bounded by parent subprocess timeout; drain the OS serial queue before close."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import time
from .transports import write_serial


def transmit(port, payload, chunk_bytes, interval):
    accepted = write_serial(port, payload, chunk_bytes, interval)
    port.flush()  # Native tcdrain can block: caller runs this process under a total timeout.
    time.sleep(.5)
    return accepted


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--send',action='store_true',required=True)
    parser.add_argument('--port',required=True)
    parser.add_argument('--file',type=Path,required=True)
    parser.add_argument('--baud',type=int,default=9600)
    parser.add_argument('--chunk',type=int,default=182)
    parser.add_argument('--interval',type=float,default=.04)
    parser.add_argument('--write-timeout',type=float,default=10)
    args=parser.parse_args()
    if not args.port.startswith('/dev/cu.') or args.port.endswith(('Bluetooth-Incoming-Port','debug-console')):
        parser.error('Printer-specific serial port required')
    if not 1<=args.chunk<=512 or not 0<=args.interval<=1 or not 1<=args.write_timeout<=60:
        parser.error('Invalid transmission limits')
    payload=args.file.read_bytes()
    if not payload or len(payload)>2_000_000:parser.error('Invalid payload size')
    import serial
    started=time.perf_counter()
    try:
        with serial.Serial(args.port,args.baud,timeout=1,write_timeout=args.write_timeout) as port:
            accepted=transmit(port,payload,args.chunk,args.interval)
        print(json.dumps({'state':'TRANSPORT_WRITES_COMPLETED','bytes_completed':accepted,
                          'send_seconds':round(time.perf_counter()-started,3),'serial_queue_drained':True,'paper_verified':False}))
    except Exception as exc:
        parser.exit(1,type(exc).__name__+': '+str(exc)+'\n')


if __name__=='__main__':main()
