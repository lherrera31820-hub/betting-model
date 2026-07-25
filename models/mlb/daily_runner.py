"""
daily_runner.py — Main automated daily script.
Runs every day at scheduled time via GitHub Actions.
Zero user input required.

Flow:
1. Load saved state (model, Elo ratings, historical data)
2. Fetch yesterday's results -> update rolling stats + Elo + bet log
3. Fetch today's schedule + pitchers
4. Fetch pitcher season stats for today's starters
5. Build feature vectors for today's games
6. Predict probabilities (ensemble + Elo)
7. Fetch live odds + find +EV bets
8. Size bets via Kelly Criterion
9. Send alert (text/email)
10. Log bets to CSV
11. Retrain model weekly on all historical data
"""

import statsapi
import pandas as pd
import numpy as np
import pickle
import os
import json
import time
from datetime import datetime, timedelta
import warnings
warnings.filterwarnings('ignore')

from collections import defaultdict

from config import (BANKROLL, MIN_PRIOR_GAMES, ELO_K, ELO_HFA, KELLY_FRACTION,
                    bet_type_config)
from bet_types import generate_bets
from features import (fetch_pitcher_stats, build_feature_vector, LEAGUE_AVG,
                       get_park_factor, get_umpire_factor)
from features_phase2 import build_phase2_features, compute_strength_of_schedule
from model import (train_models, load_models, predict_proba_ensemble,
                   EloTracker, combined_probability, heuristic_probability,
                   FEATURE_COLS)
from odds import fetch_live_odds, find_ev_bets
from alerts import send_alert
from tracker import log_bets, update_results, print_summary, get_performance_stats

# ---- State files ----
STATE_FILE       = 'daily_state.pkl'
HISTORY_FILE     = 'game_history.csv'
PITCHER_CACHE    = 'pitcher_cache.json'
RETRAIN_TRACKER  = 'last_retrain.txt'

# ---- Picks output (consumed by the frontend / committed by GitHub Actions) ----
# Repo root is the parent of this script's directory (model/).
REPO_ROOT  = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PICKS_PATH = os.path.join(REPO_ROOT, 'data', 'picks.json')

# ---- Phase 2 feature inputs ----
SCHEMA_VERSION     = "1.1.0"
RAW_SCHEDULE_PATH  = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                  'raw_schedule_2026.json')


def build_team_history(path=RAW_SCHEDULE_PATH):
    """
    Derive per-team game logs and win% from the committed historical schedule.

    Returns (games_by_team, win_pct, sos_by_team):
      games_by_team: {team_name: [{opponent, date, point_diff, win, result}, ...]}
                     sorted oldest-to-newest (as build_phase2_features expects).
      win_pct:       {team_name: raw win pct}.
      sos_by_team:   {team_name: first-order strength of schedule}.
    If the schedule file is missing/unreadable, returns empty dicts so the
    Phase 2 functions fall back to their own neutral defaults (0.500).
    """
    try:
        with open(path) as f:
            raw = json.load(f)
    except (OSError, ValueError):
        return {}, {}, {}

    games_by_team = defaultdict(list)
    wins, total = defaultdict(int), defaultdict(int)
    for rng in raw.get('ranges', []):
        for day in rng.get('data', {}).get('dates', []):
            for g in day.get('games', []):
                if g.get('gameType') != 'R':
                    continue
                if g.get('status', {}).get('abstractGameState') != 'Final':
                    continue
                home, away = g['teams']['home'], g['teams']['away']
                hn, an = home['team']['name'], away['team']['name']
                hs, as_ = home.get('score'), away.get('score')
                if hs is None or as_ is None:
                    continue
                date = g.get('officialDate') or g.get('gameDate')
                home_win = hs > as_
                games_by_team[hn].append({'opponent': an, 'date': date,
                                          'point_diff': hs - as_, 'win': home_win,
                                          'result': 'W' if home_win else 'L'})
                games_by_team[an].append({'opponent': hn, 'date': date,
                                          'point_diff': as_ - hs, 'win': not home_win,
                                          'result': 'W' if not home_win else 'L'})
                for t, w in ((hn, home_win), (an, not home_win)):
                    total[t] += 1
                    wins[t] += 1 if w else 0

    for t in games_by_team:
        games_by_team[t].sort(key=lambda x: x['date'])
    win_pct = {t: round(wins[t] / total[t], 4) if total[t] else 0.500 for t in total}
    sos_by_team = {t: round(compute_strength_of_schedule(t, games_by_team[t], win_pct), 4)
                   for t in games_by_team}
    return dict(games_by_team), win_pct, sos_by_team


