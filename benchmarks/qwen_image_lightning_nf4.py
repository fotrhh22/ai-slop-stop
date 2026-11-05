"""Qwen-Image-2512: NF4 transformer + text encoder, pinned Lightning 4-step LoRA."""
from gpu_worker.candidate_benchmark import main
if __name__ == '__main__':
    main('qwen-image-lightning-nf4')
