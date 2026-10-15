"""CFB spread model: rating-differential ensemble stub + Gaussian cover.

Dogs-first is a **gate** (see gates.py), not a model tweak.

Ensemble members (caller-supplied margin projections, already in points):
SP+, FEI, Massey, Elo. Equal weight among whoever is present. Live SP+/FEI
imports are not wired — pass the published number or hold.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping

from prediction_core.models.nfl import gaussian_cover_prob

ENSEMBLE_KEYS = ("sp_plus", "fei", "massey", "elo")


@dataclass(frozen=True)
class CFBPrediction:
    ok: bool
    mu: float | None
    sigma: float | None
    home_spread: float | None
    p_home_cover: float | None
    p_away_cover: float | None
    components_used: tuple[str, ...]
    model_version: str
    notes: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def ensemble_margin(
    components: Mapping[str, float | None],
    weights: Mapping[str, float] | None = None,
) -> tuple[float, tuple[str, ...]]:
    """Weighted average of provided margin projections (home perspective).

    Keys not in the map or valued ``None`` are skipped. Will not invent a
    missing SP+/FEI/Massey/Elo number. If ``weights`` is omitted, equal
    weight among present members.
    """
    present: list[tuple[str, float]] = []
    for key, raw in components.items():
        if raw is None:
            continue
        present.append((str(key), float(raw)))
    if not present:
        raise ValueError(
            "no CFB ensemble members provided (need at least one of "
            f"{', '.join(ENSEMBLE_KEYS)} as a point margin). LIVE: SP+/FEI/Massey/Elo plug in here"
        )
    if weights:
        used: list[tuple[str, float, float]] = []
        for key, value in present:
            w = weights.get(key)
            if w is None:
                continue
            if w < 0:
                raise ValueError("ensemble weights must be >= 0")
            used.append((key, value, float(w)))
        if not used:
            raise ValueError("weights supplied but none match provided components")
        total_w = sum(item[2] for item in used)
        if total_w <= 0:
            raise ValueError("ensemble weights must sum to > 0")
        mu = sum(value * w for _, value, w in used) / total_w
        return mu, tuple(item[0] for item in used)
    mu = sum(value for _, value in present) / len(present)
    return mu, tuple(key for key, _ in present)


def apply_week1_shrink(mu: float, *, week: int | None, shrink: float | None) -> float:
    """Optional early-season shrink toward 0. Both week and shrink must be supplied."""
    if week is None and shrink is None:
        return mu
    if week is None or shrink is None:
        raise ValueError("week-1 shrink needs both week and shrink; will not invent a factor")
    if not 0.0 <= shrink <= 1.0:
        raise ValueError("shrink must be in [0, 1]")
    if week <= 1:
        return float(mu) * float(shrink)
    return float(mu)


def predict_cfb(
    *,
    mu: float | None = None,
    sigma: float | None = None,
    home_spread: float | None = None,
    components: Mapping[str, float | None] | None = None,
    weights: Mapping[str, float] | None = None,
    week: int | None = None,
    shrink: float | None = None,
    model_version: str = "prediction_core.cfb.v1",
) -> CFBPrediction:
    notes: list[str] = [
        "ensemble stub: averages provided margins only; live SP+/FEI feeds not imported",
        "dogs-first is enforced in gates, not here",
    ]
    used: tuple[str, ...] = ()
    try:
        if mu is None:
            if not components:
                return _fail(
                    "missing μ (need mu or ensemble components). LIVE: SP+/FEI/Massey/Elo plug in here",
                    model_version,
                )
            mu, used = ensemble_margin(components, weights)
            notes.append(f"μ from ensemble {used}")
        else:
            mu = float(mu)
            notes.append("μ provided directly")

        mu = apply_week1_shrink(mu, week=week, shrink=shrink)
        if week is not None and week <= 1 and shrink is not None:
            notes.append(f"week {week} shrink {shrink} applied")

        if sigma is None:
            return _fail("missing sigma (estimate from data; will not invent CFB σ)", model_version)
        if home_spread is None:
            return _fail("missing home_spread (need a stamped market line)", model_version)

        p_home = gaussian_cover_prob(mu, float(home_spread), float(sigma), side="home")
        return CFBPrediction(
            ok=True,
            mu=mu,
            sigma=float(sigma),
            home_spread=float(home_spread),
            p_home_cover=p_home,
            p_away_cover=1.0 - p_home,
            components_used=used,
            model_version=model_version,
            notes=tuple(notes),
        )
    except ValueError as exc:
        return _fail(str(exc), model_version)


def _fail(reason: str, model_version: str) -> CFBPrediction:
    return CFBPrediction(
        ok=False,
        mu=None,
        sigma=None,
        home_spread=None,
        p_home_cover=None,
        p_away_cover=None,
        components_used=(),
        model_version=model_version,
        notes=(reason,),
    )