def _ev_bet_to_pick(b):
    """Flatten one +EV moneyline bet into the generic pick/leg shape."""
    game_id = f"{b['away_team']}-at-{b['home_team']}".replace(" ", "_")
    return {
        "game_id":     game_id,
        "event_id":    game_id,
        "league":      "MLB",
        "market":      "moneyline",
        "selection":   " ".join(x for x in (b.get("bet_team"), b.get("bet_side")) if x),
        "line":        None,
        "odds":        b["best_odds"],
        "model_prob":  (b["model_prob"] / 100.0) if b.get("model_prob") is not None else None,
        "edge":        (b["edge_pct"] / 100.0) if b.get("edge_pct") is not None else None,
        "status":      "pending",
    }


def write_picks_json(ev_bets, today_str, state):
    """Write today's +EV picks, bankroll state, and performance stats to data/picks.json.

    Picks are categorised into Singles (moneyline/spread/total) and Combinations
    (parlays/teasers) according to the configured bet-type mode. Each single keeps
    its original display fields plus the Phase 2 feature block; each combination
    carries its constituent legs with independent per-leg status so legs remain
    individually trackable. The top-level schema_version documents the pick shape.
    """
    cfg = bet_type_config()
    generic_picks = [_ev_bet_to_pick(b) for b in ev_bets]
    generated = generate_bets(generic_picks, cfg)

    output = {
        "status":         "ok",
        "schema_version": SCHEMA_VERSION,
        "generated_at":   datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ'),
        "date_display":   today_str,
        "sport":          "MLB",
        "bankroll":       round(state.get("bankroll", BANKROLL), 2),
        "performance":    get_performance_stats(),
        "bet_type_mode":  cfg["mode"],
        "picks": [],
        "combinations": generated["combinations"],
    }

    include_singles = cfg["mode"] in ("individual", "both")
    if include_singles:
        # Phase 2 feature inputs derived from the committed historical schedule.
        games_by_team, win_pct, sos_by_team = build_team_history()

        for b in ev_bets:
            pick = {
                "home_team":    b["home_team"],
                "away_team":    b["away_team"],
                "bet_side":     b["bet_side"],
                "bet_team":     b["bet_team"],
                "bet_category": "single",
                "bet_type":     "moneyline",
                "status":       "pending",
                "edge_pct":     b["edge_pct"],
                "model_prob":   b["model_prob"],
                "market_prob":  b["market_prob"],
                "best_odds":    b["best_odds"],
                "best_book":    b["best_book"],
                "kelly_bet":    b["kelly_bet_$"],
                "home_pitcher": b.get("home_pitcher", ""),
                "away_pitcher": b.get("away_pitcher", ""),
                "venue":        b.get("venue", ""),
                "tier":         "high" if b["edge_pct"] >= 5 else "medium" if b["edge_pct"] >= 3.5 else "low",
            }

            # Phase 2 features are computed for the team being bet on vs its opponent.
            team = b["bet_team"]
            opponent = b["away_team"] if team == b["home_team"] else b["home_team"]
            phase2 = build_phase2_features(
                team=team,
                opponent=opponent,
                team_games=games_by_team.get(team, []),
                opponent_games=games_by_team.get(opponent, []),
                all_team_win_pct=win_pct,
                sos_by_team=sos_by_team,
                model_prob=b["model_prob"] / 100.0,  # model_prob is stored as a percentage
            )
            pick.update(phase2)
            output["picks"].append(pick)

    os.makedirs(os.path.dirname(PICKS_PATH), exist_ok=True)
    with open(PICKS_PATH, "w") as f:
        json.dump(output, f, indent=2)
    print(f"picks.json written: {len(output['picks'])} singles, "
          f"{len(output['combinations'])} combinations (mode={cfg['mode']}) -> {PICKS_PATH}")


def write_no_data(reason):
    """Write a no_data picks.json so the frontend/deploy still has a valid file."""
    os.makedirs(os.path.dirname(PICKS_PATH), exist_ok=True)
    with open(PICKS_PATH, "w") as f:
        json.dump({
            "status": "no_data",
            "reason": str(reason)[:200],
            "generated_at": datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ'),
        }, f, indent=2)
    print(f"no_data picks.json written ({reason}) -> {PICKS_PATH}")


def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, 'rb') as f:
            return pickle.load(f)
    return {
        'team_stats': {},        # team_id -> list of (win, rs, ra)
        'pitcher_app_log': {},   # pitcher_name -> list of {date, ra, ip}
        'team_game_log': {},     # team_id -> list of {date, rs, ra, sp}
        'team_last_game': {},    # team_id -> last game date
        'pitcher_last_start': {},# pitcher_name -> last start date
        'series_tracker': {},    # matchup_key -> {last_date, game_num}
        'team_season_rs': {},    # team_id -> season avg RS
        'elo_ratings': {},       # team_id -> elo rating
        'bankroll': BANKROLL,
    }


def save_state(state):
    with open(STATE_FILE, 'wb') as f:
        pickle.dump(state, f)


