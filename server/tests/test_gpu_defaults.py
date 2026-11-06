import os
from pathlib import Path
from tempfile import TemporaryDirectory
from dataclasses import replace
from unittest import TestCase
from unittest.mock import Mock, patch

from fastapi import HTTPException
from server.config import load_settings
from server.gpu_defaults import IMAGE_MODEL, IMAGE_REVISION
from server import profiles
from gpu_worker import volume


class Klein9BTests(TestCase):
    def test_stale_render_model_values_cannot_select_4b_and_keys_are_shared(self):
        with patch.dict(os.environ, {'IMAGE_PROVIDER':'zeus', 'GPU_IMAGE_MODEL':'old-4b',
            'GPU_4090_IMAGE_MODEL':'old-4b', 'GPU_5090_IMAGE_MODEL':'old-4b',
            'GPU_4090_API_KEY':'old4090', 'GPU_API_KEY':'oldglobal', 'GPU_5090_API_KEY':'s'*40}, clear=True):
            settings = load_settings()
        self.assertEqual(settings.image_model, IMAGE_MODEL)
        for profile in settings.gpu_profiles.values():
            self.assertEqual(profile['image_model'], IMAGE_MODEL)
            self.assertEqual(profile['image_revision'], IMAGE_REVISION)
            self.assertEqual(profile['image_quantization'], 'nf4')
            self.assertEqual(profile['key'], 's'*40)

    def test_registration_rejects_wrong_image_quantization_or_revision(self):
        with patch.dict(os.environ, {'GPU_5090_API_KEY':'s'*40}, clear=True):
            settings = load_settings()
        good = {'ok':True,'router_ready':True,'provider':'zeus','image_model':IMAGE_MODEL,
            'image_revision':IMAGE_REVISION,'image_quantization':'nf4',
            'router_model':'Qwen/Qwen3.5-4B','router_revision':'b'*40,
            'gpu_name':'NVIDIA GeForce RTX 5090','gpu_uuid':'GPU-5090','router_quantization':'bf16'}
        response = Mock()
        with patch.object(profiles.httpx,'get',return_value=response):
            response.json.return_value = good
            self.assertEqual(profiles.probe(settings,'open-5090','pod1234')['image_quantization'],'nf4')
            for wrong in [{'image_quantization':'bf16'},{'image_revision':'a'*40},{'image_model':'old-4b'}]:
                response.json.return_value = {**good,**wrong}
                with self.assertRaises(HTTPException):profiles.probe(settings,'open-5090','pod1234')

    def test_restored_4b_config_upgrades_model_without_erasing_key(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            key = 'k'*40
            (root/'worker.env').write_text('GPU_API_KEY='+key+'\nGPU_IMAGE_MODEL=old-4b\nGPU_MODEL_REVISION=old\n')
            with patch.object(volume,'ROOT',root),patch.object(volume,'WORKSPACE',root.parent):
                cfg = volume.configuration()
            self.assertEqual(cfg['GPU_API_KEY'],key)
            self.assertEqual(cfg['GPU_IMAGE_MODEL'],IMAGE_MODEL)
            self.assertEqual(cfg['GPU_MODEL_REVISION'],IMAGE_REVISION)
            self.assertEqual(cfg['GPU_IMAGE_QUANTIZATION'],'nf4')
