"""Identify a worker's explicit pre-inference authentication rejection."""


class GPUAuthRejected(Exception):
    pass


def check_gpu_auth(response):
    if response.status_code not in (401, 403):
        return
    try:
        rejected = response.json().get('detail') == 'GPU_AUTH_DENIED'
    except (ValueError, AttributeError):
        rejected = False
    # Generic proxy 403/HTML errors do not prove the GPU did not execute.
    if rejected:
        raise GPUAuthRejected('GPU_AUTH_DENIED')
