"""
Builds data/picks.json using REAL MoneyLine model probabilities from the
/v1/edge feed (data/moneyline_signals.json), combined with:
  - market-specific edge thresholds
  - line shopping (best odds per outcome across bookmakers)
  - injury/lineup exclusion filtering
  - Kelly Criterion stake sizing

No placeholder probabilities are used. If no real signal exists for a
market, the pick is tagged "no_model" and excluded from staking.
"""
import json
import os
import sys
import datetime
from kelly import recommended_stake

# bet_types lives in model/; make it importable when run from the repo root.
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "model"))
from bet_types import generate_bets, normalize_market  # noqa: E402

SCHEMA_VERSION = "2.0.0"
DEFAULT_MIN_EDGE = 0.05


def _bet_type_config():
    """Bet-type generation settings, overridable via env vars."""
    mode = (os.environ.get("BET_TYPES", "both") or "both").strip().lower()
    if mode not in ("individual", "combined", "both"):
        mode = "both"
    return {
        "mode": mode,
        "combo_edge_threshold": float(os.environ.get("COMBO_EDGE_THRESHOLD", DEFAULT_MIN_EDGE)),
        "min_legs": int(os.environ.get("COMBO_MIN_LEGS", "2")),
        "max_legs": int(os.environ.get("COMBO_MAX_LEGS", "3")),
        "teaser_points": float(os.environ.get("TEASER_POINTS", "6.0")),
    }
EDGE_RULES = {
    "MLB": {"moneyline": 0.035, "total": 0.040, "player_prop": 0.050},
    "NFL": {"spread": 0.045, "side": 0.045, "total": 0.040, "player_prop": 0.060},
}
DEFAULT_BANKROLL = 1000.0
KELLY_MULTIPLIER = 0.25
MAX_STAKE_PCT = 0.03


def american_to_implied_prob(odds):
    if odds is None:
        return None
    odds = float(odds)
    if odds < 0:
        return -odds / (-odds + 100)
    return 100 / (odds + 100)


def get_market_threshold(league, market_type):
    market_type = (market_type or "").lower()
    league_rules = EDGE_RULES.get(league, {})
    if "prop" in market_type or market_type.startswith(("batter_", "pitcher_", "player_")):
        return league_rules.get("player_prop", DEFAULT_MIN_EDGE)
    if "moneyline" in market_type or market_type in ("ml", "h2h", "side"):
        return league_rules.get("moneyline", league_rules.get("side", DEFAULT_MIN_EDGE))
    if "spread" in market_type:
        return league_rules.get("spread", DEFAULT_MIN_EDGE)
    if "total" in market_type or market_type in ("ou", "over_under"):
        return league_rules.get("total", DEFAULT_MIN_EDGE)
    return DEFAULT_MIN_EDGE


def tier_from_edge(edge, threshold):
    if edge is None:
        return "no_model"
    if edge >= threshold + 0.03:
        return "tier_c"
    if edge >= threshold + 0.015:
        return "tier_b"
    if edge >= threshold:
        return "tier_a"
    return "no_edge"


