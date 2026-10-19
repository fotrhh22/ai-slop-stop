"""Regenerate the fixed 64x64 ordered blue-noise rank tile (development only: numpy).

Void-and-cluster, periodic Gaussian sigma=1.5, deterministic seed.
Reference: https://cv.ulichney.com/papers/1993-void-cluster.pdf
Runtime uses the generated JSON, with no Python or random-number dependency.
"""
from pathlib import Path
import json
import numpy as np

SIZE, SIGMA = 64, 1.5
COUNT = SIZE * SIZE
rng = np.random.default_rng(4173)
axis = np.minimum(np.arange(SIZE), SIZE - np.arange(SIZE))
kernel = np.exp(-(axis[:, None] ** 2 + axis[None, :] ** 2) / (2 * SIGMA ** 2))

def density(bits):
    return np.fft.ifft2(np.fft.fft2(bits) * np.fft.fft2(kernel)).real

def contribution(index):
    y, x = divmod(int(index), SIZE)
    return np.roll(kernel, (y, x), axis=(0, 1))

def cluster(bits, energy):
    return int(np.argmax(np.where(bits, energy, -np.inf)))

def void(bits, energy):
    return int(np.argmin(np.where(bits, np.inf, energy)))

initial = np.zeros((SIZE, SIZE), dtype=bool)
initial.flat[rng.choice(COUNT, COUNT // 10, replace=False)] = True
energy = density(initial)
for _ in range(100_000):
    old = cluster(initial, energy)
    initial.flat[old] = False
    energy -= contribution(old)
    new = void(initial, energy)
    initial.flat[new] = True
    energy += contribution(new)
    if new == old:
        break
else:
    raise RuntimeError("Void-and-cluster did not converge")

ranks = np.full((SIZE, SIZE), -1, dtype=int)
minority = int(initial.sum())
bits, energy = initial.copy(), density(initial)
for rank in range(minority - 1, -1, -1):
    index = cluster(bits, energy)
    ranks.flat[index] = rank
    bits.flat[index] = False
    energy -= contribution(index)
bits, energy = initial.copy(), density(initial)
for rank in range(minority, COUNT // 2):
    index = void(bits, energy)
    ranks.flat[index] = rank
    bits.flat[index] = True
    energy += contribution(index)
bits = ~bits
energy = density(bits)
for rank in range(COUNT // 2, COUNT):
    index = cluster(bits, energy)
    ranks.flat[index] = rank
    bits.flat[index] = False
    energy -= contribution(index)
assert np.array_equal(np.sort(ranks.ravel()), np.arange(COUNT))
# Check suppression of low spatial frequencies against white noise at several tones.
freq = np.fft.fftfreq(SIZE) * SIZE
radius = np.hypot(freq[:, None], freq[None, :])
low = (radius > 0) & (radius < 8)
for tone in (0.1, 0.25, 0.5, 0.75, 0.9):
    pattern = ranks < int(tone * COUNT)
    shuffled = rng.permutation(pattern.ravel()).reshape(pattern.shape)
    ratio = (np.abs(np.fft.fft2(pattern))[low] ** 2).mean() / (np.abs(np.fft.fft2(shuffled))[low] ** 2).mean()
    assert ratio < 0.25, (tone, ratio)
    print(f"tone={tone:.2f}: low-frequency power / shuffled={ratio:.3f}")
path = Path(__file__).resolve().parents[1] / "src/utils/blue-noise-64.json"
path.write_text(json.dumps(ranks.ravel().tolist(), separators=(",", ":")) + "\n")
