"""
train_models.py — Bulk-fetch the 2026 MLB schedule/results and train a real
ensemble win-probability model (logistic regression + gradient boosting +
random forest) on team-form/situational features only.

EFFICIENCY DESIGN (see model/BACKTEST_RESULTS.md):
  * The entire season schedule + final scores is fetched with ONE HTTP call per
    month (~5 calls total) to the MLB Stats API `schedule` endpoint. The raw
    combined response is saved to model/raw_schedule_2026.json.
  * NO per-game API calls are made. Every feature is derived in-memory from the
    bulk schedule data (team names/ids, scores, dates, venues) plus simple
    trailing-window rolling computations.

Usage:
  python model/train_models.py --fetch-only   # just fetch + save raw JSON
  python model/train_models.py                 # fetch (if needed) + train
"""

import os
import sys
import json
import pickle
import argparse
from datetime import datetime

import numpy as np
import pandas as pd
import requests

from sklearn.linear_model import LogisticRegression
from sklearn.ensemble import RandomForestClassifier, GradientBoostingClassifier
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import accuracy_score, log_loss, brier_score_loss

# Reuse the exact park-factor table + Elo settings production uses so training
# and inference see the same feature transforms.
from features import get_park_factor
from config import ELO_K, ELO_HFA

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)

RAW_SCHEDULE_PATH = os.path.join(HERE, 'raw_schedule_2026.json')
TRAINING_CSV_PATH = os.path.join(HERE, 'training_data.csv')

# Artifacts live at the repo root: the GitHub Action runs `python
# model/daily_runner.py` from the repo root, so model.load_models() /
# daily_runner state files resolve their relative paths there.
MODEL_STATE_PATH = os.path.join(REPO_ROOT, 'model_state.pkl')
ELO_STATE_PATH   = os.path.join(REPO_ROOT, 'elo_state.pkl')
DAILY_STATE_PATH = os.path.join(REPO_ROOT, 'daily_state.pkl')

# Team-form / situational features only. These are a strict subset of what
# model.features.build_feature_vector() produces at inference time, so the
# trained artifact drops straight into the production predict path.
FEATURE_COLS = [
    'home_win_pct', 'away_win_pct', 'win_pct_diff',
    'home_avg_rs', 'away_avg_rs', 'home_avg_ra', 'away_avg_ra', 'run_diff_diff',
    'park_factor', 'rest_diff',
]

ROLL_N = 15           # trailing window (matches config.LOOKBACK_GAMES)
MIN_HISTORY = 15      # skip a game unless BOTH teams have >= this many prior games

# Fetch a call per month, March through a cutoff a few days before 2026-07-17.
MONTH_RANGES = [
    ('2026-03-01', '2026-03-31'),
    ('2026-04-01', '2026-04-30'),
    ('2026-05-01', '2026-05-31'),
    ('2026-06-01', '2026-06-30'),
    ('2026-07-01', '2026-07-13'),
]

SCHEDULE_URL = 'https://statsapi.mlb.com/api/v1/schedule'


def fetch_bulk_schedule():
    """Fetch the season schedule+scores with one HTTP call per month range."""
    combined = {'fetched_at': datetime.utcnow().isoformat() + 'Z',
                'source': SCHEDULE_URL, 'ranges': []}
    n_calls = 0
    for start, end in MONTH_RANGES:
        params = {'sportId': 1, 'startDate': start, 'endDate': end, 'gameType': 'R'}
        resp = requests.get(SCHEDULE_URL, params=params, timeout=60)
        resp.raise_for_status()
        n_calls += 1
        combined['ranges'].append({'start': start, 'end': end, 'data': resp.json()})
        dates = combined['ranges'][-1]['data'].get('dates', [])
        n_games = sum(len(d.get('games', [])) for d in dates)
        print(f"  fetched {start}..{end}: {n_games} games (call #{n_calls})")
    combined['n_api_calls'] = n_calls
    with open(RAW_SCHEDULE_PATH, 'w') as f:
        json.dump(combined, f)
    print(f"Saved raw schedule ({n_calls} API calls) -> {RAW_SCHEDULE_PATH}")
    return combined


def load_raw_schedule():
    with open(RAW_SCHEDULE_PATH) as f:
        return json.load(f)


