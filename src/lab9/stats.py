"""Statistics students implement (M2 judge calibration, M4 paired bootstrap).

Rules: a judge is a classifier -- measure it like one. A higher mean is not
"better" until the paired CI of the difference excludes 0.
Reference values to self-check are in tests/test_lab9_stats.py.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

POS = "FAIL"   # the judge's job is to FIND failures, so FAIL is the positive class


def confusion(human: Sequence[str], judge: Sequence[str]) -> dict[str, int]:
    """tp/fp/fn/tn with FAIL as positive."""
    if len(human) != len(judge):
        raise ValueError("human and judge must have the same length")
    tp = fp = fn = tn = 0
    for h, j in zip(human, judge):
        if j == POS:
            if h == POS:
                tp += 1
            else:
                fp += 1
        elif h == POS:
            fn += 1
        else:
            tn += 1
    return {"tp": tp, "fp": fp, "fn": fn, "tn": tn}


def precision_recall(human: Sequence[str], judge: Sequence[str]) -> tuple[float, float]:
    cm = confusion(human, judge)
    pred_pos = cm["tp"] + cm["fp"]
    act_pos = cm["tp"] + cm["fn"]
    precision = cm["tp"] / pred_pos if pred_pos else 0.0
    recall = cm["tp"] / act_pos if act_pos else 0.0
    return precision, recall


def cohen_kappa(human: Sequence[str], judge: Sequence[str]) -> float:
    """kappa = (p_o - p_e) / (1 - p_e). Raise ValueError when undefined (p_e == 1):
    a constant judge has no signal and must FAIL calibration, not pass on a technicality."""
    cm = confusion(human, judge)
    n = sum(cm.values())
    if n == 0:
        raise ValueError("kappa undefined for empty input")
    p_o = (cm["tp"] + cm["tn"]) / n
    # Chance agreement from the marginals of both raters (positive and negative class).
    p_e = ((cm["tp"] + cm["fp"]) * (cm["tp"] + cm["fn"])
           + (cm["tn"] + cm["fn"]) * (cm["tn"] + cm["fp"])) / (n * n)
    if p_e >= 1.0:
        raise ValueError("kappa undefined (p_e == 1): no signal")
    return (p_o - p_e) / (1.0 - p_e)


def rogan_gladen(observed_rate: float, sensitivity: float, specificity: float) -> float:
    """Correct an observed failure rate for judge error; clip to [0, 1]."""
    denom = sensitivity + specificity - 1.0
    if denom <= 0:
        raise ValueError("judge no better than chance (sens + spec <= 1): cannot correct")
    return min(1.0, max(0.0, (observed_rate + specificity - 1.0) / denom))


def paired_bootstrap(a: Sequence[float], b: Sequence[float], *, n_resamples: int = 1000,
                     rng: int = 2027, confidence: float = 0.95) -> tuple[float, float, float]:
    """a[i], b[i] = per-item mean over seeds, SAME items, SAME order.
    Return (mean(a)-mean(b), lo, hi) with scipy.stats.bootstrap(paired=True, method="percentile",
    rng=numpy.random.default_rng(rng)). If a == b everywhere return (0.0, 0.0, 0.0)."""
    import numpy as np
    from scipy import stats as sps

    if len(a) != len(b):
        raise ValueError("a and b must pair the same items in the same order")
    if len(a) == 0:
        raise ValueError("paired_bootstrap needs at least one item")
    x, y = np.asarray(a, dtype=float), np.asarray(b, dtype=float)
    diff = x - y
    # Identical inputs: every resample has difference 0, and scipy's percentile
    # method would warn / return NaN on the degenerate (zero-variance) case.
    if not np.any(diff):
        return 0.0, 0.0, 0.0

    def mean_diff(u, v, axis=-1):
        # vectorized statistic: scipy passes resampled arrays of shape (..., n)
        return np.mean(u, axis=axis) - np.mean(v, axis=axis)

    res = sps.bootstrap((x, y), mean_diff, paired=True, vectorized=True,
                        n_resamples=n_resamples, confidence_level=confidence,
                        method="percentile", rng=np.random.default_rng(rng))
    ci = res.confidence_interval
    return float(diff.mean()), float(ci.low), float(ci.high)


def pass_at_k(n: int, c: int, k: int) -> float:
    """Probability that AT LEAST ONE of k trials drawn from n (c of them passing) passes.
    Unbiased estimator (Chen et al. 2021): 1 - C(n-c, k) / C(n, k). Raise ValueError if k > n."""
    _check_nck(n, c, k)
    if n - c < k:
        return 1.0                       # fewer than k failures: every k-subset contains a pass
    return 1.0 - math.comb(n - c, k) / math.comb(n, k)


def _check_nck(n: int, c: int, k: int) -> None:
    """Reject malformed (n, c, k): non-int (bool/float included), negative, c > n, k < 1 or k > n."""
    for name, v in (("n", n), ("c", c), ("k", k)):
        if isinstance(v, bool) or not isinstance(v, int):
            raise ValueError(f"{name} must be an int, got {v!r}")
    if n < 1 or c < 0 or k < 1:
        raise ValueError(f"need n >= 1, c >= 0, k >= 1; got n={n}, c={c}, k={k}")
    if c > n:
        raise ValueError(f"c={c} passes cannot exceed n={n} trials")
    if k > n:
        raise ValueError(f"k={k} cannot exceed n={n} trials")


def pass_hat_k(n: int, c: int, k: int) -> float:
    """pass^k (tau-bench, Yao et al. 2024): probability that ALL k trials drawn from n pass.
    Unbiased estimator: C(c, k) / C(n, k). Raise ValueError if k > n.
    pass^k <= pass@1 <= pass@k; a reliable agent keeps pass^k close to pass@1."""
    _check_nck(n, c, k)
    return math.comb(c, k) / math.comb(n, k)    # 0.0 when c < k (math.comb returns 0)
