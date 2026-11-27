"""Proper scoring and reliability for model probabilities.

Desk rule: score probabilities with Brier + log loss + reliability, not
just W-L. Profit is secondary to side pass/fail; these metrics feed the
offline promote/kill loop.

The isotonic helper implements PAVA so tests can run without sklearn.
Weekly walk-forward fit against held-out weeks is an offline job — this
module does not load live slates or invent labels.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Sequence


@dataclass(frozen=True)
class ReliabilityBucket:
    lo: float
    hi: float
    n: int
    mean_p: float | None
    mean_y: float | None
    gap: float | None  # mean_p - mean_y; positive = overconfident

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ReliabilityReport:
    buckets: tuple[ReliabilityBucket, ...]
    ece: float | None
    n: int
    method: str = "equal_width"

    def to_dict(self) -> dict[str, Any]:
        return {
            "buckets": [b.to_dict() for b in self.buckets],
            "ece": self.ece,
            "n": self.n,
            "method": self.method,
        }


@dataclass
class IsotonicCalibrator:
    """PAVA isotonic regression (non-decreasing map p → calibrated p).

    Stub vs live
    ------------
    * Math is real and tested.
    * Production weekly fit must be walk-forward on held-out weeks only.
      Random CV across a season leaks future games — do not do that here.
    * ``fit`` refuses empty or single-class input rather than inventing a map.
    """

    x_thresholds: list[float] = field(default_factory=list)
    y_values: list[float] = field(default_factory=list)
    fitted: bool = False

    def fit(self, probs: Sequence[float], outcomes: Sequence[int | float]) -> "IsotonicCalibrator":
        p, y = _validate_pairs(probs, outcomes)
        order = sorted(range(len(p)), key=lambda i: (p[i], y[i]))
        xs = [p[i] for i in order]
        ys = [float(y[i]) for i in order]
        blocks = [[xs[i], xs[i], ys[i], 1.0] for i in range(len(xs))]
        # PAVA: pool adjacent violators (mean of y, width-weighted).
        i = 0
        while i < len(blocks) - 1:
            if blocks[i][2] <= blocks[i + 1][2] + 1e-15:
                i += 1
                continue
            left = blocks[i]
            right = blocks[i + 1]
            n = left[3] + right[3]
            mean = (left[2] * left[3] + right[2] * right[3]) / n
            merged = [left[0], right[1], mean, n]
            blocks[i : i + 2] = [merged]
            if i:
                i -= 1
        self.x_thresholds = [b[0] for b in blocks] + [blocks[-1][1]]
        self.y_values = [b[2] for b in blocks]
        self.fitted = True
        return self

    def predict(self, probs: Sequence[float]) -> list[float]:
        if not self.fitted:
            raise RuntimeError("IsotonicCalibrator.fit(...) before predict; no default map")
        out: list[float] = []
        for raw in probs:
            p = _as_prob(raw, name="prob")
            # Step function: last block whose left edge is <= p.
            idx = 0
            for j, left in enumerate(self.x_thresholds[:-1]):
                if p >= left:
                    idx = j
            out.append(self.y_values[min(idx, len(self.y_values) - 1)])
        return out


def brier_score(probs: Sequence[float], outcomes: Sequence[int | float]) -> float:
    """Mean squared error of probabilities. Lower is better. Bounded [0, 1]."""
    p, y = _validate_pairs(probs, outcomes)
    return sum((pi - yi) ** 2 for pi, yi in zip(p, y)) / len(p)


def log_loss(
    probs: Sequence[float],
    outcomes: Sequence[int | float],
    *,
    eps: float = 1e-15,
) -> float:
    """Binary log loss. Harsh on confident misses."""
    if not 0.0 < eps < 0.5:
        raise ValueError("eps must be in (0, 0.5)")
    p, y = _validate_pairs(probs, outcomes)
    total = 0.0
    for pi, yi in zip(p, y):
        clipped = min(1.0 - eps, max(eps, pi))
        total += -(yi * _log(clipped) + (1.0 - yi) * _log(1.0 - clipped))
    return total / len(p)


def reliability_buckets(
    probs: Sequence[float],
    outcomes: Sequence[int | float],
    *,
    n_buckets: int = 10,
) -> ReliabilityReport:
    """Equal-width reliability buckets on [0, 1] plus ECE (n-weighted |gap|)."""
    if n_buckets < 2:
        raise ValueError("n_buckets must be >= 2")
    p, y = _validate_pairs(probs, outcomes)
    width = 1.0 / n_buckets
    groups: list[list[tuple[float, float]]] = [[] for _ in range(n_buckets)]
    for pi, yi in zip(p, y):
        idx = min(n_buckets - 1, int(pi / width)) if pi < 1.0 else n_buckets - 1
        groups[idx].append((pi, yi))
    buckets: list[ReliabilityBucket] = []
    ece_num = 0.0
    for i, group in enumerate(groups):
        lo = i * width
        hi = 1.0 if i == n_buckets - 1 else (i + 1) * width
        if not group:
            buckets.append(ReliabilityBucket(lo=lo, hi=hi, n=0, mean_p=None, mean_y=None, gap=None))
            continue
        mean_p = sum(g[0] for g in group) / len(group)
        mean_y = sum(g[1] for g in group) / len(group)
        gap = mean_p - mean_y
        buckets.append(
            ReliabilityBucket(
                lo=lo,
                hi=hi,
                n=len(group),
                mean_p=mean_p,
                mean_y=mean_y,
                gap=gap,
            )
        )
        ece_num += len(group) * abs(gap)
    ece = ece_num / len(p)
    return ReliabilityReport(buckets=tuple(buckets), ece=ece, n=len(p))


def fit_isotonic(
    probs: Sequence[float],
    outcomes: Sequence[int | float],
) -> IsotonicCalibrator:
    """Fit the PAVA stub. Offline weekly job should call this on held-out weeks."""
    return IsotonicCalibrator().fit(probs, outcomes)


def _validate_pairs(
    probs: Sequence[float],
    outcomes: Sequence[int | float],
) -> tuple[list[float], list[float]]:
    if len(probs) != len(outcomes):
        raise ValueError("probs and outcomes must be the same length")
    if not probs:
        raise ValueError("Need at least one (prob, outcome) pair — will not invent labels")
    p = [_as_prob(v, name="prob") for v in probs]
    y: list[float] = []
    for raw in outcomes:
        val = float(raw)
        if val not in (0.0, 1.0):
            raise ValueError("outcomes must be 0 or 1 (side settled). Will not invent results")
        y.append(val)
    return p, y


def _as_prob(value: float, *, name: str) -> float:
    p = float(value)
    if not 0.0 <= p <= 1.0:
        raise ValueError(f"{name} must be in [0, 1]")
    return p


def _log(value: float) -> float:
    from math import log

    return log(value)
