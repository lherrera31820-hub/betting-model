"""Deterministic CLEAR / FILL / HOLD gates from locked CLEAR_RULES_SEP15.

Main is the sole CLEAR writer. This module only emits a recommendation
and reject codes. It never loosens a bar to chase activity and never
invents MODEL%.

Locked rules (Sep 15 2026)
--------------------------
1. CFB — prefer dogs. Home/G6 (and, conservatively, all) favorites need
   edge ≥ ~5 pts AND model ≥ 70 or stay FILL/HOLD (especially −3/−7/−10).
2. Daily volume — soft CLEAR cap ~3–5u. Enforced in ``apply_daily_cap``.
3. MLB plus-money ML — only if model ≥ 65 AND edge ≥ 8. Opener/PRIM → reject.
4. F5 — skip American ≤ −200 unless model ≥ 75 and a real F5 market is stamped.
5. Never invent MODEL%. Missing model → HOLD.

Desk SOP bars used only where Sep 15 is silent (documented on the ticket):
- CFB dogs: edge ≥ 2.75 pts (NCAAF after W1 shrink).
- NFL: edge ≥ 2.0 pts after w=0.22 blend (blend is the caller's job to stamp).
- MLB favorite ML: edge ≥ 2.5 prob pts.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal, Sequence

Status = Literal["CLEAR", "FILL", "HOLD"]

# --- Locked CLEAR_RULES_SEP15 ---
MLB_PLUS_ML_MIN_MODEL = 65.0
MLB_PLUS_ML_MIN_EDGE = 8.0
F5_JUICE_AMERICAN = -200
F5_JUICE_MIN_MODEL = 75.0
CFB_FAV_MIN_EDGE_PTS = 5.0
CFB_FAV_MIN_MODEL = 70.0
DAILY_CAP_SOFT_U = 5.0
HIGH_MODEL_PCT = 70.0

# --- Desk SOP (not Sep 15 lock; used so dogs/NFL have a numeric bar) ---
CFB_DOG_MIN_EDGE_PTS = 2.75
NFL_MIN_EDGE_PTS = 2.0
MLB_FAV_ML_MIN_EDGE = 2.5
MLB_TOTAL_MIN_EDGE_RUNS = 0.4
DEFAULT_CLEAR_UNITS = 1.0

ALLOWED_SPORTS = {"MLB", "NFL", "CFB"}
SOCCER_SPORTS = {"SOCCER", "EPL", "MLS", "UCL", "UEFA", "LIGA", "SERIEA", "BUNDESLIGA"}
OPENER_PRIM_TAGS = {"opener", "prim", "opening", "opener/prim", "prim/opener"}
KEY_NUMBERS_CFB = {-3.0, -7.0, -10.0, 3.0, 7.0, 10.0}


class RejectCode:
    SOCCER_OFF = "REJECT_SOCCER_OFF"
    SPORT_OFF = "REJECT_SPORT_OFF"
    NO_MODEL = "REJECT_NO_MODEL"
    NO_LINES = "REJECT_NO_LINES"
    NO_SIDE = "REJECT_NO_SIDE"
    OPENER_PRIM = "REJECT_OPENER_PRIM"
    MLB_PLUS_ML_SOFT = "REJECT_MLB_PLUS_ML_SOFT"
    MLB_FAV_ML_EDGE = "REJECT_MLB_FAV_ML_EDGE"
    F5_JUICE = "REJECT_F5_JUICE"
    F5_NO_MARKET = "REJECT_F5_NO_MARKET"
    CFB_FAV_BAR = "REJECT_CFB_FAV_BAR"
    CFB_DOG_EDGE = "REJECT_CFB_DOG_EDGE"
    NFL_EDGE = "REJECT_NFL_EDGE"
    DAILY_CAP = "REJECT_DAILY_CAP"
    MISSING_PACKET = "REJECT_MISSING_PACKET"
    HIGH_MODEL_NOT_CLEAR = "REJECT_HIGH_MODEL_NOT_CLEAR"
    THIN_PA = "REJECT_THIN_PA"
    NO_LOCKED_BAR = "REJECT_NO_LOCKED_BAR"


@dataclass
class GateTicket:
    sport: str
    market: str
    side: str | None = None
    american: int | float | None = None
    line: float | None = None
    model_pct: float | None = None
    edge: float | None = None
    price_source: str | None = None
    f5_market_stamped: bool = False
    is_home: bool = False
    is_g6: bool = False
    packet_sp: bool = False
    packet_sit: bool = False
    packet_wx: bool = False
    pa_ok: bool | None = None
    game_day: bool = False
    proposed_units: float = DEFAULT_CLEAR_UNITS
    game_id: str | None = None

    @property
    def sport_norm(self) -> str:
        raw = (self.sport or "").strip().upper()
        if raw in {"NCAAF", "NCAA-F", "NCAA_FB"}:
            return "CFB"
        return raw

    @property
    def market_norm(self) -> str:
        raw = (self.market or "").strip().upper()
        aliases = {
            "H2H": "ML",
            "MONEYLINE": "ML",
            "MONEY_LINE": "ML",
            "ATS": "SPREAD",
            "SIDE": "SPREAD",
            "OU": "TOTAL",
            "OVER_UNDER": "TOTAL",
            "FIRST_5": "F5",
            "F5_ML": "F5",
        }
        return aliases.get(raw, raw)

    @property
    def is_plus_money(self) -> bool:
        return self.american is not None and float(self.american) > 0

    @property
    def is_favorite(self) -> bool:
        if self.market_norm in {"SPREAD", "ATS"} and self.line is not None:
            return float(self.line) < 0
        if self.american is not None:
            return float(self.american) < 0
        return False

    @property
    def is_dog(self) -> bool:
        if self.market_norm in {"SPREAD", "ATS"} and self.line is not None:
            return float(self.line) > 0
        if self.american is not None:
            return float(self.american) > 0
        return False


@dataclass
class GateDecision:
    status: Status
    reject_codes: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    units: float = 0.0
    sport: str = ""
    market: str = ""
    side: str | None = None
    model_pct: float | None = None
    edge: float | None = None
    proposed_units: float = DEFAULT_CLEAR_UNITS
    is_dog: bool = False
    game_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def evaluate_ticket(ticket: GateTicket) -> GateDecision:
    """Pure gate. Daily cap is applied later across the slate."""
    codes: list[str] = []
    reasons: list[str] = []
    sport = ticket.sport_norm
    market = ticket.market_norm

    decision = GateDecision(
        status="HOLD",
        sport=sport,
        market=market,
        side=ticket.side,
        model_pct=ticket.model_pct,
        edge=ticket.edge,
        proposed_units=ticket.proposed_units,
        is_dog=ticket.is_dog,
        game_id=ticket.game_id,
    )

    if sport in SOCCER_SPORTS or sport.startswith("SOCCER"):
        return _finish(decision, "HOLD", [RejectCode.SOCCER_OFF], ["soccer OFF for this rebuild"])
    if sport not in ALLOWED_SPORTS:
        return _finish(decision, "HOLD", [RejectCode.SPORT_OFF], [f"sport {sport or '?'} not in MLB/NFL/CFB"])

    if ticket.model_pct is None:
        return _finish(
            decision,
            "HOLD",
            [RejectCode.NO_MODEL],
            ["no MODEL% stamped; will not invent"],
        )
    if ticket.american is None and ticket.line is None:
        return _finish(decision, "HOLD", [RejectCode.NO_LINES], ["no Lines number stamped"])
    if not ticket.side:
        return _finish(decision, "HOLD", [RejectCode.NO_SIDE], ["no researcher/Core side"])

    if ticket.edge is None:
        return _finish(decision, "HOLD", [RejectCode.NO_MODEL], ["no edge stamped; will not invent"])

    source = (ticket.price_source or "").strip().lower()
    if source in OPENER_PRIM_TAGS:
        codes.append(RejectCode.OPENER_PRIM)
        reasons.append("opener/PRIM auto reject")

    if ticket.pa_ok is False:
        codes.append(RejectCode.THIN_PA)
        reasons.append("Savant PA thin (pa_ok=False)")

    if market == "F5":
        _gate_f5(ticket, codes, reasons)
    elif sport == "MLB" and market == "ML":
        _gate_mlb_ml(ticket, codes, reasons)
    elif sport == "MLB" and market == "TOTAL":
        _gate_mlb_total(ticket, codes, reasons)
    elif sport == "CFB" and market in {"SPREAD", "ATS", "ML"}:
        _gate_cfb(ticket, codes, reasons)
    elif sport == "NFL" and market in {"SPREAD", "ATS", "TOTAL", "ML"}:
        _gate_nfl(ticket, codes, reasons)
    else:
        codes.append(RejectCode.NO_LOCKED_BAR)
        reasons.append(f"no locked CLEAR bar for {sport} {market}")

    if not codes:
        missing = _missing_packet(ticket, market)
        if missing:
            codes.append(RejectCode.MISSING_PACKET)
            reasons.append("CLEAR packet incomplete: " + ", ".join(missing))

    if not codes:
        status: Status = "CLEAR"
        units = float(ticket.proposed_units)
        reasons.append("passed locked CLEAR bars — candidate for Main (sole writer)")
    else:
        status = _status_for_codes(codes, ticket)
        units = 0.0
        if ticket.model_pct >= HIGH_MODEL_PCT and status != "CLEAR":
            if RejectCode.HIGH_MODEL_NOT_CLEAR not in codes:
                codes.append(RejectCode.HIGH_MODEL_NOT_CLEAR)
                reasons.append("high_model≥70 not CLEAR — shadow/reject tag")

    return _finish(decision, status, codes, reasons, units)


def apply_daily_cap(
    decisions: Sequence[GateDecision],
    *,
    cap_u: float = DAILY_CAP_SOFT_U,
) -> list[GateDecision]:
    """Soft ~3–5u CLEAR cap. CFB dogs rank first. No partial units."""
    if cap_u <= 0:
        raise ValueError("daily cap must be > 0")
    clears = [d for d in decisions if d.status == "CLEAR"]
    others = [d for d in decisions if d.status != "CLEAR"]
    ranked = sorted(clears, key=_clear_rank_key)
    kept: list[GateDecision] = []
    used = 0.0
    for dec in ranked:
        take = float(dec.proposed_units or DEFAULT_CLEAR_UNITS)
        if used + take <= cap_u + 1e-9:
            dec.units = take
            used += take
            kept.append(dec)
            continue
        dec.status = "FILL"
        dec.units = 0.0
        if RejectCode.DAILY_CAP not in dec.reject_codes:
            dec.reject_codes.append(RejectCode.DAILY_CAP)
            dec.reasons.append(f"soft daily CLEAR cap {cap_u}u (would be {used + take:.2f}u)")
        if dec.model_pct is not None and dec.model_pct >= HIGH_MODEL_PCT:
            if RejectCode.HIGH_MODEL_NOT_CLEAR not in dec.reject_codes:
                dec.reject_codes.append(RejectCode.HIGH_MODEL_NOT_CLEAR)
        kept.append(dec)
    return kept + list(others)


def _clear_rank_key(dec: GateDecision) -> tuple[int, float, float]:
    # Prefer CFB dogs, then larger edge, then higher model%.
    dog_rank = 0 if (dec.sport == "CFB" and dec.is_dog) else 1
    edge = -(dec.edge if dec.edge is not None else 0.0)
    model = -(dec.model_pct if dec.model_pct is not None else 0.0)
    return (dog_rank, edge, model)


def _gate_mlb_ml(ticket: GateTicket, codes: list[str], reasons: list[str]) -> None:
    if ticket.is_plus_money:
        if ticket.model_pct < MLB_PLUS_ML_MIN_MODEL or ticket.edge < MLB_PLUS_ML_MIN_EDGE:
            codes.append(RejectCode.MLB_PLUS_ML_SOFT)
            reasons.append(
                f"+ML needs model≥{MLB_PLUS_ML_MIN_MODEL:g} AND edge≥{MLB_PLUS_ML_MIN_EDGE:g} "
                f"(got model={ticket.model_pct:g} edge={ticket.edge:g})"
            )
        return
    if ticket.edge < MLB_FAV_ML_MIN_EDGE:
        codes.append(RejectCode.MLB_FAV_ML_EDGE)
        reasons.append(
            f"MLB favorite ML needs edge≥{MLB_FAV_ML_MIN_EDGE:g} prob pts (SOP; got {ticket.edge:g})"
        )


def _gate_mlb_total(ticket: GateTicket, codes: list[str], reasons: list[str]) -> None:
    if ticket.edge < MLB_TOTAL_MIN_EDGE_RUNS:
        codes.append(RejectCode.NO_LOCKED_BAR)
        reasons.append(
            f"MLB total needs edge≥{MLB_TOTAL_MIN_EDGE_RUNS} runs (SOP; got {ticket.edge:g})"
        )


def _gate_f5(ticket: GateTicket, codes: list[str], reasons: list[str]) -> None:
    if ticket.american is None or not ticket.f5_market_stamped:
        codes.append(RejectCode.F5_NO_MARKET)
        reasons.append("F5 skip — no real F5 market stamped")
        return
    if float(ticket.american) <= F5_JUICE_AMERICAN and ticket.model_pct < F5_JUICE_MIN_MODEL:
        codes.append(RejectCode.F5_JUICE)
        reasons.append(
            f"F5 American ≤ {F5_JUICE_AMERICAN} needs model≥{F5_JUICE_MIN_MODEL:g} "
            f"+ real F5 market (got model={ticket.model_pct:g})"
        )


def _gate_cfb(ticket: GateTicket, codes: list[str], reasons: list[str]) -> None:
    if ticket.is_favorite:
        raised = ticket.edge >= CFB_FAV_MIN_EDGE_PTS and ticket.model_pct >= CFB_FAV_MIN_MODEL
        if not raised:
            key = ""
            if ticket.line is not None and float(ticket.line) in KEY_NUMBERS_CFB:
                key = f" (key number {ticket.line:g})"
            who = "home/G6 " if (ticket.is_home or ticket.is_g6) else ""
            codes.append(RejectCode.CFB_FAV_BAR)
            reasons.append(
                f"CFB {who}favorite{key} needs edge≥{CFB_FAV_MIN_EDGE_PTS:g} pts AND "
                f"model≥{CFB_FAV_MIN_MODEL:g} (got model={ticket.model_pct:g} edge={ticket.edge:g})"
            )
        return
    if ticket.is_dog and ticket.edge < CFB_DOG_MIN_EDGE_PTS:
        codes.append(RejectCode.CFB_DOG_EDGE)
        reasons.append(
            f"CFB dog needs edge≥{CFB_DOG_MIN_EDGE_PTS:g} pts (SOP; got {ticket.edge:g})"
        )


def _gate_nfl(ticket: GateTicket, codes: list[str], reasons: list[str]) -> None:
    if ticket.edge < NFL_MIN_EDGE_PTS:
        codes.append(RejectCode.NFL_EDGE)
        reasons.append(
            f"NFL needs edge≥{NFL_MIN_EDGE_PTS:g} pts after w=0.22 blend (got {ticket.edge:g})"
        )


def _missing_packet(ticket: GateTicket, market: str) -> list[str]:
    missing: list[str] = []
    if ticket.sport_norm == "MLB" and not ticket.packet_sp:
        missing.append("SP")
    if not ticket.packet_sit:
        missing.append("Sit")
    if ticket.sport_norm == "MLB" and market in {"F5", "TOTAL"} and not ticket.packet_wx:
        missing.append("WX")
    if ticket.sport_norm == "NFL" and ticket.game_day and not ticket.packet_sit:
        if "Sit" not in missing:
            missing.append("Sit")
    return missing


def _status_for_codes(codes: Sequence[str], ticket: GateTicket) -> Status:
    hold_first = {
        RejectCode.SOCCER_OFF,
        RejectCode.SPORT_OFF,
        RejectCode.NO_MODEL,
        RejectCode.NO_LINES,
        RejectCode.NO_SIDE,
        RejectCode.CFB_FAV_BAR,
        RejectCode.F5_NO_MARKET,
        RejectCode.NO_LOCKED_BAR,
    }
    if any(c in hold_first for c in codes):
        return "HOLD"
    return "FILL"


def _finish(
    decision: GateDecision,
    status: Status,
    codes: list[str],
    reasons: list[str],
    units: float = 0.0,
) -> GateDecision:
    decision.status = status
    decision.reject_codes = list(codes)
    decision.reasons = list(reasons)
    decision.units = units
    return decision
