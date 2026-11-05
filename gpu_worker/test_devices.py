"""CPU-only checks for explicit model placement; no GPU/model downloads."""
import os
from unittest import TestCase
from unittest.mock import Mock, patch
from gpu_worker.app import model_device

class DeviceTests(TestCase):
    def test_default_placement_single_and_dual(self):
        torch = Mock()
        with patch.dict(os.environ, {}, clear=True):
            torch.cuda.device_count.return_value = 1
            self.assertEqual(model_device(torch, 'IMAGE'), (0, 'cuda:0'))
            self.assertEqual(model_device(torch, 'ROUTER'), (0, 'cuda:0'))
            torch.cuda.device_count.return_value = 2
            self.assertEqual(model_device(torch, 'IMAGE'), (0, 'cuda:0'))
            self.assertEqual(model_device(torch, 'ROUTER'), (1, 'cuda:1'))
            torch.cuda.set_device.assert_called_with(1)

    def test_dual_gpu_cannot_silently_put_both_models_on_one_device(self):
        torch = Mock()
        torch.cuda.device_count.return_value = 2
        with patch.dict(os.environ, {'GPU_IMAGE_DEVICE':'0','GPU_ROUTER_DEVICE':'0'}, clear=True):
            with self.assertRaises(RuntimeError): model_device(torch, 'ROUTER')
