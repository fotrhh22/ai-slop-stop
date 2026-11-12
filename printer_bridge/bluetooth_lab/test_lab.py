import asyncio
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, Mock, patch
from .cli import execute, SAMPLE, HELPER
from .transports import chunks, write_ble, write_serial, TransmissionError


class OfflineTests(unittest.TestCase):
    def test_two_transports_use_exactly_the_same_prepared_receipt_without_io(self):
        reports = []
        for transport in ('ble','spp'):
            args = NS(transport=transport, scan=False, inspect=None, file=SAMPLE, send=False, chunk_bytes=182, interval_ms=40)
            with patch('printer_bridge.bluetooth_lab.cli.ble_send') as ble, patch('printer_bridge.bluetooth_lab.cli.spp_send') as spp:
                reports.append(execute(args)); ble.assert_not_called(); spp.assert_not_called()
        self.assertEqual(reports[0]['sha256'], reports[1]['sha256'])
        self.assertEqual(reports[0]['sha256'], hashlib.sha256(SAMPLE.read_bytes()).hexdigest())
        self.assertEqual(b''.join(chunks(SAMPLE.read_bytes(),182)), SAMPLE.read_bytes())

    def test_serial_partial_writes_preserve_every_byte_once(self):
        captured = bytearray()
        def write(data):
            count = min(3,len(data)); captured.extend(data[:count]); return count
        port = NS(write=Mock(side_effect=write))
        self.assertEqual(write_serial(port,b'abcdefghij',4,0),10)
        self.assertEqual(captured,b'abcdefghij')

    def test_serial_drain_precedes_disconnect(self):
        from .serial_worker import transmit
        sequence = []
        port = NS(write=Mock(side_effect=lambda data: sequence.append('write') or len(data)),
                  flush=Mock(side_effect=lambda: sequence.append('drain')))
        with patch('printer_bridge.bluetooth_lab.serial_worker.time.sleep', side_effect=lambda _: sequence.append('settle')):
            self.assertEqual(transmit(port,b'abc',20,0),3)
        self.assertEqual(sequence,['write','drain','settle'])

    def test_serial_failure_does_not_restart_payload(self):
        port=NS(write=Mock(side_effect=[4,TimeoutError()]))
        with self.assertRaises(TransmissionError) as result:
            write_serial(port,b'abcdefghij',4,0)
        self.assertEqual(result.exception.completed,4)
        self.assertEqual(port.write.call_count,2)

    def test_saved_pacing_profile_is_used_and_cli_overrides_it(self):
        import subprocess, tempfile
        from .cli import ROOT
        with tempfile.TemporaryDirectory() as directory:
            config=Path(directory)/'config.json'
            config.write_text(json.dumps({'chunk_bytes':237,'interval_ms':0,'settle_seconds':2,'response':'yes'}))
            result=subprocess.run([str(ROOT/'ble-test'),'--dry-run','--config',str(config)],capture_output=True,text=True,check=True)
            self.assertEqual(json.loads(result.stdout)['configured_chunk_bytes'],237)
            self.assertEqual(json.loads(result.stdout)['minimum_configured_pacing_seconds'],0)
            result=subprocess.run([str(ROOT/'ble-test'),'--dry-run','--config',str(config),'--chunk-bytes','20'],capture_output=True,text=True,check=True)
            self.assertEqual(json.loads(result.stdout)['configured_chunk_bytes'],20)

    def test_receipt_path_in_config_is_relative_to_config_directory(self):
        import subprocess, tempfile
        from .cli import ROOT
        with tempfile.TemporaryDirectory() as directory:
            directory=Path(directory)
            (directory/'test.escpos').write_bytes(b'abc')
            config=directory/'config.json';config.write_text(json.dumps({'file':'test.escpos'}))
            result=subprocess.run([str(ROOT/'ble-test'),'--dry-run','--config',str(config)],cwd='/',capture_output=True,text=True,check=True)
            self.assertEqual(json.loads(result.stdout)['bytes'],3)
            result=subprocess.run([str(ROOT/'ble-test'),'--dry-run','--config',str(config),'--file',str(SAMPLE)],capture_output=True,text=True,check=True)
            self.assertEqual(json.loads(result.stdout)['bytes'],SAMPLE.stat().st_size)

    def test_raster_encoding_preserves_every_existing_pixel(self):
        from PIL import Image
        from .raster import encode_gsv0
        from .cli import ROOT
        import struct
        with Image.open(ROOT/'samples/example-receipt.png') as source:
            for rows in (24,96,128):
                payload=encode_gsv0(source,rows)
                cursor=5;decoded=bytearray();height=0
                while cursor<len(payload)-3:
                    self.assertEqual(payload[cursor:cursor+4],b'\x1dv0\x00')
                    width,count=struct.unpack('<HH',payload[cursor+4:cursor+8])
                    self.assertEqual(width,48)
                    data=payload[cursor+8:cursor+8+width*count]
                    decoded.extend(value^0xff for value in data)
                    height+=count;cursor+=8+width*count
                self.assertEqual(height,source.height)
                self.assertEqual(bytes(decoded),source.tobytes())

    def test_signal_window_reports_signal_not_estimated_distance(self):
        from .signal_monitor import SignalWindow
        window=SignalWindow(seconds=5)
        window.add(-43,0);window.add(-45,1);window.add(float('nan'),1)
        report=window.report(2)
        self.assertEqual(report['median_dbm'],-44)
        self.assertEqual(report['samples'],2)
        self.assertNotIn('estimated_distance',report)
        report=window.report(7)
        self.assertIsNone(report['median_dbm'])
        self.assertEqual(report['advertisement_state'],'no_recent_advertisement')

    def test_invalid_chunk_is_rejected(self):
        for size in (0,513,-1):
            with self.assertRaises(ValueError): chunks(b'a',size)

    def test_wrappers_resolve_relative_files_from_caller_directory(self):
        import subprocess
        from .cli import ROOT
        for name in ('ble-test', 'spp-test'):
            result = subprocess.run([str(ROOT/name), '--dry-run', '--file', 'samples/example-receipt.escpos'],
                                    cwd=ROOT, capture_output=True, text=True, check=True)
            self.assertEqual(json.loads(result.stdout)['bytes'], SAMPLE.stat().st_size)

    def test_native_compiled_helper_dry_run_has_no_radio_access(self):
        import subprocess
        self.assertTrue(HELPER.is_file(), 'Run setup.command to build the native Mac SPP helper')
        result=subprocess.run([str(HELPER),'--dry-run','--file',str(SAMPLE)],capture_output=True,text=True,check=True)
        report=json.loads(result.stdout)
        self.assertFalse(report['radio_access'])
        self.assertEqual(report['writes'],0)
        self.assertEqual(report['bytes'],SAMPLE.stat().st_size)

    def test_native_validates_arguments_without_device_access(self):
        import subprocess
        result=subprocess.run([str(HELPER),'--send','--address','not-an-address'],capture_output=True,text=True)
        self.assertEqual(result.returncode,2)
        self.assertEqual(json.loads(result.stdout)['error'],'VALID_CLASSIC_MAC_ADDRESS_REQUIRED')