def parse_games(raw):
    """Flatten the bulk response into a chronological list of completed games."""
    games = []
    for rng in raw['ranges']:
        for day in rng['data'].get('dates', []):
            for g in day.get('games', []):
                if g.get('gameType') != 'R':
                    continue
                status = g.get('status', {})
                # Only fully completed games with a decision.
                if status.get('codedGameState') != 'F' and \
                   status.get('detailedState') != 'Final':
                    continue
                home = g['teams']['home']
                away = g['teams']['away']
                hs, as_ = home.get('score'), away.get('score')
                if hs is None or as_ is None or hs == as_:
                    continue  # skip ties / missing scores
                games.append({
                    'game_pk': g.get('gamePk'),
                    'date': pd.Timestamp(g['gameDate']).tz_convert(None).normalize()
                            if pd.Timestamp(g['gameDate']).tzinfo
                            else pd.Timestamp(g['gameDate']).normalize(),
                    'home_id': home['team']['id'], 'away_id': away['team']['id'],
                    'home_team': home['team']['name'], 'away_team': away['team']['name'],
                    'home_score': int(hs), 'away_score': int(as_),
                    'home_win': int(hs > as_),
                    'venue': g.get('venue', {}).get('name', ''),
                })
    games.sort(key=lambda x: (x['date'], x['game_pk'] or 0))
    return games


def rolling(team_hist, n=ROLL_N):
    """Trailing win%, avg RS, avg RA over last n games. Mirrors
    features.get_rolling_team_stats (defaults when no history)."""
    if not team_hist:
        return 0.5, 4.5, 4.5, 0
    r = team_hist[-n:]
    return (
        sum(x[0] for x in r) / len(r),
        float(np.mean([x[1] for x in r])),
        float(np.mean([x[2] for x in r])),
        len(r),
    )


def build_dataset(games):
    """Walk games chronologically, emitting a feature row from PRIOR history
    only (no leakage), then folding the result into the running state."""
    team_hist = {}       # team_id -> list of (win, rs, ra)
    team_count = {}      # team_id -> true count of prior games
    team_last_game = {}  # team_id -> last game date

    rows = []
    for g in games:
        hid, aid, d = g['home_id'], g['away_id'], g['date']
        hwp, hrs, hra, _ = rolling(team_hist.get(hid, []))
        awp, ars, ara, _ = rolling(team_hist.get(aid, []))
        h_count = team_count.get(hid, 0)
        a_count = team_count.get(aid, 0)

        rest_h = min((d - team_last_game[hid]).days, 5) if hid in team_last_game else 3
        rest_a = min((d - team_last_game[aid]).days, 5) if aid in team_last_game else 3

        rows.append({
            'date': d.strftime('%Y-%m-%d'),
            'home_team': g['home_team'], 'away_team': g['away_team'],
            'home_win_pct': hwp, 'away_win_pct': awp, 'win_pct_diff': hwp - awp,
            'home_avg_rs': hrs, 'away_avg_rs': ars,
            'home_avg_ra': hra, 'away_avg_ra': ara,
            'run_diff_diff': (hrs - hra) - (ars - ara),
            'park_factor': get_park_factor(g['venue']),
            'rest_diff': rest_h - rest_a,
            'home_games': h_count, 'away_games': a_count,
            'home_win': g['home_win'],
        })

        # Fold result into running state (AFTER building the row).
        for tid, win, rs, ra in [(hid, g['home_win'], g['home_score'], g['away_score']),
                                  (aid, 1 - g['home_win'], g['away_score'], g['home_score'])]:
            team_hist.setdefault(tid, []).append((win, rs, ra))
            team_count[tid] = team_count.get(tid, 0) + 1
            team_last_game[tid] = d

    return pd.DataFrame(rows)


def compute_elo_and_state(games):
    """Run Elo over the full game sequence and assemble a warm daily_state so
    production starts informed instead of every team at 1500."""
    ratings = {}
    team_stats = {}       # team_id -> list of (win, rs, ra)
    team_game_log = {}    # team_id -> list of {date, rs, ra, sp}
    team_last_game = {}   # team_id -> last date
    team_season_rs = {}   # team_id -> season avg RS

    def get(tid):
        return ratings.get(tid, 1500)

    for g in games:
        hid, aid, hw = g['home_id'], g['away_id'], g['home_win']
        rh = get(hid) + ELO_HFA
        ra = get(aid)
        p = 1 / (1 + 10 ** ((ra - rh) / 400))
        ratings[hid] = get(hid) + ELO_K * (hw - p)
        ratings[aid] = get(aid) + ELO_K * ((1 - hw) - (1 - p))

        d = g['date']
        for tid, win, rs, al in [(hid, hw, g['home_score'], g['away_score']),
                                 (aid, 1 - hw, g['away_score'], g['home_score'])]:
            team_stats.setdefault(tid, []).append((win, rs, al))
            team_game_log.setdefault(tid, []).append(
                {'date': d, 'rs': rs, 'ra': al, 'sp': ''})
            team_last_game[tid] = d

    for tid, s in team_stats.items():
        team_season_rs[tid] = float(np.mean([x[1] for x in s])) if s else 4.5

    daily_state = {
        'team_stats': team_stats,
        'pitcher_app_log': {},
        'team_game_log': team_game_log,
        'team_last_game': team_last_game,
        'pitcher_last_start': {},
        'series_tracker': {},
        'team_season_rs': team_season_rs,
        'elo_ratings': ratings,
        'bankroll': 1000.0,
    }
    return ratings, daily_state