def load_pitcher_cache():
    if os.path.exists(PITCHER_CACHE):
        with open(PITCHER_CACHE, 'r') as f:
            return json.load(f)
    return {}


def save_pitcher_cache(cache):
    with open(PITCHER_CACHE, 'w') as f:
        json.dump(cache, f)


def update_state_with_results(state, yesterday_games):
    """Process yesterday's completed games into rolling state."""
    elo = EloTracker(k=ELO_K, hfa=ELO_HFA)
    elo.ratings = state['elo_ratings']

    for g in yesterday_games:
        hid, aid = g['home_id'], g['away_id']
        d = pd.Timestamp(g['date'])

        # Team rolling stats
        for tid, win, sc, al, sp in [
            (hid, g['home_win'], g['home_score'], g['away_score'], g['home_pitcher']),
            (aid, 1-g['home_win'], g['away_score'], g['home_score'], g['away_pitcher']),
        ]:
            if tid not in state['team_stats']: state['team_stats'][tid] = []
            state['team_stats'][tid].append((win, sc, al))
            if tid not in state['team_game_log']: state['team_game_log'][tid] = []
            state['team_game_log'][tid].append({'date':d,'rs':sc,'ra':al,'sp':sp})
            state['team_last_game'][tid] = d

        # Pitcher appearance log
        for pitcher, ra in [(g['home_pitcher'], g['away_score']),
                             (g['away_pitcher'], g['home_score'])]:
            if pitcher:
                if pitcher not in state['pitcher_app_log']: state['pitcher_app_log'][pitcher] = []
                state['pitcher_app_log'][pitcher].append({'date':d,'ra':ra,'ip':6})
                state['pitcher_last_start'][pitcher] = d

        # Elo update
        elo.update(hid, aid, g['home_win'])

    state['elo_ratings'] = elo.ratings
    return state


def fetch_today_games(today_str):
    """Fetch today's schedule with probable pitchers."""
    try:
        schedule = statsapi.schedule(start_date=today_str, end_date=today_str)
        return [g for g in schedule if g['game_type'] == 'R']
    except Exception as e:
        print(f"Schedule fetch error: {e}")
        return []


def fetch_yesterday_results(yesterday_str):
    """Fetch completed games from yesterday."""
    try:
        schedule = statsapi.schedule(start_date=yesterday_str, end_date=yesterday_str)
        results = []
        for g in schedule:
            if g['status'] == 'Final' and g['game_type'] == 'R':
                results.append({
                    'date': g['game_date'],
                    'home_team': g['home_name'], 'away_team': g['away_name'],
                    'home_id': g['home_id'], 'away_id': g['away_id'],
                    'home_score': int(g['home_score']), 'away_score': int(g['away_score']),
                    'home_win': int(g['home_score'] > g['away_score']),
                    'home_pitcher': g['home_probable_pitcher'],
                    'away_pitcher': g['away_probable_pitcher'],
                    'venue': g['venue_name'],
                })
        return results
    except Exception as e:
        print(f"Yesterday results fetch error: {e}")
        return []


def get_pitcher_stats_cached(pitcher_name, cache, season=2026):
    """Fetch pitcher stats, using cache to avoid redundant API calls."""
    if pitcher_name in cache:
        return cache[pitcher_name]
    stats = fetch_pitcher_stats(pitcher_name, season=season)
    result = stats if stats else LEAGUE_AVG.copy()
    cache[pitcher_name] = result
    time.sleep(0.05)
    return result


def should_retrain():
    """Retrain weekly."""
    if not os.path.exists(RETRAIN_TRACKER):
        return True
    with open(RETRAIN_TRACKER) as f:
        last = f.read().strip()
    try:
        last_date = datetime.strptime(last, '%Y-%m-%d')
        return (datetime.now() - last_date).days >= 7
    except:
        return True


def mark_retrained():
    with open(RETRAIN_TRACKER, 'w') as f:
        f.write(datetime.now().strftime('%Y-%m-%d'))


