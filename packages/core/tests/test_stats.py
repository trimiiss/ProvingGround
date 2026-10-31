from __future__ import annotations

import numpy as np

from provingground.stats import (
    bootstrap_ci,
    mcnemar_exact,
    minimum_detectable_effect,
    paired_bootstrap_diff,
    percentile_stat,
    wilson_interval,
)


def test_bootstrap_ci_is_seeded_and_reproducible() -> None:
    values = [0.0, 1.0, 1.0, 0.0, 1.0, 1.0, 0.0, 1.0]
    a = bootstrap_ci(values, seed=1)
    b = bootstrap_ci(values, seed=1)
    assert a == b
    assert a[0] is not None and a[1] is not None and a[0] <= np.mean(values) <= a[1]


def test_bootstrap_ci_constant_and_empty() -> None:
    assert bootstrap_ci([0.5, 0.5, 0.5]) == (0.5, 0.5)
    assert bootstrap_ci([]) == (None, None)
    assert bootstrap_ci([0.3, 0.7, 0.5], n_boot=0) == (0.5, 0.5)


def test_paired_bootstrap_diff_direction() -> None:
    baseline = [0.0] * 10 + [1.0] * 10
    candidate = [1.0] * 20
    point, lo, hi = paired_bootstrap_diff(baseline, candidate, seed=3)
    assert point == 0.5
    assert lo > 0 and hi >= point


def test_mcnemar_exact() -> None:
    assert mcnemar_exact(0, 0) == 1.0
    assert mcnemar_exact(10, 0) < 0.05  # all discordant pairs go one way
    assert mcnemar_exact(5, 5) == 1.0


def test_wilson_interval() -> None:
    lo, hi = wilson_interval(8, 10)
    assert lo is not None and 0 <= lo < 0.8 < hi <= 1.0
    assert wilson_interval(0, 0) == (None, None)


def test_percentile_stat_and_mde() -> None:
    arr = np.arange(0, 100, dtype=float)
    assert abs(percentile_stat(95)(arr[None, :])[0] - np.percentile(arr, 95)) < 1e-9
    mde = minimum_detectable_effect([0.1, -0.1, 0.0, 0.2, -0.2])
    assert mde is not None and mde > 0
    assert minimum_detectable_effect([0.1]) is None
