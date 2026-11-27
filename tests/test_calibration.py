"""Brier, log loss, reliability, isotonic PAVA — no live labels invented."""

from __future__ import annotations

from math import log

import pytest

from prediction_core.calibration import (
    brier_score,
    fit_isotonic,
    log_loss,
    reliability_buckets,
)


def test_brier_known_value():
    assert brier_score([0.7], [1]) == pytest.approx(0.09)
    assert brier_score([0.0, 1.0], [0, 1]) == pytest.approx(0.0)


def test_log_loss_coin_flip():
    assert log_loss([0.5], [1]) == pytest.approx(-log(0.5))


def test_log_loss_confident_miss_is_large():
    miss = log_loss([0.99], [0])
    hit = log_loss([0.99], [1])
    assert miss > hit
    assert miss > 3.0


def test_reliability_buckets_ece():
    report = reliability_buckets([0.2, 0.2, 0.8, 0.8], [0, 0, 1, 1], n_buckets=5)
    assert report.n == 4
    assert report.ece == pytest.approx(0.2)
    filled = [b for b in report.buckets if b.n]
    assert all(b.mean_p is not None for b in filled)


def test_isotonic_is_non_decreasing():
    # Classic inversion: higher p with worse outcomes gets pooled.
    probs = [0.1, 0.4, 0.6, 0.9]
    outcomes = [0, 1, 0, 1]
    cal = fit_isotonic(probs, outcomes)
    fitted = cal.predict(probs)
    assert cal.fitted
    assert fitted == sorted(fitted)
    assert all(0.0 <= v <= 1.0 for v in fitted)


def test_calibration_refuses_empty():
    with pytest.raises(ValueError, match="invent"):
        brier_score([], [])
