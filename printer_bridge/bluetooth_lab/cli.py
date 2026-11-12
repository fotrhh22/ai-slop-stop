"""Print an already prepared receipt directly via BLE or macOS Classic SPP."""
from __future__ import annotations
import argparse
import asyncio
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from datetime import datetime, timezone
from .transports import chunks, write_ble, write_serial, TransmissionError

ROOT = Path(__file__).resolve().parent
SAMPLE = ROOT / 'samples/example-receipt.escpos'
HELPER = ROOT / 'build/PT210SPP.app/Contents/MacOS/pt210-spp'


def payload_info(path):
    payload = path.read_bytes()
    if not payload or len(payload) > 2_000_000:
        raise ValueError('Expected a nonempty ESC/POS file below 2 MB')
    return payload, {'file': str(path.resolve()), 'bytes': len(payload),
                     'sha256': hashlib.sha256(payload).hexdigest()}


async def ble_send(args, payload):
    from bleak import BleakClient, BleakScanner
    device = await BleakScanner.find_device_by_address(args.device, timeout=args.connect_timeout)
    if device is None:
        raise RuntimeError('BLE_DEVICE_NOT_FOUND: run ble-test --scan on this Mac')
    async with BleakClient(device, timeout=args.connect_timeout) as client:
        characteristic = client.services.get_characteristic(args.characteristic)
        if characteristic is None:
            raise RuntimeError('BLE_CHARACTERISTIC_NOT_FOUND: use --inspect before printing')
        start = time.perf_counter()
        accepted = await write_ble(client, characteristic, payload, args.chunk_bytes,
                                   args.response == 'yes', args.interval_ms/1000, args.write_timeout)
        # Without-response can return while CoreBluetooth still has queued data.
        # Give the final packet time before closing the connection. This is not a paper ACK.
        await asyncio.sleep(args.settle_seconds)
        return {'state': 'TRANSPORT_WRITES_COMPLETED', 'bytes_completed': accepted,
                'send_seconds': round(time.perf_counter()-start, 3), 'paper_verified': False}


def spp_send(args, payload):
    if args.port:
        if not args.port.startswith('/dev/cu.') or args.port.endswith(('Bluetooth-Incoming-Port', 'debug-console')):
            raise ValueError('Choose the paired printer serial port, not the generic incoming/debug port')
        command = [sys.executable, '-m', 'printer_bridge.bluetooth_lab.serial_worker', '--send',
                   '--port', args.port, '--file', str(args.file.resolve()), '--baud', str(args.baud),
                   '--chunk', str(args.chunk_bytes), '--interval', str(args.interval_ms/1000),
                   '--write-timeout', str(args.write_timeout)]
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=args.total_timeout)
        except subprocess.TimeoutExpired as exc:
            raise TransmissionError(0, len(payload), exc) from exc
        if result.returncode:
            raise RuntimeError((result.stdout or result.stderr).strip()[:600] or 'SERIAL_HELPER_FAILED')
        return json.loads(result.stdout)
    if not HELPER.is_file():
        raise RuntimeError('Build native SPP helper with ./setup.command first')
    command = [str(HELPER), '--send', '--address', args.address, '--file', str(args.file.resolve()),
               '--chunk', str(args.chunk_bytes), '--delay-ms', str(args.interval_ms), '--channel', args.channel]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=args.total_timeout)
    except subprocess.TimeoutExpired as exc:
        # A blocked OS write may have delivered data. Never restart the helper.
        raise TransmissionError(0, len(payload), exc) from exc
    if result.returncode:
        raise RuntimeError((result.stdout or result.stderr).strip()[:600] or 'SPP_HELPER_FAILED')
    return json.loads(result.stdout)


