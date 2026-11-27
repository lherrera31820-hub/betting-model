"""Market plumbing: American odds, de-vig, edge vs model, price join, CLV.

All helpers operate on caller-supplied prices. They never fetch books or
invent lines. CLV is an internal promote/kill metric — do not lead Luis
with it (side pass/fail is the scoreboard).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Iterable, Literal, Mapping, Sequence

CLVLabel = Literal["BEAT", "PUSH", "LOSE", "UNAVAILABLE"]


@dataclass(frozen=True)
class PriceStamp:
    """One timestamped posted number. Missing fields stay None — never filled."""

    american: int | float | None = None
    line: float | None = None
    book: str | None = None
    ts: str | None = None
    market: str | None = None

    def has_american(self) -> bool:
        return self.american is not None


@dataclass(frozen=True)
class PriceJoin:
    """Open / best / close stamps plus derived shop pointers."""

    open: PriceStamp | None = None
    best: PriceStamp | None = None
    close: PriceStamp | None = None
    intended_close_book: str | None = None
    shop_price: int | float | None = None
    shop_line: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "open": _stamp_dict(self.open),
            "best": _stamp_dict(self.best),
            "close": _stamp_dict(self.close),
            "intended_close_book": self.intended_close_book,
            "shop_price": self.shop_price,
            "shop_line": self.shop_line,
        }


@dataclass(frozen=True)
class CLVGrade:
    """Internal-only close-line grade. Not a Luis headline metric."""

    clv_pct: float | None
    clv_prob_pts: float | None
    beat_close: bool | None
    label: CLVLabel
    method: str = "decimal_ratio"
    note: str = "internal promote/kill — Luis scoreboard is side pass/fail"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class TwoWayFair:
    p_a: float
    p_b: float
    overround: float
    method: str = "multiplicative"


def american_to_implied(american: int | float) -> float:
    """Raw book implied probability (includes vig)."""
    odds = float(american)
    if odds == 0:
        raise ValueError("American odds cannot be 0")
    if odds < 0:
        return (-odds) / ((-odds) + 100.0)
    return 100.0 / (odds + 100.0)


def american_to_decimal(american: int | float) -> float:
    odds = float(american)
    if odds == 0:
        raise ValueError("American odds cannot be 0")
    if odds < 0:
        return 1.0 + (100.0 / (-odds))
    return 1.0 + (odds / 100.0)


def decimal_to_american(decimal_odds: float) -> float:
    if decimal_odds <= 1.0:
        raise ValueError("Decimal odds must be > 1")
    if decimal_odds >= 2.0:
        return (decimal_odds - 1.0) * 100.0
    return -100.0 / (decimal_odds - 1.0)


def implied_to_american(prob: float) -> float:
    if not 0.0 < prob < 1.0:
        raise ValueError("Probability must be in (0, 1)")
    if prob == 0.5:
        return 100.0
    if prob < 0.5:
        return (100.0 * (1.0 - prob)) / prob
    return -(100.0 * prob) / (1.0 - prob)


def multiplicative_devig(implied: Sequence[float]) -> list[float]:
    """Normalize raw implied probs so they sum to 1 (multiplicative / proportional)."""
    if not implied:
        raise ValueError("Need at least one implied probability")
    if any(p <= 0 for p in implied):
        raise ValueError("Implied probabilities must be > 0")
    total = float(sum(implied))
    if total <= 0:
        raise ValueError("Implied probabilities must sum to > 0")
    return [float(p) / total for p in implied]


def two_way_devig(
    american_a: int | float,
    american_b: int | float,
    method: str = "multiplicative",
) -> TwoWayFair:
    """De-vig a two-way market. Only multiplicative is implemented in v1."""
    if method != "multiplicative":
        raise ValueError(f"Unsupported de-vig method {method!r}; v1 is multiplicative only")
    raw_a = american_to_implied(american_a)
    raw_b = american_to_implied(american_b)
    overround = raw_a + raw_b
    fair_a, fair_b = multiplicative_devig([raw_a, raw_b])
    return TwoWayFair(p_a=fair_a, p_b=fair_b, overround=overround, method=method)


def probability_edge(
    model_p: float,
    market_p: float,
    *,
    as_points: bool = True,
) -> float:
    """``model_p - market_p``. Percentage points when ``as_points`` (desk +ML bar uses this)."""
    if not 0.0 <= model_p <= 1.0:
        raise ValueError("model_p must be a probability in [0, 1]")
    if not 0.0 <= market_p <= 1.0:
        raise ValueError("market_p must be a probability in [0, 1]")
    diff = model_p - market_p
    return diff * 100.0 if as_points else diff


def spread_point_edge(model_mu: float, market_home_spread: float) -> float:
    """Home-margin edge in points: model μ minus market-implied margin (−spread)."""
    market_mu = -float(market_home_spread)
    return float(model_mu) - market_mu


def best_american(quotes: Iterable[int | float]) -> float:
    """Best price for the bettor is the maximum American number."""
    values = list(quotes)
    if not values:
        raise ValueError("Need at least one American quote")
    return max(float(q) for q in values)


def parse_price_stamp(raw: Mapping[str, Any] | PriceStamp | None) -> PriceStamp | None:
    if raw is None:
        return None
    if isinstance(raw, PriceStamp):
        return raw
    if not isinstance(raw, Mapping):
        raise TypeError("Price stamp must be a mapping or PriceStamp")
    american = raw.get("american", raw.get("odds"))
    line = raw.get("line", raw.get("spread"))
    return PriceStamp(
        american=_maybe_float(american),
        line=_maybe_float(line),
        book=_maybe_str(raw.get("book") or raw.get("bookmaker")),
        ts=_maybe_str(raw.get("ts") or raw.get("timestamp") or raw.get("as_of")),
        market=_maybe_str(raw.get("market")),
    )


def join_open_best_close(
    open_px: Mapping[str, Any] | PriceStamp | None = None,
    best_px: Mapping[str, Any] | PriceStamp | None = None,
    close_px: Mapping[str, Any] | PriceStamp | None = None,
) -> PriceJoin:
    """Structure caller-supplied open / best / close stamps. Does not invent a close."""
    open_s = parse_price_stamp(open_px)
    best_s = parse_price_stamp(best_px)
    close_s = parse_price_stamp(close_px)
    shop = best_s or open_s
    return PriceJoin(
        open=open_s,
        best=best_s,
        close=close_s,
        intended_close_book=close_s.book if close_s else None,
        shop_price=shop.american if shop else None,
        shop_line=shop.line if shop else None,
    )


def clv_grade(
    bet_american: int | float | None,
    close_american: int | float | None,
    *,
    push_tol_pct: float = 0.05,
) -> CLVGrade:
    """Grade a ticket vs a stamped close.

    Practitioner sign (internal promote/kill): positive means you beat the close.

    * ``clv_pct`` = ``(bet_decimal / close_decimal - 1) * 100``
      (longer bet price than close → positive).
    * ``clv_prob_pts`` = ``(implied_close − implied_bet) * 100``
      (close shorter / more confident in your side → positive).

    Returns UNAVAILABLE when either price is missing — never invents a close.
    """
    if bet_american is None or close_american is None:
        return CLVGrade(
            clv_pct=None,
            clv_prob_pts=None,
            beat_close=None,
            label="UNAVAILABLE",
        )
    bet_dec = american_to_decimal(bet_american)
    close_dec = american_to_decimal(close_american)
    clv_pct = (bet_dec / close_dec - 1.0) * 100.0
    clv_prob_pts = (american_to_implied(close_american) - american_to_implied(bet_american)) * 100.0
    if abs(clv_pct) <= push_tol_pct:
        label: CLVLabel = "PUSH"
        beat: bool | None = None
    elif clv_pct > 0:
        label = "BEAT"
        beat = True
    else:
        label = "LOSE"
        beat = False
    return CLVGrade(
        clv_pct=round(clv_pct, 4),
        clv_prob_pts=round(clv_prob_pts, 4),
        beat_close=beat,
        label=label,
    )


def _stamp_dict(stamp: PriceStamp | None) -> dict[str, Any] | None:
    if stamp is None:
        return None
    return asdict(stamp)


def _maybe_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    return float(value)


def _maybe_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
