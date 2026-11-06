"""Shared image profile and credential precedence; imports no model runtime."""
import os

IMAGE_MODEL = 'black-forest-labs/FLUX.2-klein-9B'
IMAGE_REVISION = '92196c8e11f7b6cf2b7493e037d8c5345c559216'
IMAGE_QUANTIZATION = 'nf4'


def shared_gpu_key(values=None):
    values = os.environ if values is None else values
    return (values.get('GPU_5090_API_KEY') or values.get('GPU_API_KEY')
            or values.get('GPU_4090_API_KEY', ''))
