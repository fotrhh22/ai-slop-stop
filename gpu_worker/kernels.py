"""Check Qwen3.5 CUDA fast-path imports without loading a model or running inference."""
import json
from importlib.metadata import version


def check():
    import torch
    import triton
    from causal_conv1d import causal_conv1d_fn, causal_conv1d_update
    from fla.ops.gated_delta_rule import chunk_gated_delta_rule, fused_recurrent_gated_delta_rule
    if torch.__version__ != '2.8.0+cu128' or torch.version.cuda != '12.8' or triton.__version__ != '3.4.0':
        raise RuntimeError('Kernel installation changed the validated Torch/CUDA/Triton runtime')
    if not all(callable(fn) for fn in (causal_conv1d_fn, causal_conv1d_update,
                                      chunk_gated_delta_rule, fused_recurrent_gated_delta_rule)):
        raise RuntimeError('Qwen fast-path functions unavailable')
    return {name: version(name) for name in
            ('flash-linear-attention', 'fla-core', 'causal-conv1d', 'torch', 'triton')}


if __name__ == '__main__':
    print(json.dumps({'qwen_fast_path_imports': check()}))
