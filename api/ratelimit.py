"""Keyless per-IP rate limiting: a token bucket per client address.

Buckets are per-pod and in-memory (deliberately -- the engine's statelessness
is a design principle, and with N replicas the effective ceiling is ~N x the
advertised limit, which we disclose in the docs rather than paper over with
shared state). Prune keeps the table bounded against address churn.
"""

import math
import time
from dataclasses import dataclass, field


@dataclass
class TokenBucketLimiter:
    capacity: float = 120.0        # burst headroom
    refill_per_second: float = 1.0  # 60/min sustained
    _buckets: dict[str, tuple[float, float]] = field(default_factory=dict)

    def check(self, key: str) -> tuple[bool, int, int]:
        """Spend one token. Returns (allowed, remaining, reset_seconds)."""
        now = time.monotonic()
        tokens, last = self._buckets.get(key, (self.capacity, now))
        tokens = min(self.capacity, tokens + (now - last) * self.refill_per_second)
        allowed = tokens >= 1.0
        if allowed:
            tokens -= 1.0
        self._buckets[key] = (tokens, now)
        if len(self._buckets) > 20_000:
            self._prune(now)
        reset = 0 if tokens >= 1.0 else math.ceil((1.0 - tokens) / self.refill_per_second)
        return allowed, int(tokens), reset

    def _prune(self, now: float) -> None:
        # Anything idle long enough to have refilled completely carries no
        # information -- drop it.
        idle = self.capacity / self.refill_per_second
        self._buckets = {
            k: v for k, v in self._buckets.items() if now - v[1] < idle
        }

    def reset(self) -> None:
        self._buckets.clear()


def client_ip(request) -> str:
    """Behind the ALB the client address arrives in X-Forwarded-For; the ALB
    appends to any inbound value, so the *last* entry is the address the ALB
    actually saw and the only one we can trust."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[-1].strip()
    return request.client.host if request.client else "unknown"
