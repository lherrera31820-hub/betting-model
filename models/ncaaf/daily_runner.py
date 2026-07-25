"""
daily_runner.py — NCAAF daily picks generator.
Runs daily via GitHub Actions (generate-picks.yml). Zero user input required.

Unlike MLB (which trains an ensemble on statsapi historical data), there's no
free equivalent historical stats source for NCAAF, so this runs on Elo alone:
ratings start neutral (1500/team) and update from each day's completed games
via SportsDataIO. This mirrors the "no trained model yet" cold-start fallback
models/mlb/daily_runner.py already uses before it has enough history.

Flow:
1. Load saved Elo state
2. Fetch yesterday's completed games (SportsDataIO) -> update Elo ratings
3. Fetch today's scheduled games (SportsDataIO)
4. Predict P(home win) per game via Elo
5. Fetch live odds (The Odds API) + find +EV bets
6. Categorize into singles/combinations (same generator MLB uses)
7. Write data/picks_ncaaf.json
8. Save Elo state

Always writes data/picks_ncaaf.json and exits 0, even on API failures — the
frontend/deploy step needs a valid file to exist either way.
"""

import os
import json
from datetime import datetime, timedelta

from config import BANKROLL, ELO_K, ELO_HFA, bet_type_config
from elo import EloTracker
from sportsdata import fetch_games_by_date
from odds import fetch_live_odds, find_ev_bets
from bet_types import generate_bets

SPORT_NAME     = "NCAAF"
SCHEMA_VERSION = "1.0.0"

# This script lives at models/ncaaf/daily_runner.py, two directories below
# the repo root (models/ncaaf -> models -> repo root), so REPO_ROOT needs
# three dirname() calls — same pattern (and same past bug, see PR #8 on the
# MLB runner) that must be gotten right here too.
REPO_ROOT   = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PICKS_PATH  = os.path.join(REPO_ROOT, 'data', 'picks_ncaaf.json')
ELO_STATE   = os.path.join(REPO_ROOT, 'elo_state_ncaaf.pkl')


def load_elo():
    tracker = EloTracker(k=ELO_K, hfa=ELO_HFA)
    tracker.load(ELO_STATE)
    return tracker


def update_elo_from_yesterday(tracker, yesterday_str):
    games = fetch_games_by_date(yesterday_str)
    updated = 0
    for g in games:
        status = (g.get('status') or '').lower()
        if status != 'final':
            continue
        hs, as_ = g.get('home_score'), g.get('away_score')
        if hs is None or as_ is None:
            continue
        home_win = 1.0 if hs > as_ else (0.0 if as_ > hs else 0.5)
        tracker.update(g['home_key'], g['away_key'], home_win)
        updated += 1
    print(f"  Elo updated from {updated} final game(s) on {yesterday_str}")
    return tracker


def build_predictions(tracker, todays_games):
    predictions = []
    for g in todays_games:
        status = (g.get('status') or '').lower()
        if status in ('final', 'canceled', 'cancelled', 'postponed'):
            continue
        p_home = tracker.predict(g['home_key'], g['away_key'])
        label = f"Week {g['week']}" if g.get('week') else ''
        predictions.append({
            'home_team': g['home_team'],
            'away_team': g['away_team'],
            'p_home':    p_home,
            'venue':     label,
        })
    return predictions


def get_performance_stats():
    """No historical bet log yet for this sport — returns neutral placeholders
    (same shape tracker.get_performance_stats() would return for MLB) so the
    frontend's Performance section renders without special-casing this sport."""
    return {"roi": 0.0, "win_rate": 0.0, "settled_bets": 0, "avg_clv": 0.0}


def write_picks_json(ev_bets, today_str, bankroll):
    cfg = bet_type_config()
    generic_picks = []
    for b in ev_bets:
        generic_picks.append({
            "home_team":   b["home_team"],
            "away_team":   b["away_team"],
            "bet_side":    b["bet_side"],
            "bet_team":    b["bet_team"],
            "bet_category": "single",
            "bet_type":    "moneyline",
            "status":      "pending",
            "edge_pct":    b["edge_pct"],
            "model_prob":  b["model_prob"],
            "market_prob": b["market_prob"],
            "best_odds":   b["best_odds"],
            "best_book":   b["best_book"],
            "kelly_bet":   b["kelly_bet_$"],
            "venue":       b.get("venue", ""),
            "tier":        "high" if b["edge_pct"] >= 5 else "medium" if b["edge_pct"] >= 3.5 else "low",
        })
    generated = generate_bets(generic_picks, cfg)

    output = {
        "status":         "ok",
        "schema_version": SCHEMA_VERSION,
        "generated_at":   datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ'),
        "date_display":   today_str,
        "sport":          SPORT_NAME,
        "bankroll":        round(bankroll, 2),
        "performance":    get_performance_stats(),
        "bet_type_mode":  cfg["mode"],
        "picks":          generated["singles"] if cfg["mode"] in ("individual", "both") else [],
        "combinations":   generated["combinations"],
    }

    os.makedirs(os.path.dirname(PICKS_PATH), exist_ok=True)
    with open(PICKS_PATH, "w") as f:
        json.dump(output, f, indent=2)
    print(f"picks_ncaaf.json written: {len(output['picks'])} singles, "
          f"{len(output['combinations'])} combinations (mode={cfg['mode']}) -> {PICKS_PATH}")


def write_no_data(reason):
    os.makedirs(os.path.dirname(PICKS_PATH), exist_ok=True)
    with open(PICKS_PATH, "w") as f:
        json.dump({
            "status": "no_data",
            "reason": str(reason)[:200],
            "sport": SPORT_NAME,
            "generated_at": datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ'),
        }, f, indent=2)
    print(f"no_data picks_ncaaf.json written ({reason}) -> {PICKS_PATH}")


def run_pipeline():
    today = datetime.utcnow().date()
    yesterday_str = (today - timedelta(days=1)).isoformat()
    today_str = today.isoformat()

    print(f"[NCAAF] Loading Elo state...")
    tracker = load_elo()

    print(f"[NCAAF] Updating Elo from {yesterday_str}...")
    tracker = update_elo_from_yesterday(tracker, yesterday_str)

    print(f"[NCAAF] Fetching today's schedule ({today_str})...")
    todays_games = fetch_games_by_date(today_str)
    if not todays_games:
        tracker.save(ELO_STATE)
        write_no_data(f"no NCAAF games scheduled for {today_str} (likely off-season)")
        return

    predictions = build_predictions(tracker, todays_games)
    if not predictions:
        tracker.save(ELO_STATE)
        write_no_data(f"no upcoming NCAAF games for {today_str}")
        return

    print(f"[NCAAF] Fetching live odds...")
    live_odds = fetch_live_odds()
    if not live_odds:
        print("  Warning: could not fetch odds. No picks will be produced today.")

    ev_bets = find_ev_bets(predictions, live_odds, bankroll=BANKROLL)
    print(f"  +EV bets found: {len(ev_bets)}")

    write_picks_json(ev_bets, today_str, BANKROLL)
    tracker.save(ELO_STATE)
    print("[NCAAF] Done. Elo state saved.")


def main():
    if not os.environ.get('ODDS_API_KEY') or not os.environ.get('SPORTSDATAIO_API_KEY'):
        write_no_data("ODDS_API_KEY or SPORTSDATAIO_API_KEY not set")
        return
    try:
        run_pipeline()
    except Exception as e:
        print(f"[NCAAF] Pipeline error: {e}")
        write_no_data(f"pipeline error: {e}")


if __name__ == '__main__':
    main()
