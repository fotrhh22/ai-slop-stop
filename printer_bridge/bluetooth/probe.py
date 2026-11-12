"""Discover BLE printers or enumerate an explicitly selected device. Never prints."""
from __future__ import annotations
import argparse
import asyncio
import json

PRINTER_HINTS = ('pt-210', 'pt210', 'mtp', 'printer', 'goojprt')


def printer_candidate(name):
    return any(hint in (name or '').lower() for hint in PRINTER_HINTS)


def service_report(services):
    result = []
    for service in services:
        characteristics = []
        for char in service.characteristics:
            properties = list(char.properties)
            item = {'uuid': char.uuid, 'handle': char.handle, 'properties': properties,
                    'write_candidate': bool({'write', 'write-without-response'} & set(properties))}
            if 'write-without-response' in properties:
                item['max_write_without_response_size'] = char.max_write_without_response_size
            characteristics.append(item)
        result.append({'uuid': service.uuid, 'characteristics': characteristics})
    return result


async def probe(args):
    try:
        from bleak import BleakClient, BleakScanner
    except ImportError:
        raise RuntimeError('Install optional requirements-bluetooth.txt in a Python 3.11 virtual environment') from None
    if args.scan:
        discovered = await BleakScanner.discover(timeout=args.timeout, return_adv=True)
        devices = []
        for device, adv in discovered.values():
            name = adv.local_name or device.name or ''
            if args.all or printer_candidate(name):
                devices.append({'name': name, 'id': device.address, 'rssi': adv.rssi,
                                'advertised_services': list(adv.service_uuids),
                                'printer_name_candidate': printer_candidate(name)})
        return {'mode': 'ble_scan', 'devices': devices,
                'note': 'A missing result does not rule out Classic SPP. No GATT writes were performed.'}
    device = await BleakScanner.find_device_by_address(args.inspect, timeout=args.timeout)
    if device is None:
        raise RuntimeError('DEVICE_NOT_FOUND: rescan on this Mac; a macOS device UUID is not a Bluetooth MAC address')
    async with BleakClient(device, timeout=args.timeout) as client:
        return {'mode': 'ble_inspect', 'name': device.name, 'id': device.address,
                'services': service_report(client.services),
                'note': 'Write properties are candidates only. No print, status or configuration command was sent.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--scan', action='store_true', help='List nearby printer-name BLE candidates')
    mode.add_argument('--inspect', metavar='DEVICE_ID', help='Enumerate services of an explicitly selected device')
    parser.add_argument('--all', action='store_true', help='Include non-printer names in local scan output')
    parser.add_argument('--timeout', type=float, default=10)
    args = parser.parse_args()
    if not .5 <= args.timeout <= 30:
        parser.error('--timeout must be between 0.5 and 30 seconds')
    if args.all and not args.scan:
        parser.error('--all is only valid with --scan')
    try:
        print(json.dumps(asyncio.run(probe(args)), ensure_ascii=False, indent=2))
    except (RuntimeError, OSError) as exc:
        parser.exit(1, str(exc) + '\n')


if __name__ == '__main__':
    main()
