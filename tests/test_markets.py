"""Market math: de-vig, edge vs model, CLV. Fixture prices only."""

from __future__ import annotations

import pytest

from prediction_core.markets import (
    american_to_decimal,
    american_to_implied,
    best_american,
    clv_grade,
    join_open_best_close,
    probability_edge,
    spread_point_edge,
    two_way_devig,
)


def test_american_to_implied_known_prices():
    assert american_to_implied(100) == pytest.approx(0.5)
    assert american_to_implied(-110) == pytest.approx(110 / 210)
    assert american_to_implied(150) == pytest.approx(0.4)
    assert american_to_implied(-200) == pytest.approx(2 / 3)


def test_multiplicative_devig_minus_110_both_sides():
    fair = two_way_devig(-110, -110)
    assert fair.p_a == pytest.approx(0.5)
    assert fair.p_b == pytest.approx(0.5)
    assert fair.overround == pytest.approx(2 * (110 / 210))
    assert fair.method == "multiplicative"


def test_probability_edge_in_points():
    # model 65% vs fair 55% → 10 percentage points (MLB +ML bar uses this scale)
    assert probability_edge(0.65, 0.55, as_points=True) == pytest.approx(10.0)
    assert probability_edge(0.65, 0.55, as_points=False) == pytest.approx(0.10)


def test_spread_point_edge_home_favorite():
    # model μ = 6, market home -3 → market μ = 3 → edge +3 pts
    assert spread_point_edge(6.0, -3.0) == pytest.approx(3.0)


def test_best_american_is_max():
    assert best_american([-110, -105, 100]) == pytest.approx(100)


def test_join_open_best_close_does_not_invent_close():
    joined = join_open_best_close(
        {"american": -110, "book": "FIXTURE-Open", "ts": "2026-09-17T12:00:00Z"},
        {"american": -105, "book": "FIXTURE-Shop", "ts": "2026-09-18T12:00:00Z"},
        None,
    )
    assert joined.shop_price == pytest.approx(-105)
    assert joined.close is None
    assert joined.intended_close_book is None
    payload = joined.to_dict()
    assert payload["close"] is None


def test_clv_beat_close_is_positive():
    # Fixture: bet +150, close +120 — longer bet price than close.
    grade = clv_grade(150, 120)
    assert grade.label == "BEAT"
    assert grade.beat_close is True
    assert grade.clv_pct is not None and grade.clv_pct > 0
    assert grade.clv_prob_pts is not None and grade.clv_prob_pts > 0


def test_clv_missing_close_unavailable():
    grade = clv_grade(-110, None)
    assert grade.label == "UNAVAILABLE"
    assert grade.clv_pct is None
    assert grade.beat_close is None


def test_decimal_roundtrip_plus_money():
    dec = american_to_decimal(150)
    assert dec == pytest.approx(2.5)
