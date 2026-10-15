"""Runnable model math from supplied inputs. No live Savant/odds fetches."""

from __future__ import annotations

import pytest

from prediction_core.models.cfb import ensemble_margin, predict_cfb
from prediction_core.models.mlb import elo_win_prob, predict_mlb, win_probs_from_lambdas
from prediction_core.models.nfl import blend_margin, gaussian_cover_prob, predict_nfl


def test_mlb_poisson_equal_lambdas_are_symmetric():
    p_h, p_a, p_t = win_probs_from_lambdas(4.0, 4.0, family="poisson")
    assert p_h == pytest.approx(p_a, abs=1e-9)
    assert p_t > 0
    assert p_h + p_a + p_t == pytest.approx(1.0, abs=1e-9)


def test_mlb_higher_lambda_favors_home():
    pred = predict_mlb(lam_home=5.0, lam_away=3.0, family="poisson")
    assert pred.ok
    assert pred.p_home_ml is not None and pred.p_away_ml is not None
    assert pred.p_home_ml > pred.p_away_ml
    assert pred.p_home_ml + pred.p_away_ml == pytest.approx(1.0, abs=1e-9)


def test_mlb_negbin_requires_alpha():
    pred = predict_mlb(lam_home=4.5, lam_away=4.0, family="negbin")
    assert pred.ok is False
    assert any("alpha" in n.lower() for n in pred.notes)


def test_mlb_negbin_with_alpha_runs():
    pred = predict_mlb(lam_home=4.5, lam_away=4.0, family="negbin", alpha=0.15)
    assert pred.ok
    assert pred.family == "negbin"
    assert pred.p_home_ml is not None


def test_mlb_elo_prior_hook():
    assert elo_win_prob(1500, 1500, hfa=0) == pytest.approx(0.5)
    pred = predict_mlb(
        lam_home=4.0,
        lam_away=4.0,
        elo_home=1600,
        elo_away=1500,
        elo_hfa=20,
        elo_weight=0.3,
    )
    assert pred.ok
    assert pred.p_home_ml is not None
    assert pred.p_home_ml > 0.5
    assert any("Elo" in n for n in pred.notes)


def test_mlb_refuses_to_invent_lambda():
    pred = predict_mlb()
    assert pred.ok is False
    assert pred.p_home_ml is None


def test_nfl_gaussian_at_the_number_is_half():
    assert gaussian_cover_prob(mu=0.0, home_spread=0.0, sigma=14.0) == pytest.approx(0.5)


def test_nfl_blend_uses_locked_w():
    blend = blend_margin(7.0, -3.0, w=0.22)
    # market μ = 3; edge = 0.22 * (7 - 3) = 0.88
    assert blend.edge_pts == pytest.approx(0.88)
    assert blend.mu_blend == pytest.approx(3.88)


def test_nfl_refuses_invented_sigma():
    pred = predict_nfl(mu=4.0, home_spread=-3.0)
    assert pred.ok is False
    assert pred.p_home_cover is None


def test_nfl_predict_with_supplied_sigma():
    pred = predict_nfl(mu=6.0, sigma=13.8, home_spread=-3.0)
    assert pred.ok
    assert pred.p_home_cover is not None
    assert pred.p_home_cover > 0.5
    assert pred.blend is not None
    assert pred.blend.w == 0.22


def test_cfb_ensemble_equal_weight():
    mu, used = ensemble_margin({"sp_plus": 4.0, "fei": 6.0, "massey": None})
    assert mu == pytest.approx(5.0)
    assert used == ("sp_plus", "fei")


def test_cfb_predict_from_components():
    pred = predict_cfb(
        components={"sp_plus": 3.0, "elo": 5.0},
        sigma=16.0,
        home_spread=-3.0,
    )
    assert pred.ok
    assert pred.mu == pytest.approx(4.0)
    assert pred.p_home_cover is not None
    assert "sp_plus" in pred.components_used


def test_cfb_refuses_empty_ensemble():
    pred = predict_cfb(components={"sp_plus": None, "fei": None}, sigma=16.0, home_spread=7.0)
    assert pred.ok is False
