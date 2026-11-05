from pathlib import Path
import tempfile
import json
from types import SimpleNamespace as NS
from unittest import TestCase
from unittest.mock import Mock, patch
import sys

from gpu_worker.image_candidates import CANDIDATES, generation_kwargs, load_pipeline
from gpu_worker import vision_limits


class CandidateTests(TestCase):
    def test_same_prompt_and_seed_with_model_specific_sampling(self):
        torch=Mock();torch.Generator.return_value.manual_seed.return_value='generator'
        for name,c in CANDIDATES.items():
            kwargs=generation_kwargs(c,'악어 머리 은색 폭격기',42,torch,'cuda:0')
            self.assertEqual(kwargs['prompt'],'악어 머리 은색 폭격기')
            self.assertEqual((kwargs['width'],kwargs['height']),(1024,1024))
            if name.startswith('qwen'):
                self.assertEqual(kwargs['true_cfg_scale'],1.0)
                self.assertNotIn('guidance_scale',kwargs)
            else:self.assertIn('guidance_scale',kwargs)
        torch.Generator.return_value.manual_seed.assert_called_with(42)

    def test_nf4_quantizes_both_components_and_pins_lightning_revision(self):
        torch=Mock();diffusers=Mock();quant=Mock()
        c=CANDIDATES['qwen-image-lightning-nf4']
        pipe=diffusers.QwenImagePipeline.from_pretrained.return_value
        with patch.dict(sys.modules,{'torch':torch,'diffusers':diffusers,'diffusers.quantizers':quant}):
            loaded,metadata=load_pipeline(c,0)
        self.assertIs(loaded,pipe)
        self.assertEqual(quant.PipelineQuantizationConfig.call_args.kwargs['components_to_quantize'],['transformer','text_encoder'])
        self.assertEqual(quant.PipelineQuantizationConfig.call_args.kwargs['quant_kwargs']['bnb_4bit_quant_type'],'nf4')
        pipe.load_lora_weights.assert_called_once_with(c.lightning_repo,revision=c.lightning_revision,
            weight_name=c.lightning_file,adapter_name='lightning')
        pipe.to.assert_called_once_with('cuda:0')
        self.assertEqual(metadata['quantization'],'nf4')
        self.assertEqual(diffusers.QwenImagePipeline.from_pretrained.call_args.kwargs['revision'],c.revision)

    def test_z_image_bf16_does_not_load_unrelated_lightning_or_quantizer(self):
        torch=Mock();diffusers=Mock();quant=Mock()
        with patch.dict(sys.modules,{'torch':torch,'diffusers':diffusers,'diffusers.quantizers':quant}):
            load_pipeline(CANDIDATES['z-image-turbo'],1)
        pipe=diffusers.ZImagePipeline.from_pretrained.return_value
        pipe.load_lora_weights.assert_not_called()
        quant.PipelineQuantizationConfig.assert_not_called()
        pipe.to.assert_called_once_with('cuda:1')

    def test_offload_is_explicit_and_recorded(self):
        torch=Mock();diffusers=Mock();quant=Mock()
        with patch.dict(sys.modules,{'torch':torch,'diffusers':diffusers,'diffusers.quantizers':quant}):
            pipe,metadata=load_pipeline(CANDIDATES['klein-9b-nf4'],0,offload=True)
        pipe.enable_model_cpu_offload.assert_called_once_with(gpu_id=0)
        pipe.to.assert_not_called()
        self.assertTrue(metadata['offload'])


class VisionTests(TestCase):
    def test_actual_processor_grid_is_bounded_before_gpu_transfer(self):
        processor=NS(image_processor=NS(patch_size=16,merge_size=2))
        with patch.dict('os.environ',{'GPU_ROUTER_MAX_PIXELS':'262144'}):
            pixels=vision_limits.configure(processor)
        self.assertEqual(processor.image_processor.size,{'shortest_edge':65536,'longest_edge':262144})
        grid=Mock();grid.tolist.return_value=[[1,32,32],[1,16,32]]
        self.assertEqual(vision_limits.verify({'image_grid_thw':grid},processor,pixels),[256,128])
        grid.tolist.return_value=[[1,64,64],[1,32,32]]
        with self.assertRaises(ValueError):vision_limits.verify({'image_grid_thw':grid},processor,pixels)

    def test_rejects_unbounded_or_invalid_pixel_configuration(self):
        for value in ['0','16777216','-1']:
            with patch.dict('os.environ',{'GPU_ROUTER_MAX_PIXELS':value}):
                with self.assertRaises(ValueError):vision_limits.configure(NS())
