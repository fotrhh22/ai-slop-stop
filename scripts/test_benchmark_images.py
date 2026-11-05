import hashlib
import base64
import json
from pathlib import Path
import tempfile
from unittest import TestCase
from unittest.mock import Mock, patch

from scripts import benchmark_images as bench


class BenchmarkTests(TestCase):
    def setup_case(self, folder):
        evidence = {'health': {'image_model':'test'}, 'results':[]}
        return evidence, Path(folder)/'results.json', {'id':'apple','prompt':'빨간 사과','criteria':['빨간색']}

    def test_timeout_keeps_id_and_never_retries(self):
        with tempfile.TemporaryDirectory() as folder:
            evidence,path,case=self.setup_case(folder)
            def timeout(*args,**kwargs):
                saved=json.loads(path.read_text())
                self.assertEqual(saved['results'][0]['status'],'unconfirmed')
                self.assertTrue(saved['results'][0]['request_id'])
                raise TimeoutError()
            with patch.object(bench,'request',side_effect=timeout) as request:
                with self.assertRaises(RuntimeError):
                    bench.generate_case('https://test','private-key',case,Path(folder),evidence,path,Mock())
            request.assert_called_once()
            self.assertEqual(evidence['results'][0]['status'],'unconfirmed')
            self.assertNotIn('private-key',path.read_text())

    def test_valid_result_saves_image_energy_and_report(self):
        with tempfile.TemporaryDirectory() as folder:
            evidence,path,case=self.setup_case(folder)
            raw=b'\x89PNG\r\n\x1a\nfixture'
            def response(base,key,route,body):
                self.assertEqual(route,'/v1/generate')
                return {'request_id':body['request_id'],'status':'succeeded',
                    'image_url':'data:image/png;base64,'+base64.b64encode(raw).decode(),
                    'image_sha256':hashlib.sha256(raw).hexdigest(),
                    'energy':{'status':'measured','energy_j':360}}
            sampler=Mock();sampler.window.return_value=[]
            with patch.object(bench,'request',side_effect=response):
                row=bench.generate_case('https://test','private-key',case,Path(folder),evidence,path,sampler)
            self.assertEqual(row['energy_mwh'],100)
            self.assertEqual((Path(folder)/row['image']).read_bytes(),raw)
            summary=bench.report(path)
            self.assertEqual(summary['successful'],1)
            self.assertEqual(summary['quality'],'not_automatically_scored')
            self.assertTrue((Path(folder)/'report.html').exists())

    def test_bad_hash_is_unconfirmed_and_stops(self):
        with tempfile.TemporaryDirectory() as folder:
            evidence,path,case=self.setup_case(folder)
            def response(base,key,route,body):
                return {'request_id':body['request_id'],'status':'succeeded',
                    'image_url':'data:image/png;base64,'+base64.b64encode(b'\x89PNG\r\n\x1a\nfixture').decode(),
                    'image_sha256':'wrong'}
            with patch.object(bench,'request',side_effect=response):
                with self.assertRaises(RuntimeError):
                    bench.generate_case('https://test','key',case,Path(folder),evidence,path,Mock())
            self.assertEqual(evidence['results'][0]['status'],'unconfirmed')

    def test_report_escapes_prompts(self):
        with tempfile.TemporaryDirectory() as folder:
            evidence,path,case=self.setup_case(folder)
            evidence['results']=[{**case,'case_id':case['id'],'prompt':'<script>alert(1)</script>','status':'unconfirmed'}]
            bench.save(path,evidence);bench.report(path)
            page=(Path(folder)/'report.html').read_text()
            self.assertNotIn('<script>',page)
            self.assertIn('&lt;script&gt;',page)