def load_json(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except FileNotFoundError:
        return default


def load_injury_flags():
    data = load_json("data/injuries_lineups.json", {"nfl_injuries": [], "mlb_lineups": []})
    flagged_players = set()
    for row in data.get("nfl_injuries", []):
        status = (row.get("status") or "").lower()
        if status in ("out", "doubtful", "ir", "injured_reserve"):
            pid = row.get("player_id")
            if pid:
                flagged_players.add(str(pid))
    return flagged_players


def build_line_shopped_signals(signals):
    """
    Groups MoneyLine edge signals by (league, event_id, market, outcome, point)
    and keeps only the single best-odds offer per group -- this is the line
    shopping step. Also carries model_prob and ev_pct from that same group
    (modelProb is identical across bookmakers for the same outcome).
    """
    groups = {}
    for row in signals:
        key = (
            row.get("league"),
            row.get("event_id"),
            row.get("market"),
            row.get("outcome"),
            row.get("point"),
        )
        groups.setdefault(key, []).append(row)

    best_rows = {}
    for key, rows in groups.items():
        best = max(rows, key=lambda r: r.get("odds", -10_000))
        best_rows[key] = best
    return best_rows


def build_picks_from_signals(best_rows, injury_flags):
    picks = []
    for (league, event_id, market, outcome, point), row in best_rows.items():
        league_label = league.upper() if league else None
        odds = row.get("odds")
        implied = american_to_implied_prob(odds)
        model_prob = row.get("model_prob")
        threshold = get_market_threshold(league_label, market)

        edge = None
        if implied is not None and model_prob is not None:
            edge = round(model_prob - implied, 4)

        tier = tier_from_edge(edge, threshold)

        player_key = str(row.get("description") or "").lower()
        if player_key and player_key in injury_flags:
            tier = "excluded_injury"

        stake_info = None
        if tier in ("tier_a", "tier_b", "tier_c") and model_prob is not None and odds is not None:
            stake_info = recommended_stake(
                model_prob=model_prob,
                american_odds=odds,
                bankroll=DEFAULT_BANKROLL,
                kelly_multiplier=KELLY_MULTIPLIER,
                max_stake_pct=MAX_STAKE_PCT,
            )

        picks.append({
            "pick_id": f"{league}-{event_id}-{market}-{outcome}".replace(" ", "_"),
            "league": league_label,
            "event_id": event_id,
            "market_type": market,
            "selection": outcome,
            "line": point,
            "book": row.get("bookmaker_id"),
            "odds": odds,
            "implied_prob": round(implied, 4) if implied is not None else None,
            "model_prob": model_prob,
            "edge_pct": edge,
            "ev_pct": row.get("ev_pct"),
            "confidence_tier": tier,
            "threshold_used": threshold,
            "recommended_stake": stake_info,
            "notes": row.get("description"),
            "line_shopped": True,
        })
    return picks


def build_mlb_schedule_rows(mlb_data):
    rows = []
    for game in mlb_data.get("games", []):
        rows.append({
            "pick_id": f"mlb-schedule-{game['game_id']}",
            "league": "MLB",
            "event_id": str(game["game_id"]),
            "market_type": "info_only",
            "selection": None,
            "line": None,
            "book": None,
            "odds": None,
            "implied_prob": None,
            "model_prob": None,
            "edge_pct": None,
            "ev_pct": None,
            "confidence_tier": "data_only",
            "threshold_used": None,
            "recommended_stake": None,
            "notes": f"Probable pitchers: {game.get('away_probable_pitcher')} vs {game.get('home_probable_pitcher')}",
            "line_shopped": False,
        })
    return rows


def main():
    mlb_data = load_json("data/mlb_raw.json", {"games": []})
    signals_data = load_json("data/moneyline_signals.json", {"edge_signals": []})
    injury_flags = load_injury_flags()

    best_rows = build_line_shopped_signals(signals_data.get("edge_signals", []))
    signal_picks = build_picks_from_signals(best_rows, injury_flags)
    schedule_rows = build_mlb_schedule_rows(mlb_data)

    picks = schedule_rows + signal_picks

    # Tag every pick with its bet category/type so the frontend can group them.
    for p in picks:
        p["bet_category"] = "single"
        p["bet_type"] = normalize_market(p.get("market_type"))
        p.setdefault("status", "pending")

    tier_counts = {}
    for p in picks:
        tier_counts[p["confidence_tier"]] = tier_counts.get(p["confidence_tier"], 0) + 1

    # Build Singles vs Combinations. Signal picks carry a numeric `edge_pct`
    # (a fraction); schedule/info rows have edge None and are ignored for combos.
    cfg = _bet_type_config()
    generic_picks = [{
        "game_id": p.get("event_id"),
        "league": p.get("league"),
        "market": p.get("market_type"),
        "selection": p.get("selection"),
        "line": p.get("line"),
        "odds": p.get("odds"),
        "model_prob": p.get("model_prob"),
        "edge": p.get("edge_pct"),
        "pick_id": p.get("pick_id"),
        "status": "pending",
    } for p in signal_picks]
    generated = generate_bets(generic_picks, cfg)

    output_picks = picks if cfg["mode"] in ("individual", "both") else []

    output = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "default_min_edge_threshold": DEFAULT_MIN_EDGE,
        "edge_rules": EDGE_RULES,
        "bet_type_mode": cfg["mode"],
        "combo_edge_threshold": cfg["combo_edge_threshold"],
        "tier_counts": tier_counts,
        "picks": output_picks,
        "combinations": generated["combinations"],
    }

    with open("data/picks.json", "w") as f:
        json.dump(output, f, indent=2)

    print(f"Wrote {len(output_picks)} singles and {len(generated['combinations'])} "
          f"combinations to data/picks.json (mode={cfg['mode']})")
    print(f"Tier breakdown: {tier_counts}")


if __name__ == "__main__":
    main()
