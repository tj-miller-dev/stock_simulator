"""Deterministic randomness derivation.

All randomness in the engine flows from SHA-256 over a scoped key string:

    gen{generation}:{seed}:{symbol}:{scope}

Scalar draws (gaps, scenario schedules, value noise) use hash_float/hash_norm
directly. Vector draws (a year of daily increments, a day of minute increments)
seed a numpy PCG64 generator from the same digest.

Determinism contract: within a generation, these streams must never change.
numpy is pinned in requirements.txt because PCG64/standard_normal streams are
stable in practice but not contractually guaranteed across numpy versions; the
golden-file tests in tests/test_golden.py are the tripwire, and the escape
hatch is bumping GENERATION, never silently changing gen 1 output.
"""

import hashlib
import math

import numpy as np

_TWO_53 = float(1 << 53)


def _digest(key: str) -> bytes:
    return hashlib.sha256(key.encode("utf-8")).digest()


def hash_float(key: str) -> float:
    """Uniform float in [0, 1), 53 bits from the digest."""
    value = int.from_bytes(_digest(key)[:8], "big") >> 11
    return value / _TWO_53


def hash_norm(key: str) -> float:
    """Standard normal via Box-Muller on two independent digest halves."""
    raw = _digest(key)
    u1 = ((int.from_bytes(raw[:8], "big") >> 11) + 1) / (_TWO_53 + 1)  # (0, 1]
    u2 = (int.from_bytes(raw[8:16], "big") >> 11) / _TWO_53
    return math.sqrt(-2.0 * math.log(u1)) * math.cos(2.0 * math.pi * u2)


def rng(key: str) -> np.random.Generator:
    """A PCG64 generator whose state is fully determined by the key."""
    raw = _digest(key)
    words = [int.from_bytes(raw[i : i + 8], "big") for i in range(0, 32, 8)]
    return np.random.Generator(np.random.PCG64(np.random.SeedSequence(words)))


def scope_key(generation: int, seed: str, symbol: str, scope: str) -> str:
    return f"gen{generation}:{seed}:{symbol}:{scope}"


def value_noise(x: float, key_prefix: str) -> float:
    """Continuous noise in [-1, 1]: smoothstep interpolation between hashed
    lattice values. Random-access -- O(1) per sample -- which is what lets the
    SSE demo clock price any instant without generating history."""
    i = math.floor(x)
    t = x - i
    a = hash_float(f"{key_prefix}:{i}") * 2.0 - 1.0
    b = hash_float(f"{key_prefix}:{i + 1}") * 2.0 - 1.0
    s = t * t * (3.0 - 2.0 * t)
    return a + (b - a) * s
