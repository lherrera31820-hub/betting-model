"""MLB run model: Poisson / NegBin score matrix + Elo prior hook.

Runnable math only. Live Baseball Savant / Statcast / probable-pitcher
feeds are **not** fetched here — the caller must pass λ (or component
adjustments). If those inputs are missing, return ``ok=False`` rather
than inventing MODEL%.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from math import exp, lgamma, log
from typing import Any, Literal

Family = Literal["poisson", "negbin"]

# LIVE_SAVANT_HOOKS — plug these in at the data layer, then pass numbers here.
# Do not scrape or guess them inside this module.
LIVE_SAVANT_INPUTS = (
    "starter xwOBA / FIP vs handedness (Savant)",
    "lineup xwOBA with PA≥150 hard stop (Savant + gate)",
    "park factor (Savant / Statcast)",
    "probable pitcher confirmation (MLB Stats API)",
    "bullpen residual (optional)",
    "weather run-env (Open-Meteo / NOAA) for totals and F5",
)


@dataclass(frozen=True)
class MLBPrediction:
    ok: bool
    p_home: float | None
    p_away: float | None
    p_tie: float | None
    p_home_ml: float | None  # tie split 50/50 for full-game ML
    p_away_ml: float | None
    lam_home: float | None
    lam_away: float | None
    family: str | None
    model_version: str
    notes: tuple[str, ...]
    live_hooks: tuple[str, ...] = LIVE_SAVANT_INPUTS

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def poisson_pmf(k: int, lam: float) -> float:
    if k < 0:
        raise ValueError("k must be >= 0")
    if lam < 0:
        raise ValueError("lambda must be >= 0")
    if lam == 0:
        return 1.0 if k == 0 else 0.0
    # exp(k log λ − λ − log k!)
    return exp(k * log(lam) - lam - lgamma(k + 1))


def negbin_pmf(k: int, mu: float, alpha: float) -> float:
    """NB2 parameterization: Var = μ + α μ², r = 1/α.

    ``alpha`` must come from a calibration / residual fit. This function
    will not pick a default overdispersion.
    """
    if k < 0:
        raise ValueError("k must be >= 0")
    if mu < 0:
        raise ValueError("mu must be >= 0")
    if alpha <= 0:
        raise ValueError("NegBin alpha must be > 0 (estimate from data; use Poisson if unknown)")
    if mu == 0:
        return 1.0 if k == 0 else 0.0
    r = 1.0 / alpha
    # C(k+r-1, k) * (r/(r+μ))^r * (μ/(r+μ))^k
    log_c = lgamma(k + r) - lgamma(r) - lgamma(k + 1)
    return exp(log_c + r * log(r / (r + mu)) + k * log(mu / (r + mu)))


def elo_win_prob(elo_home: float, elo_away: float, hfa: float) -> float:
    """Logistic Elo P(home win). ``hfa`` is required — no invented home-field."""
    return 1.0 / (1.0 + 10.0 ** ((elo_away - (elo_home + hfa)) / 400.0))


def expected_runs_from_components(
    base_home: float,
    base_away: float,
    *,
    park_factor: float | None = None,
    pitcher_adj_home: float | None = None,
    pitcher_adj_away: float | None = None,
    lineup_adj_home: float | None = None,
    lineup_adj_away: float | None = None,
    weather_adj: float | None = None,
) -> tuple[float, float]:
    """Compose λ from caller-supplied pieces. Missing adjustments are skipped.

    Sign convention: pitcher_adj is added to the *opponent* λ (positive =
    worse pitcher / more runs allowed). lineup_adj is added to that team's λ.
    park_factor and weather_adj multiply both sides when provided (1.0 = neutral).
    """
    if base_home < 0 or base_away < 0:
        raise ValueError("base expected runs must be >= 0")
    lam_h = float(base_home)
    lam_a = float(base_away)
    if pitcher_adj_away is not None:
        lam_h += float(pitcher_adj_away)
    if pitcher_adj_home is not None:
        lam_a += float(pitcher_adj_home)
    if lineup_adj_home is not None:
        lam_h += float(lineup_adj_home)
    if lineup_adj_away is not None:
        lam_a += float(lineup_adj_away)
    scale = 1.0
    if park_factor is not None:
        scale *= float(park_factor)
    if weather_adj is not None:
        scale *= float(weather_adj)
    lam_h *= scale
    lam_a *= scale
    if lam_h < 0 or lam_a < 0:
        raise ValueError("composed expected runs went negative — check adjustments")
    return lam_h, lam_a


def scale_runs_for_f5(lam: float, *, innings: float = 5.0, full: float = 9.0) -> float:
    """Stub inning scale. Live F5 should use starter IP / inning-split Savant, not 5/9."""
    if full <= 0:
        raise ValueError("full innings must be > 0")
    if lam < 0:
        raise ValueError("lambda must be >= 0")
    return float(lam) * (float(innings) / float(full))


def win_probs_from_lambdas(
    lam_home: float,
    lam_away: float,
    *,
    family: Family = "poisson",
    alpha: float | None = None,
    k_max: int | None = None,
) -> tuple[float, float, float]:
    """Return (P(home score > away), P(away > home), P(tie))."""
    if family not in ("poisson", "negbin"):
        raise ValueError("family must be 'poisson' or 'negbin'")
    if family == "negbin" and alpha is None:
        raise ValueError("NegBin requires a fitted alpha; will not invent overdispersion")
    pmf_h = _pmf_vector(lam_home, family, alpha, k_max)
    pmf_a = _pmf_vector(lam_away, family, alpha, k_max)
    p_home = p_away = p_tie = 0.0
    for i, ph in enumerate(pmf_h):
        for j, pa in enumerate(pmf_a):
            mass = ph * pa
            if i > j:
                p_home += mass
            elif i < j:
                p_away += mass
            else:
                p_tie += mass
    return p_home, p_away, p_tie


def predict_mlb(
    *,
    lam_home: float | None = None,
    lam_away: float | None = None,
    base_home: float | None = None,
    base_away: float | None = None,
    park_factor: float | None = None,
    pitcher_adj_home: float | None = None,
    pitcher_adj_away: float | None = None,
    lineup_adj_home: float | None = None,
    lineup_adj_away: float | None = None,
    weather_adj: float | None = None,
    family: Family = "poisson",
    alpha: float | None = None,
    elo_home: float | None = None,
    elo_away: float | None = None,
    elo_hfa: float | None = None,
    elo_weight: float | None = None,
    f5: bool = False,
    model_version: str = "prediction_core.mlb.v1",
) -> MLBPrediction:
    """Calibrated-enough run model from provided λ / components + optional Elo blend."""
    notes: list[str] = []
    try:
        if lam_home is None or lam_away is None:
            if base_home is None or base_away is None:
                return _fail(
                    "missing expected runs (need lam_* or base_*). LIVE: Savant/SP inputs plug in here",
                    model_version,
                )
            lam_home, lam_away = expected_runs_from_components(
                base_home,
                base_away,
                park_factor=park_factor,
                pitcher_adj_home=pitcher_adj_home,
                pitcher_adj_away=pitcher_adj_away,
                lineup_adj_home=lineup_adj_home,
                lineup_adj_away=lineup_adj_away,
                weather_adj=weather_adj,
            )
            notes.append("lambda composed from caller components")
        else:
            lam_home = float(lam_home)
            lam_away = float(lam_away)
            notes.append("lambda provided directly")

        if f5:
            lam_home = scale_runs_for_f5(lam_home)
            lam_away = scale_runs_for_f5(lam_away)
            notes.append("F5 used 5/9 scale stub — replace with live inning-split λ")

        p_home, p_away, p_tie = win_probs_from_lambdas(
            lam_home, lam_away, family=family, alpha=alpha
        )
        notes.append(f"family={family}")

        if _all_present(elo_home, elo_away, elo_hfa, elo_weight):
            w = float(elo_weight)  # type: ignore[arg-type]
            if not 0.0 <= w <= 1.0:
                return _fail("elo_weight must be in [0, 1]", model_version)
            p_elo = elo_win_prob(float(elo_home), float(elo_away), float(elo_hfa))  # type: ignore[arg-type]
            p_home_reg = p_home + 0.5 * p_tie
            p_blend = w * p_elo + (1.0 - w) * p_home_reg
            # Preserve tie mass; re-allocate remaining to home/away.
            remain = 1.0 - p_tie
            if remain <= 0:
                return _fail("degenerate score matrix (tie mass 1)", model_version)
            p_home = p_blend * remain
            p_away = (1.0 - p_blend) * remain
            notes.append(f"Elo prior blended at w={w}")
        elif any(v is not None for v in (elo_home, elo_away, elo_hfa, elo_weight)):
            return _fail(
                "partial Elo hook (need elo_home, elo_away, elo_hfa, elo_weight). Will not invent HFA/K",
                model_version,
            )
        else:
            notes.append("no Elo prior (hook idle)")

        p_home_ml = p_home + 0.5 * p_tie
        p_away_ml = p_away + 0.5 * p_tie
        return MLBPrediction(
            ok=True,
            p_home=p_home,
            p_away=p_away,
            p_tie=p_tie,
            p_home_ml=p_home_ml,
            p_away_ml=p_away_ml,
            lam_home=lam_home,
            lam_away=lam_away,
            family=family,
            model_version=model_version,
            notes=tuple(notes),
        )
    except ValueError as exc:
        return _fail(str(exc), model_version)


def _pmf_vector(
    lam: float,
    family: Family,
    alpha: float | None,
    k_max: int | None,
) -> list[float]:
    cap = k_max if k_max is not None else _auto_k_max(lam)
    out: list[float] = []
    for k in range(cap + 1):
        if family == "poisson":
            out.append(poisson_pmf(k, lam))
        else:
            out.append(negbin_pmf(k, lam, float(alpha)))
    tail = 1.0 - sum(out)
    if tail > 0:
        out[-1] += tail
    return out


def _auto_k_max(lam: float) -> int:
    # Cover the body + a wide right tail; remaining mass is folded into the last bin.
    return max(20, int(lam + 12.0 * (lam ** 0.5) + 8.0))


def _all_present(*values: Any) -> bool:
    return all(v is not None for v in values)


def _fail(reason: str, model_version: str) -> MLBPrediction:
    return MLBPrediction(
        ok=False,
        p_home=None,
        p_away=None,
        p_tie=None,
        p_home_ml=None,
        p_away_ml=None,
        lam_home=None,
        lam_away=None,
        family=None,
        model_version=model_version,
        notes=(reason,),
    )
