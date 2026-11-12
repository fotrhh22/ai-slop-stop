import urllib.error
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch,MagicMock
from io import BytesIO
from PIL import Image
from .renderer import atkinson,escpos_bytes,render_receipt
from .worker import Journal,process,reconcile,submit

class WorkerTests(unittest.TestCase):
    def test_cups_job_id_supports_mac_korean_and_english_output(self):
        for text in ['요청 id는 PT210_USB_BRIDGE-12(1개의 파일)', 'request id is PT210_USB_BRIDGE-12 (1 file(s))']:
            with patch('printer_bridge.worker.subprocess.run',return_value=MagicMock(returncode=0,stdout=text)):
                self.assertEqual(submit(Path('receipt.escpos'),'PT210_USB_BRIDGE'),'PT210_USB_BRIDGE-12')

    def test_atkinson_reference_matches_production_kernel(self):
        image=Image.new('RGB',(3,3),(130,130,130))
        self.assertEqual(list(atkinson(image).getdata()),[255,0,255,255,0,0,0,255,255])
    def test_escpos_black_white_polarity_and_band_size(self):
        white=escpos_bytes(Image.new('1',(384,24),1))
        black=escpos_bytes(Image.new('1',(384,24),0))
        self.assertEqual(white[13:13+1152],bytes(1152))
        self.assertEqual(black[13:13+1152],b'\xff'*1152)
        self.assertNotIn(b'\x1dV',white) # no cutter
    def test_transmission_intent_is_not_resubmitted_on_recovery(self):
        with tempfile.TemporaryDirectory() as directory:
            journal=Journal(Path(directory)/'journal.json')
            job={'id':'job','lease_token':'lease'}
            journal.save(job,state='SENDING',acked=False)
            client=MagicMock()
            with patch('printer_bridge.worker.submit') as submit:
                reconcile(client,journal);reconcile(client,journal)
                submit.assert_not_called()
            self.assertEqual(client.call.call_count,1)
            self.assertEqual(client.call.call_args.args[1]['status'],'UNKNOWN')
    def test_spooled_job_ack_retry_never_prints_again(self):
        with tempfile.TemporaryDirectory() as directory:
            journal=Journal(Path(directory)/'journal.json');job={'id':'job','lease_token':'lease'}
            journal.save(job,state='SENT',cups_job_id='PT210_USB_BRIDGE-9',acked=False)
            client=MagicMock();client.call.side_effect=[OSError('network'),{}]
            with patch('printer_bridge.worker.submit') as submit:
                self.assertRaises(OSError,reconcile,client,journal)
                reconcile(client,journal);submit.assert_not_called()
            self.assertTrue(journal.data['job']['acked'])
    def test_lost_cups_response_is_unknown(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory);journal=Journal(path/'journal.json')
            job={'id':'job','lease_token':'lease','receipt_id':'receipt'}
            client=MagicMock();client.call.return_value={'receipt':{'id':'receipt','verified':True},'image_base64':'AA=='}
            with patch('printer_bridge.worker.render_receipt',return_value=Image.new('1',(384,24),1)),patch('printer_bridge.worker.submit',side_effect=TimeoutError):
                process(client,journal,job,path,'queue')
            self.assertEqual(journal.data['job']['state'],'UNKNOWN')
    def test_payload_network_failure_is_retried_before_single_submission(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory);journal=Journal(path/'journal.json')
            job={'id':'job','lease_token':'lease','receipt_id':'receipt'}
            client=MagicMock();client.call.side_effect=[urllib.error.URLError('temporary'),
                {'receipt':{'id':'receipt','verified':True},'image_base64':'AA=='},{},{}]
            with patch('printer_bridge.worker.render_receipt',return_value=Image.new('1',(384,24),1)), \
                    patch('printer_bridge.worker.time.sleep'), \
                    patch('printer_bridge.worker.submit',return_value='queue-1') as submit:
                process(client,journal,job,path,'queue')
                submit.assert_called_once()
            self.assertEqual(journal.data['job']['state'],'SENT')
            self.assertTrue(journal.data['job']['acked'])

    def test_exhausted_payload_retries_never_submit_and_report_payload_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory);journal=Journal(path/'journal.json')
            job={'id':'job','lease_token':'lease','receipt_id':'receipt'}
            client=MagicMock();client.call.side_effect=[urllib.error.URLError('temporary')]*3+[{}]
            with patch('printer_bridge.worker.time.sleep'),patch('printer_bridge.worker.submit') as submit:
                process(client,journal,job,path,'queue');submit.assert_not_called()
            self.assertEqual(journal.data['job']['state'],'FAILED')
            self.assertEqual(journal.data['job']['error_code'],'PAYLOAD_FAILED')
            self.assertEqual(journal.data['job']['error_stage'],'payload')

    def test_render_error_never_submits_to_cups(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory);journal=Journal(path/'journal.json')
            job={'id':'job','lease_token':'lease','receipt_id':'receipt'}
            client=MagicMock();client.call.return_value={}
            with patch('printer_bridge.worker.submit') as submit:
                process(client,journal,job,path,'queue');submit.assert_not_called()
            self.assertEqual(journal.data['job']['state'],'FAILED')

if __name__=='__main__':unittest.main()