def calibration_table(y_true, y_prob, n_bins=10):
    edges = np.linspace(0, 1, n_bins + 1)
    rows = []
    for i in range(n_bins):
        lo, hi = edges[i], edges[i + 1]
        mask = (y_prob >= lo) & (y_prob < hi) if i < n_bins - 1 else \
               (y_prob >= lo) & (y_prob <= hi)
        cnt = int(mask.sum())
        if cnt == 0:
            rows.append((lo, hi, 0, None, None))
        else:
            rows.append((lo, hi, cnt, float(y_prob[mask].mean()),
                         float(y_true[mask].mean())))
    return rows


def train(df):
    """Time-split train/test, fit the ensemble, report held-out metrics."""
    df = df[(df['home_games'] >= MIN_HISTORY) &
            (df['away_games'] >= MIN_HISTORY)].copy()
    df['date'] = pd.to_datetime(df['date'])
    df = df.sort_values('date').reset_index(drop=True)

    n = len(df)
    split = int(n * 0.8)
    train_df, test_df = df.iloc[:split], df.iloc[split:]

    X_tr, y_tr = train_df[FEATURE_COLS].values, train_df['home_win'].values
    X_te, y_te = test_df[FEATURE_COLS].values, test_df['home_win'].values

    scaler = StandardScaler().fit(X_tr)
    Xtr_s, Xte_s = scaler.transform(X_tr), scaler.transform(X_te)

    lr = LogisticRegression(max_iter=1000, random_state=42).fit(Xtr_s, y_tr)
    gb = GradientBoostingClassifier(n_estimators=100, max_depth=3,
                                    learning_rate=0.1, random_state=42).fit(X_tr, y_tr)
    rf = RandomForestClassifier(n_estimators=300, max_depth=6,
                                random_state=42).fit(X_tr, y_tr)

    # Blend = simple average of the three calibrated probabilities.
    p_te = (lr.predict_proba(Xte_s)[:, 1] +
            gb.predict_proba(X_te)[:, 1] +
            rf.predict_proba(X_te)[:, 1]) / 3.0

    metrics = {
        'n_total': n, 'n_train': len(train_df), 'n_test': len(test_df),
        'train_start': train_df['date'].iloc[0], 'train_end': train_df['date'].iloc[-1],
        'test_start': test_df['date'].iloc[0], 'test_end': test_df['date'].iloc[-1],
        'accuracy': accuracy_score(y_te, (p_te >= 0.5).astype(int)),
        'log_loss': log_loss(y_te, p_te),
        'brier': brier_score_loss(y_te, p_te),
        'base_rate_home_win': float(y_te.mean()),
        'calibration': calibration_table(y_te, p_te),
    }

    state = {'lr': lr, 'gb': gb, 'rf': rf, 'scaler': scaler,
             'feature_cols': FEATURE_COLS, 'n_train': len(train_df)}
    return state, metrics


