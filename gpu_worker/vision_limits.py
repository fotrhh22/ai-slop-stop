"""Bound Qwen processor output, not just the dimensions of the input PIL images."""
import math
import os


def configure(processor):
    pixels = int(os.environ.get('GPU_ROUTER_MAX_PIXELS', str(512*512)))
    if not 256*256 <= pixels <= 1024*1024:
        raise ValueError('GPU_ROUTER_MAX_PIXELS must be between 65536 and 1048576 per image')
    image_processor = processor.image_processor
    image_processor.size = {'shortest_edge':256*256, 'longest_edge':pixels}
    # Current Qwen3.5 uses the Qwen3-VL processor. Old max_pixels kwargs alone
    # can be deprecated; size is its actual preprocessing budget.
    return pixels


def verify(inputs, processor, pixels, expected_images=2):
    grid = inputs['image_grid_thw'].tolist()
    if len(grid) != expected_images:
        raise ValueError('Unexpected image count in Qwen vision inputs')
    patch = processor.image_processor.patch_size
    merge = processor.image_processor.merge_size
    limit = pixels // ((patch * merge) ** 2)
    tokens = [math.prod(dims) // (merge ** 2) for dims in grid]
    if any(t > limit for t in tokens):
        raise ValueError('Qwen processor exceeded the configured per-image vision token budget')
    return tokens
