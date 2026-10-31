"""Statistics used by summaries, comparisons and the quality gate.

All resampling is seeded so the same inputs always produce the same intervals - a gate
verdict must be reproducible.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence

import numpy as np

StatFn = Callable[[np.ndarray], np.ndarray]  # maps (n_boot, n) samples -> (n_boot,) statistics

_MAX_CELLS = 4_000_000  # bootstrap matrix size per chunk (keeps memory bounded)


def mean_stat(samples: np.ndarray) -> np.ndarray:
    return samples.mean(axis=-1)


def percentile_stat(q: float) -> StatFn:
    def _stat(samples: np.ndarray) -> np.ndarray:
        return np.percentile(samples, q, axis=-1)

    return _stat


def _bootstrap_stats(columns: Sequence[np.ndarray], stat: StatFn, n_boot: int, seed: int) -> list[np.ndarray]:
    """Resample row indices (shared across columns, i.e. paired) and apply ``stat`` per column."""
    n = len(columns[0])
    rng = np.random.default_rng(seed)
    chunk = max(1, min(n_boot, _MAX_CELLS // max(n, 1)))
    out: list[list[np.ndarray]] = [[] for _ in columns]
    done = 0
    while done < n_boot:
        size = min(chunk, n_boot - done)
        idx = rng.integers(0, n, size=(size, n))
        for i, col in enumerate(columns):
            out[i].append(stat(col[idx]))
        done += size
    return [np.concatenate(parts) for parts in out]


def bootstrap_ci(
    values: Sequence[float] | np.ndarray,
    stat: StatFn = mean_stat,
    *,
    n_boot: int = 2000,
    conf: float = 0.95,
    seed: int = 0,
) -> tuple[float | None, float | None]:
    arr = np.asarray(values, dtype=float)
    if arr.size == 0:
        return None, None
    if n_boot <= 0 or arr.size == 1 or np.all(arr == arr[0]):
        point = float(stat(arr[None, :])[0])
        return point, point
    (boot,) = _bootstrap_stats([arr], stat, n_boot, seed)
    alpha = (1 - conf) / 2
    return float(np.quantile(boot, alpha)), float(np.quantile(boot, 1 - alpha))


def paired_bootstrap_diff(
    baseline: Sequence[float] | np.ndarray,
    candidate: Sequence[float] | np.ndarray,
    stat: StatFn = mean_stat,
    *,
    n_boot: int = 5000,
    conf: float = 0.95,
    seed: int = 0,
) -> tuple[float, float, float]:
    """Point estimate and CI of ``stat(candidate) - stat(baseline)`` over paired rows."""
    a = np.asarray(baseline, dtype=float)
    b = np.asarray(candidate, dtype=float)
    if a.shape != b.shape or a.size == 0:
        raise ValueError("paired_bootstrap_diff needs two equal-length, non-empty arrays")
    point = float(stat(b[None, :])[0] - stat(a[None, :])[0])
    if a.size == 1:
        return point, point, point
    boot_a, boot_b = _bootstrap_stats([a, b], stat, n_boot, seed)
    diffs = boot_b - boot_a
    alpha = (1 - conf) / 2
    return point, float(np.quantile(diffs, alpha)), float(np.quantile(diffs, 1 - alpha))


def unpaired_diff_ci(
    baseline: Sequence[float] | np.ndarray,
    candidate: Sequence[float] | np.ndarray,
    stat: StatFn = mean_stat,
    *,
    n_boot: int = 5000,
    conf: float = 0.95,
    seed: int = 0,
) -> tuple[float, float, float]:
    """Point estimate and CI of ``stat(candidate) - stat(baseline)`` for two independent samples.

    Used for metrics that aren't naturally paired per case, such as latency percentiles.
    """
    a = np.asarray(baseline, dtype=float)
    b = np.asarray(candidate, dtype=float)
    if a.size == 0 or b.size == 0:
        raise ValueError("unpaired_diff_ci needs two non-empty arrays")
    point = float(stat(b[None, :])[0] - stat(a[None, :])[0])
    if a.size == 1 and b.size == 1:
        return point, point, point
    rng = np.random.default_rng(seed)
    boot_a = _resample_stat(a, stat, n_boot, rng)
    boot_b = _resample_stat(b, stat, n_boot, rng)
    diffs = boot_b - boot_a
    alpha = (1 - conf) / 2
    return point, float(np.quantile(diffs, alpha)), float(np.quantile(diffs, 1 - alpha))


def _resample_stat(arr: np.ndarray, stat: StatFn, n_boot: int, rng: np.random.Generator) -> np.ndarray:
    n = len(arr)
    chunk = max(1, min(n_boot, _MAX_CELLS // max(n, 1)))
    parts: list[np.ndarray] = []
    done = 0
    while done < n_boot:
        size = min(chunk, n_boot - done)
        parts.append(stat(arr[rng.integers(0, n, size=(size, n))]))
        done += size
    return np.concatenate(parts)


def mcnemar_exact(n01: int, n10: int) -> float:
    """Two-sided exact McNemar p-value from the discordant pair counts.

    ``n01``: pass in baseline, fail in candidate; ``n10``: fail in baseline, pass in candidate.
    """
    n = n01 + n10
    if n == 0:
        return 1.0
    k = min(n01, n10)
    tail = sum(math.comb(n, i) for i in range(k + 1)) / 2**n
    return float(min(1.0, 2 * tail))


def wilson_interval(successes: int, n: int, conf: float = 0.95) -> tuple[float | None, float | None]:
    if n == 0:
        return None, None
    z = _z_for(conf)
    p = successes / n
    denom = 1 + z**2 / n
    centre = (p + z**2 / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z**2 / (4 * n**2)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


def _z_for(conf: float) -> float:
    # Inverse normal CDF via Acklam's rational approximation (enough precision for CIs).
    p = 1 - (1 - conf) / 2
    a = [-3.969683028665376e01, 2.209460984245205e02, -2.759285104469687e02, 1.383577518672690e02,
         -3.066479806614716e01, 2.506628277459239e00]  # fmt: skip
    b = [-5.447609879822406e01, 1.615858368580409e02, -1.556989798598866e02, 6.680131188771972e01,
         -1.328068155288572e01]  # fmt: skip
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e00, -2.549732539343734e00,
         4.374664141464968e00, 2.938163982698783e00]  # fmt: skip
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e00, 3.754408661907416e00]
    if p > 0.97575:
        q = math.sqrt(-2 * math.log(1 - p))
        return -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / (
            (((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1
        )
    q = p - 0.5
    r = q * q
    return (
        (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5])
        * q
        / (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1)
    )


def minimum_detectable_effect(diffs: Sequence[float] | np.ndarray, conf: float = 0.95) -> float | None:
    """Half-width of the CI on a paired mean difference: the eval's noise floor at this n."""
    arr = np.asarray(diffs, dtype=float)
    if arr.size < 2:
        return None
    return float(_z_for(conf) * arr.std(ddof=1) / math.sqrt(arr.size))