def run_pipeline():
    """Run the full daily pipeline. Returns (ev_bets, today_str, state)."""
    today     = datetime.now()
    yesterday = today - timedelta(days=1)
    today_str     = today.strftime('%Y-%m-%d')
    yesterday_str = yesterday.strftime('%Y-%m-%d')

    print(f"\n===== MLB Betting Model — {today_str} =====\n")

    # 1. Load state
    state = load_state()
    pitcher_cache = load_pitcher_cache()

    # 2. Process yesterday's results
    print("Fetching yesterday's results...")
    yesterday_games = fetch_yesterday_results(yesterday_str)
    if yesterday_games:
        state = update_state_with_results(state, yesterday_games)
        # Update P&L in bet tracker
        update_results([{
            'home_team': g['home_team'],
            'away_team': g['away_team'],
            'home_win':  g['home_win'],
        } for g in yesterday_games])
        # Update season RS averages
        for tid in state['team_stats']:
            rs_vals = [x[1] for x in state['team_stats'][tid]]
            state['team_season_rs'][tid] = np.mean(rs_vals) if rs_vals else 4.5
        print(f"  Processed {len(yesterday_games)} games from {yesterday_str}")
    else:
        print(f"  No completed games found for {yesterday_str}")

    # 3. Retrain model if due
    if should_retrain() and os.path.exists(HISTORY_FILE):
        print("Weekly retrain triggered...")
        hist = pd.read_csv(HISTORY_FILE)
        train_models(hist)
        mark_retrained()

    # 4. Load models
    model_state = load_models()
    elo = EloTracker(k=ELO_K, hfa=ELO_HFA)
    elo.ratings = state['elo_ratings']

    # 5. Fetch today's schedule
    print("Fetching today's schedule...")
    today_games = fetch_today_games(today_str)
    print(f"  {len(today_games)} games today")
    if not today_games:
        print("No games scheduled today. Exiting.")
        save_state(state)
        return [], today_str, state

    # 6. Build features for today's games
    predictions = []
    for g in today_games:
        hp_name = g.get('home_probable_pitcher', '')
        ap_name = g.get('away_probable_pitcher', '')

        # Fetch pitcher stats (cached)
        pitcher_stats_today = {}
        for name in [hp_name, ap_name]:
            if name:
                pitcher_stats_today[name] = get_pitcher_stats_cached(name, pitcher_cache)

        game_dict = {
            'home_id': g['home_id'], 'away_id': g['away_id'],
            'home_pitcher': hp_name, 'away_pitcher': ap_name,
            'venue': g['venue_name'], 'date': pd.Timestamp(today_str),
        }

        feats = build_feature_vector(
            game_dict,
            state['team_stats'],
            pitcher_stats_today,
            state['pitcher_app_log'],
            state['team_game_log'],
            state['series_tracker'],
            state['team_last_game'],
            state['pitcher_last_start'],
            state['team_season_rs'],
        )

        # Elo prediction
        p_elo = elo.predict(g['home_id'], g['away_id'])

        # Ensemble prediction when a trained artifact exists AND we have enough
        # history; otherwise fall back to a per-game heuristic baseline.
        #
        # ROOT-CAUSE NOTE: the old fallback here was `p_final = p_elo`. On a cold
        # start there is no model_state.pkl and no Elo history, so every team is
        # rated 1500 and elo.predict() returns the SAME home-field-advantage-only
        # value (~0.599) for every game -> that produced the "every pick 59.9% /
        # HOME" bug. heuristic_probability() mixes in real per-game features so
        # predictions vary by matchup even before a real model is trained.
        if model_state and feats.get('home_games', 0) >= MIN_PRIOR_GAMES:
            p_ens = float(predict_proba_ensemble([feats], model_state)[0])
            p_final = combined_probability(p_ens, p_elo)
        else:
            p_final = heuristic_probability(feats, p_elo)

        predictions.append({
            'home_team':    g['home_name'],
            'away_team':    g['away_name'],
            'home_pitcher': hp_name,
            'away_pitcher': ap_name,
            'venue':        g['venue_name'],
            'p_home':       p_final,
            'p_elo':        p_elo,
            'features':     feats,
        })

    # 7. Fetch live odds + find +EV bets
    print("Fetching live odds...")
    live_odds = fetch_live_odds()
    if not live_odds:
        print("  Warning: Could not fetch odds. Sending predictions only.")

    ev_bets = find_ev_bets(predictions, live_odds, bankroll=state['bankroll'])
    print(f"  +EV bets found: {len(ev_bets)}")

    # 8. Send alert
    send_alert(ev_bets, today_str, state['bankroll'], method='email')

    # 9. Log bets
    if ev_bets:
        log_bets(ev_bets, today_str)

    # 10. Print performance summary
    print_summary()

    # 11. Save updated state + pitcher cache
    save_state(state)
    save_pitcher_cache(pitcher_cache)
    print(f"\nDone. State saved. Next run tomorrow at scheduled time.")

    return ev_bets, today_str, state


def main():
    """
    Resilient entry point. Always writes data/picks.json and exits 0.
    If ODDS_API_KEY is missing or any API call fails, writes a no_data JSON.
    """
    if not os.environ.get('ODDS_API_KEY'):
        write_no_data("ODDS_API_KEY not set")
        return

    try:
        ev_bets, today_str, state = run_pipeline()
        write_picks_json(ev_bets, today_str, state)
    except Exception as e:
        print(f"Pipeline error: {e}")
        write_no_data(f"pipeline error: {e}")


if __name__ == '__main__':
    main()
