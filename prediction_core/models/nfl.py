"""NFL Gaussian spread-cover model.

Point differential ~ N(μ, σ²). Cover probability = Φ((μ + home_spread) / σ)
for the home side (continuous; P(push) = 0).

``σ`` and home-field must be supplied from an empirical / rating fit.
This module will not invent a league-average sigma.
Desk SOP: blend model μ toward the market at w ≤ 0.22, then require
edge ≥ 2.0 pts after that blend before CLEAR (enforced in gates.py).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from math import erf, sqrt
from typing import Any

NFL_BLEND_W_MAX = 0.22
NFL_MIN_EDGE_PTS = 2.0


@dataclass(frozen=True)
class NFLBlend:
    mu_model: float
    mu_market: float
    mu_blend: float
    w: float
    edge_pts: float  # blend μ − market μ (home perspective)


@dataclass(frozen=True)
class NFLPrediction:
    ok: bool
    mu: float | None
    sigma: float | None
    home_spread: float | None
    p_home_cover: float | None
    p_away_cover: float | None
    blend: NFLBlend | None
    model_version: str
    notes: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        return payload


def norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + erf(x / sqrt(2.0)))


def gaussian_cover_prob(mu: float, home_spread: float, sigma: float, *, side: str = "home") -> float:
    """P(side covers) under a continuous Gaussian margin.

    Home covers when ``home_margin + home_spread > 0``, i.e.
    ``Φ((μ + home_spread) / σ)``.
    """
    if sigma <= 0:
        raise ValueError("sigma must be > 0 (estimate from data; will not invent NFL σ)")
    z = (float(mu) + float(home_spread)) / float(sigma)
    p_home = norm_cdf(z)
    side_l = side.lower()
    if side_l == "home":
        return p_home
    if side_l == "away":
        return 1.0 - p_home
    raise ValueError("side must be 'home' or 'away'")


def blend_margin(
    model_mu: float,
    market_home_spread: float,
    *,
    w: float = NFL_BLEND_W_MAX,
) -> NFLBlend:
    """Shrink model μ toward the market line. Default w is the locked desk max 0.22."""
    if not 0.0 <= w <= 1.0:
        raise ValueError("blend weight w must be in [0, 1]")
    if w > NFL_BLEND_W_MAX + 1e-12:
        raise ValueError(f"NFL blend w={w} exceeds locked max {NFL_BLEND_W_MAX}")
    mu_market = -float(market_home_spread)
    mu_blend = w * float(model_mu) + (1.0 - w) * mu_market
    edge_pts = mu_blend - mu_market  # == w * (model_mu - mu_market)
    return NFLBlend(
        mu_model=float(model_mu),
        mu_market=mu_market,
        mu_blend=mu_blend,
        w=w,
        edge_pts=edge_pts,
    )


def margin_from_ratings(
    rating_home: float,
    rating_away: float,
    *,
    hfa: float,
    points_per_rating: float,
) -> float:
    """μ = (R_home − R_away) * points_per_rating + hfa.

    ``hfa`` and ``points_per_rating`` are required. No invented scale.
    """
    return (float(rating_home) - float(rating_away)) * float(points_per_rating) + float(hfa)


def predict_nfl(
    *,
    mu: float | None = None,
    sigma: float | None = None,
    home_spread: float | None = None,
    rating_home: float | None = None,
    rating_away: float | None = None,
    hfa: float | None = None,
    points_per_rating: float | None = None,
    blend_w: float = NFL_BLEND_W_MAX,
    side: str = "home",
    model_version: str = "prediction_core.nfl.v1",
) -> NFLPrediction:
    notes: list[str] = []
    try:
        if mu is None:
            if None in (rating_home, rating_away, hfa, points_per_rating):
                return _fail(
                    "missing μ (need mu or rating_home/away + hfa + points_per_rating). "
                    "LIVE: nflfastR EPA / Massey / Elo plug in here",
                    model_version,
                )
            mu = margin_from_ratings(
                float(rating_home),  # type: ignore[arg-type]
                float(rating_away),  # type: ignore[arg-type]
                hfa=float(hfa),  # type: ignore[arg-type]
                points_per_rating=float(points_per_rating),  # type: ignore[arg-type]
            )
            notes.append("μ from rating differential")
        else:
            mu = float(mu)
            notes.append("μ provided directly")

        if sigma is None:
            return _fail("missing sigma (estimate from data; will not invent NFL σ)", model_version)
        if home_spread is None:
            return _fail("missing home_spread (need a stamped market line)", model_version)

        p_home = gaussian_cover_prob(mu, home_spread, float(sigma), side="home")
        p_away = 1.0 - p_home
        blend = blend_margin(mu, float(home_spread), w=blend_w)
        notes.append(f"Gaussian cover; blend w={blend.w}")
        if side.lower() not in ("home", "away"):
            return _fail("side must be home or away", model_version)
        return NFLPrediction(
            ok=True,
            mu=mu,
            sigma=float(sigma),
            home_spread=float(home_spread),
            p_home_cover=p_home,
            p_away_cover=p_away,
            blend=blend,
            model_version=model_version,
            notes=tuple(notes),
        )
    except ValueError as exc:
        return _fail(str(exc), model_version)


def _fail(reason: str, model_version: str) -> NFLPrediction:
    return NFLPrediction(
        ok=False,
        mu=None,
        sigma=None,
        home_spread=None,
        p_home_cover=None,
        p_away_cover=None,
        blend=None,
        model_version=model_version,
        notes=(reason,),
    )
