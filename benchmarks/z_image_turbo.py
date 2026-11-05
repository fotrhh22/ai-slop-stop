"""Z-Image-Turbo: BF16 baseline, optional NF4, 9 scheduler steps (8 effective NFEs)."""
from gpu_worker.candidate_benchmark import main
if __name__ == '__main__':
    main('z-image-turbo')