def execute(args):
    if args.scan or args.inspect:
        if args.transport == 'ble':
            from printer_bridge.bluetooth.probe import probe
            from types import SimpleNamespace as NS
            return asyncio.run(probe(NS(scan=args.scan, inspect=args.inspect, all=False, timeout=args.connect_timeout)))
        if args.inspect:
            raise ValueError('SPP inspection uses --address with --scan to query SDP')
        command = [str(HELPER), '--inspect', '--address', args.address] if args.address else [str(HELPER), '--scan']
        result = subprocess.run(command, capture_output=True, text=True, timeout=args.total_timeout)
        if result.returncode:
            raise RuntimeError((result.stdout or result.stderr).strip()[:600])
        return json.loads(result.stdout)
    payload, info = payload_info(args.file)
    if not args.send:
        size = args.chunk_bytes
        return {'mode': 'dry_run', 'transport': args.transport, **info,
                'configured_chunks': len(chunks(payload, size)), 'configured_chunk_bytes': size,
                'distance_m': getattr(args,'distance_m',None),
                'minimum_configured_pacing_seconds': round(max(0, len(chunks(payload, size))-1)*args.interval_ms/1000, 3),
                'note': 'No Bluetooth access, DB, HTTP, CUPS or printer write. BLE actual chunk may be smaller.'}
    if args.transport == 'ble' and not (args.device and args.characteristic):
        raise ValueError('BLE send requires --device and --characteristic from actual inspection')
    if args.transport == 'spp' and bool(args.port) == bool(args.address):
        raise ValueError('SPP send requires exactly one of --address or --port')
    # Separate from the production journal: these are intentional standalone tests.
    runs = ROOT / 'runs'
    runs.mkdir(mode=0o700, exist_ok=True)
    with (runs / 'transmit.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        run_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        log = runs / (run_id+'.json')
        report = {'run_id': run_id, 'transport': args.transport, **info, 'distance_m': args.distance_m, 'state': 'STARTING', 'paper_verified': False}
        def save():
            with log.open('w') as stream:
                json.dump(report, stream, indent=2); stream.flush(); os.fsync(stream.fileno())
        save()
        try:
            report.update(asyncio.run(ble_send(args, payload)) if args.transport == 'ble' else spp_send(args, payload))
        except BaseException as exc:
            report.update(state='UNCONFIRMED', error=str(exc)[:600], automatic_retry=False)
            save()
            raise
        save()
        return {**report, 'log': str(log)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('transport', choices=['ble', 'spp'])
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--send', action='store_true', help='Actually transmit one receipt; no automatic retry')
    mode.add_argument('--dry-run', action='store_true', help='Offline validation (default)')
    mode.add_argument('--scan', action='store_true', help='BLE scan / native Classic inquiry, no printing')
    mode.add_argument('--inspect', metavar='DEVICE_UUID', help='BLE service inspection, no printing')
    parser.add_argument('--file', type=Path)
    parser.add_argument('--distance-m', type=float, help='Physically measured test distance; never estimated from RSSI')
    parser.add_argument('--config', type=Path, help='Local device settings; defaults to ble.local or spp.local if present')
    parser.add_argument('--device', help='Actual BLE device UUID from this Mac')
    parser.add_argument('--characteristic', help='Actual BLE write characteristic UUID')
    parser.add_argument('--response', choices=['yes', 'no'])
    parser.add_argument('--address', help='Classic Bluetooth MAC address; native RFCOMM')
    parser.add_argument('--port', help='Explicit paired printer /dev/cu.* port; optional SPP path')
    parser.add_argument('--channel', help='Classic RFCOMM channel or auto via SDP SPP 0x1101')
    parser.add_argument('--baud', type=int, default=9600, help='Virtual serial port setting, not Bluetooth radio throughput')
    parser.add_argument('--chunk-bytes', type=int)
    parser.add_argument('--interval-ms', type=float)
    parser.add_argument('--connect-timeout', type=float, default=15)
    parser.add_argument('--write-timeout', type=float, default=10)
    parser.add_argument('--total-timeout', type=float, default=180)
    parser.add_argument('--settle-seconds', type=float, help='Wait after the last BLE write before disconnecting')
    args = parser.parse_args()
    config_path = args.config or (ROOT / (args.transport+'.local'))
    if config_path.exists():
        try:
            settings = json.loads(config_path.read_text())
            if not isinstance(settings, dict): raise ValueError('configuration must be a JSON object')
            if args.file is None and settings.get('file') is not None:
                if not isinstance(settings['file'],str): raise ValueError('file must be a string')
                configured_file=Path(settings['file'])
                args.file=configured_file if configured_file.is_absolute() else config_path.parent/configured_file
            for name in ('device', 'characteristic', 'response', 'address', 'port', 'channel'):
                value = settings.get(name)
                if value is not None and not isinstance(value, str): raise ValueError(name+' must be a string')
                if getattr(args, name) is None and value: setattr(args, name, value)
            for name in ('chunk_bytes', 'interval_ms', 'settle_seconds'):
                value = settings.get(name)
                if value is not None:
                    if isinstance(value, bool) or not isinstance(value, (int,float)):
                        raise ValueError(name+' must be a number')
                    if name == 'chunk_bytes' and not isinstance(value,int):
                        raise ValueError('chunk_bytes must be an integer')
                    if getattr(args,name) is None: setattr(args,name,value)
        except (ValueError, OSError) as exc:
            parser.error('Invalid local config: '+str(exc))
    elif args.config:
        parser.error('Config file not found')
    args.file = args.file or SAMPLE
    args.chunk_bytes = args.chunk_bytes if args.chunk_bytes is not None else 182
    args.interval_ms = args.interval_ms if args.interval_ms is not None else 40
    args.settle_seconds = args.settle_seconds if args.settle_seconds is not None else 1
    args.response = args.response or 'no'
    args.channel = args.channel or 'auto'
    if args.distance_m is not None and not 0 <= args.distance_m <= 1000: parser.error('distance-m must be 0..1000')
    if not 0 <= args.settle_seconds <= 10: parser.error('settle-seconds must be 0..10')
    if args.response not in ('yes','no'): parser.error('response must be yes or no')
    if not 1 <= args.chunk_bytes <= 512 or not 0 <= args.interval_ms <= 1000:
        parser.error('chunk-bytes must be 1..512 and interval-ms 0..1000')
    if not 1 <= args.connect_timeout <= 30 or not 1 <= args.write_timeout <= 60 or not 1 <= args.total_timeout <= 600:
        parser.error('connect/write/total timeout must be 1..30 / 1..60 / 1..600 seconds')
    if args.channel != 'auto' and (not args.channel.isdigit() or not 1 <= int(args.channel) <= 30):
        parser.error('channel must be auto or 1..30')
    os.umask(0o077)
    try:
        print(json.dumps(execute(args), ensure_ascii=False, indent=2))
    except KeyboardInterrupt:
        parser.exit(130, 'Interrupted; transmission may be incomplete. Check paper before retrying.\n')
    except Exception as exc:
        parser.exit(1, f'{type(exc).__name__}: {exc}\n')


if __name__ == '__main__':
    main()
