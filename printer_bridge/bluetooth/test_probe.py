import unittest
from types import SimpleNamespace as NS, ModuleType
from unittest.mock import AsyncMock, patch
from .probe import printer_candidate, service_report, probe


class ProbeTests(unittest.TestCase):
    def test_names_are_hints_not_profile_detection(self):
        self.assertTrue(printer_candidate('MTP-II'))
        self.assertTrue(printer_candidate('PT-210'))
        self.assertFalse(printer_candidate('FNB58'))
        self.assertFalse(printer_candidate(None))

    def test_report_preserves_handles_and_does_not_guess_write_uuid(self):
        char = NS(uuid='actual-uuid', handle=7, properties=['write-without-response'],
                  max_write_without_response_size=182)
        report = service_report([NS(uuid='actual-service', characteristics=[char])])
        self.assertEqual(report[0]['characteristics'][0]['handle'], 7)
        self.assertEqual(report[0]['characteristics'][0]['max_write_without_response_size'], 182)
        self.assertTrue(report[0]['characteristics'][0]['write_candidate'])
        self.assertEqual(service_report([NS(uuid='s', characteristics=[NS(uuid='r', handle=8, properties=['read'])])])[0]
                         ['characteristics'][0]['write_candidate'], False)


class ProbeAsyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_scan_filters_names_without_connecting_or_writing(self):
        module = ModuleType('bleak')
        module.BleakClient = AsyncMock()
        module.BleakScanner = NS(discover=AsyncMock(return_value={
            'one': (NS(name='PT-210', address='uuid-one'), NS(local_name='', rssi=-45, service_uuids=['service'])),
            'two': (NS(name='FNB58', address='uuid-two'), NS(local_name='', rssi=-50, service_uuids=[]))}))
        with patch.dict('sys.modules', {'bleak': module}):
            result = await probe(NS(scan=True, all=False, timeout=1))
        self.assertEqual([row['id'] for row in result['devices']], ['uuid-one'])
        module.BleakClient.assert_not_called()

    async def test_inspect_only_enumerates_selected_device(self):
        client = NS(services=[])
        manager = AsyncMock()
        manager.__aenter__.return_value = client
        from unittest.mock import Mock
        module = ModuleType('bleak')
        module.BleakClient = Mock(return_value=manager)
        device = NS(name='PT-210', address='uuid-one')
        module.BleakScanner = NS(find_device_by_address=AsyncMock(return_value=device))
        with patch.dict('sys.modules', {'bleak': module}):
            result = await probe(NS(scan=False, inspect='uuid-one', timeout=1))
        self.assertEqual(result['services'], [])
        manager.__aexit__.assert_awaited_once()
