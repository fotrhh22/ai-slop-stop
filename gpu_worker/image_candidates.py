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

def generation_kwargs(candidate, prompt, seed, torch, device):
    kwargs = dict(prompt=prompt, width=1024, height=1024,
        num_inference_steps=candidate.steps, generator=torch.Generator(device=device).manual_seed(seed))
    if candidate.pipeline == 'QwenImagePipeline':
        kwargs.update(true_cfg_scale=1.0)  # Lightning: no positive/negative CFG double pass.
    else:
        kwargs['guidance_scale'] = candidate.guidance
    return kwargs


def load_pipeline(candidate, device, quantization=None, offload=False):
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
                  'offload':offload, 'width':1024, 'height':1024}