def write_backtest_md(metrics, n_games_parsed, n_api_calls):
    cal = metrics['calibration']
    lines = []
    lines.append("# Backtest Results — Real Ensemble (team-form features)\n")
    lines.append("## Model\n")
    lines.append("Ensemble of **logistic regression + gradient boosting + random "
                 "forest**, blended by simple average of the three predicted "
                 "P(home win) probabilities. The logistic-regression input is "
                 "standardized; the tree models use raw features.\n")
    lines.append("## Dataset\n")
    lines.append(f"- Source: MLB Stats API `schedule` endpoint (regular season, 2026).")
    lines.append(f"- Completed games parsed from bulk fetch: **{n_games_parsed}**")
    lines.append(f"- Modeling rows after requiring >= {MIN_HISTORY} prior games for "
                 f"both teams: **{metrics['n_total']}**")
    lines.append(f"- Full date range used: "
                 f"**{metrics['train_start'].date()} .. {metrics['test_end'].date()}**")
    lines.append(f"- Total external HTTP/API calls for the entire task: "
                 f"**{n_api_calls}** (one bulk `schedule` call per month; zero "
                 f"per-game calls).\n")
    lines.append("## Time-based split (chronological, never random)\n")
    lines.append(f"- Train: **{metrics['n_train']}** games, "
                 f"{metrics['train_start'].date()} .. {metrics['train_end'].date()} "
                 f"(earliest ~80%)")
    lines.append(f"- Held-out test: **{metrics['n_test']}** games, "
                 f"{metrics['test_start'].date()} .. {metrics['test_end'].date()} "
                 f"(most recent ~20%)\n")
    lines.append("## Held-out metrics\n")
    lines.append(f"- Accuracy: **{metrics['accuracy']:.4f}**")
    lines.append(f"- Log loss: **{metrics['log_loss']:.4f}**")
    lines.append(f"- Brier score: **{metrics['brier']:.4f}**")
    lines.append(f"- Test-set home-win base rate: {metrics['base_rate_home_win']:.4f} "
                 f"(a naive always-home baseline would score this accuracy)\n")
    lines.append("## Calibration table (held-out)\n")
    lines.append("| Predicted bucket | N | Mean predicted | Actual home-win rate |")
    lines.append("|---|---|---|---|")
    for lo, hi, cnt, mp, act in cal:
        if cnt == 0:
            lines.append(f"| {lo:.1f}–{hi:.1f} | 0 | — | — |")
        else:
            lines.append(f"| {lo:.1f}–{hi:.1f} | {cnt} | {mp:.3f} | {act:.3f} |")
    lines.append("")
    lines.append("## Honest limitations\n")
    lines.append("**Team-form features only.** This version deliberately trains on "
                 "team-form / situational features derived entirely from the bulk "
                 "schedule + final scores: trailing-15-game win%, average runs "
                 "scored/allowed, run-differential differential, home/away park "
                 "factor, and rest-day differential. It does **not** include "
                 "starting-pitcher ERA/FIP/WHIP or bullpen ERA. Those require a "
                 "separate MLB Stats API call per game per pitcher, which is exactly "
                 "what caused a prior attempt at this task to time out after ~3 "
                 "hours with zero commits. Pitcher-level detail is a documented "
                 "future improvement, not part of this artifact.\n")
    lines.append("## Why no historical ROI is reported\n")
    lines.append("No historical closing-odds archive is available, so there is no "
                 "honest way to compute a historical betting ROI: ROI requires the "
                 "market price you would actually have gotten at bet time, and "
                 "fabricating or back-filling those odds would produce a misleading "
                 "number. This deliverable therefore reports only genuine held-out "
                 "**classifier** metrics on real game outcomes. Real ROI and CLV "
                 "will accrue **going forward**: `model/tracker.py` logs each live "
                 "pick with the odds actually taken (`log_bets`), settles it against "
                 "the real result (`update_results`), and reports cumulative "
                 "ROI/CLV (`get_performance_stats`) into `data/picks.json` on every "
                 "daily run.\n")
    with open(os.path.join(HERE, 'BACKTEST_RESULTS.md'), 'w') as f:
        f.write('\n'.join(lines))
    print("Wrote model/BACKTEST_RESULTS.md")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--fetch-only', action='store_true')
    args = ap.parse_args()

    if not os.path.exists(RAW_SCHEDULE_PATH):
        raw = fetch_bulk_schedule()
    else:
        print(f"Using existing {RAW_SCHEDULE_PATH}")
        raw = load_raw_schedule()

    if args.fetch_only:
        return

    n_api_calls = raw.get('n_api_calls', len(MONTH_RANGES))
    games = parse_games(raw)
    print(f"Parsed {len(games)} completed regular-season games.")

    df = build_dataset(games)
    df.to_csv(TRAINING_CSV_PATH, index=False)
    print(f"Wrote {TRAINING_CSV_PATH} ({len(df)} rows)")

    state, metrics = train(df)
    with open(MODEL_STATE_PATH, 'wb') as f:
        pickle.dump(state, f)
    print(f"Saved model artifact -> {MODEL_STATE_PATH}")

    ratings, daily_state = compute_elo_and_state(games)
    with open(ELO_STATE_PATH, 'wb') as f:
        pickle.dump(ratings, f)
    with open(DAILY_STATE_PATH, 'wb') as f:
        pickle.dump(daily_state, f)
    print(f"Saved Elo state -> {ELO_STATE_PATH} and warm daily state -> {DAILY_STATE_PATH}")

    write_backtest_md(metrics, len(games), n_api_calls)

    print("\n===== HELD-OUT METRICS =====")
    print(f"n_total={metrics['n_total']} train={metrics['n_train']} test={metrics['n_test']}")
    print(f"date range {metrics['train_start'].date()}..{metrics['test_end'].date()}")
    print(f"accuracy={metrics['accuracy']:.4f} log_loss={metrics['log_loss']:.4f} "
          f"brier={metrics['brier']:.4f}")


if __name__ == '__main__':
    main()
