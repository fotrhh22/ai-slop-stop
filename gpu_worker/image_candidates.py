"""Pinned, isolated image candidate configurations. No implicit prompt rewriting."""
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class Candidate:
    model: str
    revision: str
    pipeline: str
    steps: int
    guidance: float
    quantization: str
    lightning_repo: str = ''
    lightning_revision: str = ''
    lightning_file: str = ''


CANDIDATES = {
    'z-image-turbo': Candidate('Tongyi-MAI/Z-Image-Turbo',
        'f332072aa78be7aecdf3ee76d5c247082da564a6', 'ZImagePipeline', 9, 0.0, 'bf16'),
    'klein-9b-nf4': Candidate('black-forest-labs/FLUX.2-klein-9B',
        '92196c8e11f7b6cf2b7493e037d8c5345c559216', 'Flux2KleinPipeline', 4, 1.0, 'nf4'),
    'qwen-image-lightning-nf4': Candidate('Qwen/Qwen-Image-2512',
        '25468b98e3276ca6700de15c6628e51b7de54a26', 'QwenImagePipeline', 4, 1.0, 'nf4',
        'lightx2v/Qwen-Image-2512-Lightning', '4609161e308ce82018a42c2a0c9b107e865bb751',
        'Qwen-Image-2512-Lightning-4steps-V1.0-bf16.safetensors'),
}


CANDIDATES = {name:CANDIDATES[name] for name in ('z-image-turbo','qwen-image-lightning-nf4','klein-9b-nf4')}

# LightX2V's Qwen-Image-2512 distillation uses shift=3 and no terminal shift.
LIGHTNING_SCHEDULER = {
    'base_image_seq_len':256, 'base_shift':1.0986122886681098,
    'invert_sigmas':False, 'max_image_seq_len':8192, 'max_shift':1.0986122886681098,
    'num_train_timesteps':1000, 'shift':1.0, 'shift_terminal':None,
    'stochastic_sampling':False, 'time_shift_type':'exponential',
    'use_beta_sigmas':False, 'use_dynamic_shifting':True,
    'use_exponential_sigmas':False, 'use_karras_sigmas':False,
}

def generation_kwargs(candidate, prompt, seed, torch, device):
    kwargs = dict(prompt=prompt, width=1024, height=1024,
        num_inference_steps=candidate.steps, generator=torch.Generator(device=device).manual_seed(seed))
    if candidate.pipeline == 'QwenImagePipeline':
        kwargs.update(true_cfg_scale=1.0)  # Lightning: no positive/negative CFG double pass.
    else:
        kwargs['guidance_scale'] = candidate.guidance
    return kwargs


def load_pipeline(candidate, device, quantization=None, offload=False, stream_cache=False):
    import torch
    import diffusers
    from diffusers.quantizers import PipelineQuantizationConfig
    quantization = quantization or candidate.quantization
    if quantization not in ('bf16', 'nf4'):
        raise ValueError('Use bf16 or nf4')
    kwargs = dict(revision=candidate.revision, torch_dtype=torch.bfloat16, use_safetensors=True)
    if quantization == 'nf4':
        kwargs['quantization_config'] = PipelineQuantizationConfig(
            quant_backend='bitsandbytes_4bit',
            quant_kwargs=dict(load_in_4bit=True, bnb_4bit_quant_type='nf4',
                bnb_4bit_compute_dtype=torch.bfloat16, bnb_4bit_use_double_quant=True),
            components_to_quantize=['transformer', 'text_encoder'])
    torch.cuda.set_device(device)
    if stream_cache and candidate.pipeline == 'QwenImagePipeline' and quantization == 'nf4':
        # A 50GB ephemeral disk cannot hold the complete BF16 transformer and
        # encoder at once. Quantize the transformer first, then discard only
        # this run's explicitly owned download cache before fetching the encoder.
        import gc
        import os
        import shutil
        from pathlib import Path
        from diffusers import BitsAndBytesConfig, QwenImageTransformer2DModel
        home = Path(os.environ['HF_HOME']).resolve()
        if not (home / '.candidate-owned-cache').is_file():
            raise RuntimeError('Streaming requires a private benchmark-owned cache')
        cache = home / 'hub'
        transformer = QwenImageTransformer2DModel.from_pretrained(candidate.model,
            revision=candidate.revision, subfolder='transformer', cache_dir=str(cache),
            torch_dtype=torch.bfloat16, device_map={'':device}, low_cpu_mem_usage=True,
            quantization_config=BitsAndBytesConfig(load_in_4bit=True,bnb_4bit_quant_type='nf4',
                bnb_4bit_compute_dtype=torch.bfloat16,bnb_4bit_use_double_quant=True))
        torch.cuda.synchronize(device)
        gc.collect()
        if cache.exists():shutil.rmtree(cache)
        kwargs['transformer'] = transformer
        kwargs['quantization_config'] = PipelineQuantizationConfig(
            quant_backend='bitsandbytes_4bit',
            quant_kwargs=dict(load_in_4bit=True,bnb_4bit_quant_type='nf4',
                bnb_4bit_compute_dtype=torch.bfloat16,bnb_4bit_use_double_quant=True),
            components_to_quantize=['text_encoder'])
    if candidate.lightning_repo:
        kwargs['scheduler'] = diffusers.FlowMatchEulerDiscreteScheduler.from_config(LIGHTNING_SCHEDULER)
    pipe = getattr(diffusers, candidate.pipeline).from_pretrained(candidate.model, **kwargs)
    if candidate.lightning_repo:
        pipe.load_lora_weights(candidate.lightning_repo, revision=candidate.lightning_revision,
                               weight_name=candidate.lightning_file, adapter_name='lightning')
    if offload:
        pipe.enable_model_cpu_offload(gpu_id=device)
    else:
        pipe.to('cuda:' + str(device))
    pipe.set_progress_bar_config(disable=True)
    return pipe, {**asdict(candidate), 'quantization':quantization,
                  'quantized_components':['transformer','text_encoder'] if quantization=='nf4' else [],
                  'offload':offload, 'streamed_download_cache':bool(stream_cache and candidate.pipeline=='QwenImagePipeline' and quantization=='nf4'),
                  'lightning_scheduler':dict(LIGHTNING_SCHEDULER) if candidate.lightning_repo else None, 'width':1024, 'height':1024}
