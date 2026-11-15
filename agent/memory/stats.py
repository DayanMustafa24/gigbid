"""Small pure statistics used by outcome memory and the X2 proposal-experiments analytics (Phase 9). No dependencies."""
from __future__ import annotations

import math


def posterior_rate(successes: int, n: int, prior_rate: float, prior_strength: float) -> float:
    """Beta-posterior mean with a prior worth `prior_strength` pseudo-observations: a few outcomes cannot swing the estimate."""
    return (successes + prior_rate * prior_strength) / (n + prior_strength) if (n + prior_strength) else prior_rate


def two_proportion_z(x1: int, n1: int, x2: int, n2: int) -> tuple[float, float]:
    """Pooled two-proportion z-test. Returns (z, two-sided p). (0.0, 1.0) when it is undefined (an empty arm or no variance)."""
    if n1 <= 0 or n2 <= 0:
        return 0.0, 1.0
    p = (x1 + x2) / (n1 + n2)
    se = math.sqrt(p * (1 - p) * (1 / n1 + 1 / n2))
    if se == 0:
        return 0.0, 1.0
    z = (x1 / n1 - x2 / n2) / se
    return z, math.erfc(abs(z) / math.sqrt(2))
