"""Slate in → candidate JSON out for the sole CLEAR writer (Main).

Schema
------
Input slate (JSON object)::

    {
      "slate_id": str,
      "as_of": ISO-8601,
      "generated_at": optional ISO-8601 (defaults to as_of — deterministic),
      "fixture": bool,
      "daily_cap_u": float,          # default 5.0
      "games": [
        {
          "game_id": str,
          "sport": "MLB" | "NFL" | "CFB" | "NCAAF" | soccer...,
          "home": str,
          "away": str,
          "kickoff": optional,
          "packet": {"sit": str|bool, "sp": str|bool, "wx": str|bool, "pa_ok": bool},
          "tickets": [ { see below } ]
        }
      ]
    }

Each ticket::

    {
      "market": "ML" | "F5" | "SPREAD" | "TOTAL",
      "side": str,
      "is_home": bool,
      "is_g6": bool,
      "american": int,
      "american_opp": optional int,   # enables de-vig
      "line": optional float,
      "price_source": "shop" | "opener" | "prim" | ...,
      "f5_market_stamped": bool,
      "proposed_units": float,
      "prices": {"open": stamp, "best": stamp, "close": stamp},
      "model": {"model_pct": 0-100, "edge": number, "source": "provided", "model_version": str},
      "model_inputs": { sport-specific — used only if model.model_pct is absent }
    }

Output (``prediction_core.candidates.v1``)::

    {
      "schema_version": "prediction_core.candidates.v1",
      "writer": "Main",
      "scoreboard": "side_pass_fail",
      "clv_policy": "internal_promote_kill",
      "soccer": "off",
      "daily": {"cap_u", "clear_u", "clear_n"},
      "summary": {"CLEAR", "FILL", "HOLD"},
      "candidates": [ { id, game_id, sport, market, side, american, line,
                        model_pct, model_source, edge, edge_unit, status,
                        units, reject_codes, reasons, prices, clv, packet } ]
    }

Never invents MODEL%, lines, injuries, or stats. If a feed is missing the
ticket is HOLD/FILL — never CLEAR.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from prediction_core.gates import (
    DAILY_CAP_SOFT_U,
    DEFAULT_CLEAR_UNITS,
    GateDecision,
    GateTicket,
    apply_daily_cap,
    evaluate_ticket,
)
from prediction_core.markets import (
    clv_grade,
    join_open_best_close,
    probability_edge,
    two_way_devig,
)
from prediction_core.models.cfb import predict_cfb
from prediction_core.models.mlb import predict_mlb
from prediction_core.models.nfl import predict_nfl

CANDIDATES_SCHEMA_VERSION = "prediction_core.candidates.v1"
WRITER = "Main"


@dataclass
class ResolvedModel:
    model_pct: float | None
    edge: float | None
    source: str | None
    version: str | None
    edge_unit: str
    notes: tuple[str, ...]
    extras: dict[str, Any]


def run_slate(slate: Mapping[str, Any]) -> dict[str, Any]:
    """Evaluate every ticket on a slate and emit the candidate packet."""
    if not isinstance(slate, Mapping):
        raise TypeError("slate must be a JSON object")
    slate_id = str(slate.get("slate_id") or slate.get("id") or "")
    if not slate_id:
        raise ValueError("slate_id is required")
    as_of = slate.get("as_of")
    if not as_of:
        raise ValueError("as_of is required (point-in-time stamp; will not invent)")
    generated_at = slate.get("generated_at") or as_of
    cap_u = float(slate.get("daily_cap_u", DAILY_CAP_SOFT_U))
    fixture = bool(slate.get("fixture", False))

    games = slate.get("games")
    if not isinstance(games, list):
        raise ValueError("slate.games must be a list")

    built: list[dict[str, Any]] = []
    decisions: list[GateDecision] = []

    for game in games:
        if not isinstance(game, Mapping):
            raise TypeError("each game must be an object")
        tickets = game.get("tickets") or []
        if not isinstance(tickets, list):
            raise ValueError(f"{game.get('game_id')}: tickets must be a list")
        packet = _packet_flags(game.get("packet") or {})
        for raw in tickets:
            if not isinstance(raw, Mapping):
                raise TypeError("each ticket must be an object")
            candidate, decision = _build_ticket(game, raw, packet, as_of)
            built.append(candidate)
            decisions.append(decision)

    capped = apply_daily_cap(decisions, cap_u=cap_u)
    by_id = {id(d): d for d in capped}
    # apply_daily_cap returns the same objects (mutated). Re-read status/units.
    for cand, dec in zip(built, decisions):
        final = by_id.get(id(dec), dec)
        cand["status"] = final.status
        cand["units"] = final.units
        cand["reject_codes"] = list(final.reject_codes)
        cand["reasons"] = list(final.reasons)

    clear_u = sum(c["units"] for c in built if c["status"] == "CLEAR")
    summary = {
        "CLEAR": sum(1 for c in built if c["status"] == "CLEAR"),
        "FILL": sum(1 for c in built if c["status"] == "FILL"),
        "HOLD": sum(1 for c in built if c["status"] == "HOLD"),
    }
    return {
        "schema_version": CANDIDATES_SCHEMA_VERSION,
        "slate_id": slate_id,
        "as_of": as_of,
        "generated_at": generated_at,
        "writer": WRITER,
        "scoreboard": "side_pass_fail",
        "clv_policy": "internal_promote_kill",
        "note": (
            "Candidates only. Main is sole CLEAR writer. "
            "Luis scores side pass/fail first; CLV is internal for promote/kill. "
            "Never invent MODEL%/lines/injuries/stats. Soccer OFF."
        ),
        "fixture": fixture,
        "sports_in_scope": ["MLB", "NFL", "CFB"],
        "soccer": "off",
        "daily": {
            "cap_u": cap_u,
            "clear_u": round(clear_u, 4),
            "clear_n": summary["CLEAR"],
        },
        "summary": summary,
        "candidates": built,
    }


def _build_ticket(
    game: Mapping[str, Any],
    raw: Mapping[str, Any],
    packet: dict[str, Any],
    as_of: str,
) -> tuple[dict[str, Any], GateDecision]:
    sport = str(game.get("sport") or "")
    game_id = str(game.get("game_id") or "")
    market = str(raw.get("market") or "")
    side = raw.get("side")
    american = _maybe_number(raw.get("american"))
    line = _maybe_number(raw.get("line"))
    prices = join_open_best_close(
        (raw.get("prices") or {}).get("open") if isinstance(raw.get("prices"), Mapping) else raw.get("open"),
        (raw.get("prices") or {}).get("best") if isinstance(raw.get("prices"), Mapping) else raw.get("best"),
        (raw.get("prices") or {}).get("close") if isinstance(raw.get("prices"), Mapping) else raw.get("close"),
    )
    if american is None and prices.shop_price is not None:
        american = float(prices.shop_price)
    if line is None and prices.shop_line is not None:
        line = float(prices.shop_line)

    resolved = resolve_model(
        sport=sport,
        market=market,
        side=str(side) if side is not None else None,
        is_home=bool(raw.get("is_home", False)),
        home=str(game.get("home") or ""),
        away=str(game.get("away") or ""),
        american=american,
        american_opp=_maybe_number(raw.get("american_opp")),
        line=line,
        model_block=raw.get("model") if isinstance(raw.get("model"), Mapping) else None,
        model_inputs=raw.get("model_inputs") if isinstance(raw.get("model_inputs"), Mapping) else None,
    )

    f5_stamped = bool(raw.get("f5_market_stamped"))
    if market.upper() in {"F5", "FIRST_5", "F5_ML"} and american is not None:
        f5_stamped = f5_stamped or True

    ticket = GateTicket(
        sport=sport,
        market=market,
        side=str(side) if side else None,
        american=american,
        line=line,
        model_pct=resolved.model_pct,
        edge=resolved.edge,
        price_source=raw.get("price_source") or raw.get("source"),
        f5_market_stamped=f5_stamped,
        is_home=bool(raw.get("is_home", False)),
        is_g6=bool(raw.get("is_g6", False)),
        packet_sp=bool(packet["sp"]),
        packet_sit=bool(packet["sit"]),
        packet_wx=bool(packet["wx"]),
        pa_ok=packet["pa_ok"],
        game_day=bool(game.get("game_day", False) or raw.get("game_day", False)),
        proposed_units=float(raw.get("proposed_units", DEFAULT_CLEAR_UNITS)),
        game_id=game_id or None,
    )
    decision = evaluate_ticket(ticket)
    close_american = prices.close.american if prices.close else None
    clv = clv_grade(american, close_american)
    candidate_id = raw.get("id") or _candidate_id(game_id, market, side)
    candidate = {
        "id": candidate_id,
        "game_id": game_id,
        "sport": decision.sport,
        "home": game.get("home"),
        "away": game.get("away"),
        "kickoff": game.get("kickoff"),
        "market": decision.market,
        "side": side,
        "is_home": bool(raw.get("is_home", False)),
        "american": american,
        "line": line,
        "price_source": ticket.price_source,
        "model_pct": resolved.model_pct,
        "model_source": resolved.source,
        "model_version": resolved.version,
        "model_notes": list(resolved.notes),
        "edge": resolved.edge,
        "edge_unit": resolved.edge_unit,
        "status": decision.status,
        "units": decision.units,
        "reject_codes": list(decision.reject_codes),
        "reasons": list(decision.reasons),
        "prices": prices.to_dict(),
        "clv": clv.to_dict(),
        "packet": {
            "sit": packet["sit_text"],
            "sp": packet["sp_text"],
            "wx": packet["wx_text"],
            "pa_ok": packet["pa_ok"],
        },
        "as_of": as_of,
        "extras": resolved.extras,
    }
    return candidate, decision


def resolve_model(
    *,
    sport: str,
    market: str,
    side: str | None,
    is_home: bool,
    home: str,
    away: str,
    american: float | None,
    american_opp: float | None,
    line: float | None,
    model_block: Mapping[str, Any] | None,
    model_inputs: Mapping[str, Any] | None,
) -> ResolvedModel:
    """Prefer a stamped Sim MODEL%. Compute only when inputs exist and no stamp."""
    if model_block and model_block.get("model_pct") is not None:
        pct = _as_model_pct(model_block.get("model_pct"))
        edge = model_block.get("edge")
        if edge is None:
            return ResolvedModel(
                model_pct=pct,
                edge=None,
                source="provided",
                version=_maybe_str(model_block.get("model_version") or model_block.get("version")),
                edge_unit=_edge_unit(sport, market),
                notes=("model_pct provided; edge missing — will not invent edge",),
                extras={},
            )
        return ResolvedModel(
            model_pct=pct,
            edge=float(edge),
            source=str(model_block.get("source") or "provided"),
            version=_maybe_str(model_block.get("model_version") or model_block.get("version")),
            edge_unit=_edge_unit(sport, market),
            notes=("stamped MODEL% + edge (Sim/Core) — not invented",),
            extras={},
        )

    if not model_inputs:
        return ResolvedModel(
            model_pct=None,
            edge=None,
            source=None,
            version=None,
            edge_unit=_edge_unit(sport, market),
            notes=("no model stamp and no model_inputs",),
            extras={},
        )

    sport_n = sport.strip().upper()
    if sport_n in {"NCAAF", "NCAA-F", "NCAA_FB"}:
        sport_n = "CFB"
    market_n = market.strip().upper()

    if sport_n == "MLB":
        pred = predict_mlb(**_mlb_kwargs(model_inputs, market_n))
        if not pred.ok or pred.p_home_ml is None or pred.p_away_ml is None:
            return ResolvedModel(None, None, "computed", pred.model_version, "prob_pts", pred.notes, {})
        p_side = _mlb_side_prob(pred.p_home_ml, pred.p_away_ml, side, is_home, home, away)
        edge = _prob_edge_from_market(p_side, american, american_opp)
        extras = {"lam_home": pred.lam_home, "lam_away": pred.lam_away, "family": pred.family}
        return ResolvedModel(
            model_pct=round(p_side * 100.0, 4) if p_side is not None else None,
            edge=edge,
            source="computed",
            version=pred.model_version,
            edge_unit="prob_pts",
            notes=pred.notes,
            extras=extras,
        )

    if sport_n == "NFL":
        pred = predict_nfl(**_nfl_kwargs(model_inputs, line, is_home))
        if not pred.ok or pred.p_home_cover is None:
            return ResolvedModel(None, None, "computed", pred.model_version, "spread_pts", pred.notes, {})
        p_side = pred.p_home_cover if _side_is_home(side, is_home, home, away) else pred.p_away_cover
        edge = None
        if pred.blend is not None:
            raw = pred.blend.edge_pts
            edge = raw if _side_is_home(side, is_home, home, away) else -raw
        extras = {"mu": pred.mu, "sigma": pred.sigma, "blend": pred.blend.edge_pts if pred.blend else None}
        return ResolvedModel(
            model_pct=round(float(p_side) * 100.0, 4) if p_side is not None else None,
            edge=None if edge is None else round(edge, 4),
            source="computed",
            version=pred.model_version,
            edge_unit="spread_pts",
            notes=pred.notes,
            extras=extras,
        )

    if sport_n == "CFB":
        pred = predict_cfb(**_cfb_kwargs(model_inputs, line, is_home))
        if not pred.ok or pred.p_home_cover is None or pred.mu is None or pred.home_spread is None:
            return ResolvedModel(None, None, "computed", pred.model_version, "spread_pts", pred.notes, {})
        p_side = pred.p_home_cover if _side_is_home(side, is_home, home, away) else pred.p_away_cover
        raw_edge = pred.mu + pred.home_spread
        edge = raw_edge if _side_is_home(side, is_home, home, away) else -raw_edge
        extras = {"mu": pred.mu, "sigma": pred.sigma, "components_used": list(pred.components_used)}
        return ResolvedModel(
            model_pct=round(float(p_side) * 100.0, 4) if p_side is not None else None,
            edge=round(edge, 4),
            source="computed",
            version=pred.model_version,
            edge_unit="spread_pts",
            notes=pred.notes,
            extras=extras,
        )

    return ResolvedModel(
        None,
        None,
        None,
        None,
        _edge_unit(sport, market),
        (f"no compute path for sport {sport_n}",),
        {},
    )


def _mlb_kwargs(inputs: Mapping[str, Any], market: str) -> dict[str, Any]:
    allowed = {
        "lam_home",
        "lam_away",
        "base_home",
        "base_away",
        "park_factor",
        "pitcher_adj_home",
        "pitcher_adj_away",
        "lineup_adj_home",
        "lineup_adj_away",
        "weather_adj",
        "family",
        "alpha",
        "elo_home",
        "elo_away",
        "elo_hfa",
        "elo_weight",
        "f5",
        "model_version",
    }
    kwargs = {k: inputs[k] for k in allowed if k in inputs}
    if market in {"F5", "FIRST_5", "F5_ML"}:
        kwargs.setdefault("f5", True)
    return kwargs


def _nfl_kwargs(inputs: Mapping[str, Any], line: float | None, is_home: bool) -> dict[str, Any]:
    allowed = {
        "mu",
        "sigma",
        "home_spread",
        "rating_home",
        "rating_away",
        "hfa",
        "points_per_rating",
        "blend_w",
        "side",
        "model_version",
    }
    kwargs = {k: inputs[k] for k in allowed if k in inputs}
    if "home_spread" not in kwargs:
        if inputs.get("home_spread") is not None:
            kwargs["home_spread"] = inputs["home_spread"]
        elif line is not None:
            kwargs["home_spread"] = float(line) if is_home else -float(line)
    return kwargs


def _cfb_kwargs(inputs: Mapping[str, Any], line: float | None, is_home: bool) -> dict[str, Any]:
    allowed = {
        "mu",
        "sigma",
        "home_spread",
        "components",
        "weights",
        "week",
        "shrink",
        "model_version",
    }
    kwargs = {k: inputs[k] for k in allowed if k in inputs}
    if "home_spread" not in kwargs and line is not None:
        kwargs["home_spread"] = float(line) if is_home else -float(line)
    return kwargs


def _mlb_side_prob(
    p_home_ml: float,
    p_away_ml: float,
    side: str | None,
    is_home: bool,
    home: str,
    away: str,
) -> float | None:
    if _side_is_home(side, is_home, home, away):
        return p_home_ml
    if side:
        return p_away_ml
    return None


def _side_is_home(side: str | None, is_home: bool, home: str, away: str) -> bool:
    if is_home:
        return True
    if not side:
        return False
    return side.strip().upper() == (home or "").strip().upper()


def _prob_edge_from_market(
    model_p: float | None,
    american: float | None,
    american_opp: float | None,
) -> float | None:
    if model_p is None or american is None:
        return None
    if american_opp is not None:
        fair = two_way_devig(american, american_opp)
        market_p = fair.p_a
    else:
        from prediction_core.markets import american_to_implied

        market_p = american_to_implied(american)
    return round(probability_edge(model_p, market_p, as_points=True), 4)


def _as_model_pct(value: Any) -> float:
    pct = float(value)
    # Accept a probability in (0, 1] when the caller stamped a fraction.
    if 0.0 < pct <= 1.0:
        pct *= 100.0
    if not 0.0 <= pct <= 100.0:
        raise ValueError("model_pct must be in [0, 100] (or a probability in (0, 1])")
    return pct


def _edge_unit(sport: str, market: str) -> str:
    market_n = (market or "").upper()
    sport_n = (sport or "").upper()
    if sport_n == "MLB" and market_n == "TOTAL":
        return "runs"
    if market_n in {"SPREAD", "ATS", "TOTAL"} and sport_n in {"NFL", "CFB", "NCAAF"}:
        return "spread_pts"
    return "prob_pts"


def _packet_flags(raw: Mapping[str, Any] | None) -> dict[str, Any]:
    raw = raw or {}
    sit = raw.get("sit")
    sp = raw.get("sp")
    wx = raw.get("wx")
    return {
        "sit": _truthy_note(sit),
        "sp": _truthy_note(sp),
        "wx": _truthy_note(wx),
        "sit_text": sit if isinstance(sit, str) else sit,
        "sp_text": sp if isinstance(sp, str) else sp,
        "wx_text": wx if isinstance(wx, str) else wx,
        "pa_ok": raw.get("pa_ok"),
    }


def _truthy_note(value: Any) -> bool:
    if value is True:
        return True
    if value is False or value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    return bool(value)


def _candidate_id(game_id: str, market: str, side: Any) -> str:
    side_s = str(side or "na").replace(" ", "")
    return f"{game_id}:{market}:{side_s}"


def _maybe_number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    return float(value)


def _maybe_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