class BLETests(unittest.IsolatedAsyncioTestCase):
    async def test_ble_keeps_connection_alive_after_final_write(self):
        from types import ModuleType
        from .cli import ble_send
        sequence = []
        char = NS(properties=['write'])
        client = NS(services=NS(get_characteristic=Mock(return_value=char)))
        manager = AsyncMock()
        manager.__aenter__.return_value = client
        async def close(*args): sequence.append('disconnect')
        manager.__aexit__.side_effect = close
        module = ModuleType('bleak')
        module.BleakClient = Mock(return_value=manager)
        module.BleakScanner = NS(find_device_by_address=AsyncMock(return_value=NS()))
        async def write(*args, **kwargs): sequence.append('write'); return 1
        async def settle(*args): sequence.append('settle')
        args = NS(device='device', characteristic='char', connect_timeout=1, chunk_bytes=20,
                  response='yes', interval_ms=0, write_timeout=1, settle_seconds=2)
        with patch.dict('sys.modules', {'bleak': module}), \
             patch('printer_bridge.bluetooth_lab.cli.write_ble', side_effect=write), \
             patch('printer_bridge.bluetooth_lab.cli.asyncio.sleep', side_effect=settle):
            await ble_send(args, b'a')
        self.assertEqual(sequence, ['write', 'settle', 'disconnect'])

    async def test_write_size_is_capped_to_actual_characteristic_limit(self):
        char=NS(properties=['write-without-response'],max_write_without_response_size=20)
        client=NS(is_connected=True,write_gatt_char=AsyncMock())
        payload=bytes(range(100))
        self.assertEqual(await write_ble(client,char,payload,182,False,0),100)
        sent=[call.args[1] for call in client.write_gatt_char.await_args_list]
        self.assertEqual([len(block) for block in sent],[20]*5)
        self.assertEqual(b''.join(sent),payload)
        for call in client.write_gatt_char.await_args_list:self.assertFalse(call.kwargs['response'])

    async def test_write_type_mismatch_does_not_write(self):
        client=NS(is_connected=True,write_gatt_char=AsyncMock())
        with self.assertRaises(ValueError):
            await write_ble(client,NS(properties=['read']),b'a',20,False,0)
        client.write_gatt_char.assert_not_called()

    async def test_midstream_failure_is_unknown_without_retry(self):
        char=NS(properties=['write'],max_write_without_response_size=20)
        client=NS(is_connected=True,write_gatt_char=AsyncMock(side_effect=[None,OSError('lost')]))
        with self.assertRaises(TransmissionError) as result:
            await write_ble(client,char,b'abcdefghij',4,True,0)
        self.assertEqual(result.exception.completed,4)
        self.assertEqual(result.exception.attempted,8)
        self.assertEqual(client.write_gatt_char.await_count,2)

    async def test_disconnected_client_never_queues_more_data(self):
        char=NS(properties=['write-without-response'],max_write_without_response_size=20)
        client=NS(is_connected=False,write_gatt_char=AsyncMock())
        with self.assertRaises(TransmissionError): await write_ble(client,char,b'a',20,False,0)
        client.write_gatt_char.assert_not_called()

    async def test_timeout_is_not_retried(self):
        async def stalled(*args,**kwargs): await asyncio.sleep(1)
        char=NS(properties=['write'],max_write_without_response_size=20)
        client=NS(is_connected=True,write_gatt_char=AsyncMock(side_effect=stalled))
        with self.assertRaises(TransmissionError): await write_ble(client,char,b'a',20,True,0,timeout=.01)
        self.assertEqual(client.write_gatt_char.await_count,1)
