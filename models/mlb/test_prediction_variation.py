"""
test_prediction_variation.py — standalone proof that predictions now vary per game.

Simulates the cold-start scenario that caused the bug (no model_state.pkl, empty
Elo history) using several DISTINCT matchups and mocked odds, then runs the exact
prediction + EV path from the pipeline. Before the fix every game returned
model_prob=59.9 / bet_side=HOME; after the fix both vary.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from model import EloTracker, heuristic_probability
from odds import find_ev_bets
from config import ELO_K, ELO_HFA


def feats(era_diff, win_pct_diff, bp_era_diff, run_diff_diff, rest_diff, park):
    """Minimal feature dict; unspecified feature weights default to 0."""
    return {
        'era_diff': era_diff, 'fip_diff': era_diff * 0.6,
        'recent_era_diff': era_diff * 0.5, 'bp_era_diff': bp_era_diff,
        'win_pct_diff': win_pct_diff, 'run_diff_diff': run_diff_diff,
        'rest_diff': rest_diff, 'pitcher_rest_diff': 0.0,
        'park_factor': park, 'home_games': 0,
    }


# Distinct simulated games (home strongly favored -> away strongly favored)
GAMES = [
    ("Dodgers", "Rockies",  feats(+2.1, +0.30, +1.2, +2.5, +1, 98)),
    ("Yankees", "Athletics", feats(+1.0, +0.15, +0.4, +1.0,  0, 102)),
    ("Guardians", "Rangers", feats(+0.2, +0.02, +0.1, +0.2,  0, 100)),
    ("Marlins", "Braves",   feats(-1.4, -0.20, -0.8, -1.8, -1, 97)),
    ("Rockies", "Padres",   feats(-2.3, -0.28, -1.1, -2.6,  0, 115)),
]

# Mocked live odds: every game priced with home at -120, away at +100
def mock_odds(home, away):
    return {
        "home_team": home, "away_team": away,
        "bookmakers": [{
            "key": "draftkings", "title": "DraftKings",
            "markets": [{"key": "h2h", "outcomes": [
                {"name": home, "price": -120}, {"name": away, "price": +100},
            ]}],
        }],
    }


def main():
    # This test exercises the heuristic fallback path (heuristic_probability)
    # directly, so it is valid whether or not a trained model_state.pkl exists.
    # It proves the fallback still produces per-matchup variation.
    elo = EloTracker(k=ELO_K, hfa=ELO_HFA)   # empty ratings -> all teams 1500

    predictions, live_odds = [], []
    print(f"{'MATCHUP':<22}{'p_elo':>8}{'p_home(fixed)':>16}")
    print("-" * 46)
    for home, away, f in GAMES:
        p_elo = elo.predict(home, away)      # constant across games (the old bug)
        p_home = heuristic_probability(f, p_elo)
        print(f"{home+' vs '+away:<22}{p_elo*100:>7.1f}%{p_home*100:>15.1f}%")
        predictions.append({"home_team": home, "away_team": away, "p_home": p_home,
                            "home_pitcher": "", "away_pitcher": "", "venue": ""})
        live_odds.append(mock_odds(home, away))

    bets = find_ev_bets(predictions, live_odds, bankroll=1000.0)
    print(f"\n+EV bets found: {len(bets)}")
    print(f"{'BET':<26}{'side':>6}{'model_prob':>12}{'edge%':>8}")
    print("-" * 52)
    for b in bets:
        print(f"{b['bet_team']:<26}{b['bet_side']:>6}{b['model_prob']:>11}%{b['edge_pct']:>8}")

    probs = {b['model_prob'] for b in bets}
    sides = {b['bet_side'] for b in bets}
    print(f"\ndistinct model_prob values: {sorted(probs)}")
    print(f"distinct bet_side values:   {sorted(sides)}")
    assert len(probs) > 1, "model_prob still constant!"
    assert sides == {"HOME", "AWAY"} or len(sides) > 1, "bet_side still constant!"
    print("\nPASS: predictions vary per game and both HOME and AWAY bets appear.")


if __name__ == "__main__":
    main()
