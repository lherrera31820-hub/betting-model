"""
Phase 2 Feature Enhancements — Strength of Schedule, Common Opponents,
Recency-Weighted Form, Second-Order SOS
Merge into existing features.py
"""

from datetime import datetime, timedelta
from collections import defaultdict
import math


def compute_strength_of_schedule(team, games, all_team_records):
    """
    First-order SOS: average win% of opponents faced.
    games: list of dicts {opponent, date, result}
    all_team_records: dict {team: win_pct}
    """
    opponents = [g["opponent"] for g in games if g.get("opponent") in all_team_records]
    if not opponents:
        return 0.500
    return sum(all_team_records[o] for o in opponents) / len(opponents)


def compute_second_order_sos(team, games, sos_by_team):
    """
    Second-order SOS: average SOS of opponents faced (RPI-style).
    """
    opponents = [g["opponent"] for g in games if g.get("opponent") in sos_by_team]
    if not opponents:
        return 0.500
    return sum(sos_by_team[o] for o in opponents) / len(opponents)


def compute_win_pct_adjusted(win_pct_raw, sos):
    """
    Adjusted win% = raw win% + (SOS - 0.500)
    """
    return round(win_pct_raw + (sos - 0.500), 4)


def compute_common_opponents_score(team_a, team_b, games_a, games_b):
    """
    Compare performance of team_a and team_b against shared opponents.
    Returns a score from 0 to 1 representing team_a's relative edge.
    games_a / games_b: list of dicts {opponent, point_diff, win}
    """
    opp_a = {g["opponent"]: g for g in games_a}
    opp_b = {g["opponent"]: g for g in games_b}
    shared = set(opp_a.keys()) & set(opp_b.keys())

    if not shared:
        return 0.5  # no data, neutral

    a_score = 0
    b_score = 0
    for opp in shared:
        a_score += 1 if opp_a[opp]["win"] else 0
        a_score += opp_a[opp].get("point_diff", 0) / 100  # small margin weight
        b_score += 1 if opp_b[opp]["win"] else 0
        b_score += opp_b[opp].get("point_diff", 0) / 100

    total = a_score + b_score
    if total == 0:
        return 0.5
    return round(a_score / total, 4)


def compute_recent_form_weighted(games, num_games=10, decay=0.85):
    """
    Recency-weighted win rate. Most recent games weighted higher via exponential decay.
    games: list of dicts sorted oldest-to-newest {win: bool}
    """
    recent = games[-num_games:] if len(games) > num_games else games
    if not recent:
        return 0.500

    weights = [decay ** (len(recent) - 1 - i) for i in range(len(recent))]
    weighted_wins = sum(w for w, g in zip(weights, recent) if g["win"])
    total_weight = sum(weights)
    return round(weighted_wins / total_weight, 4) if total_weight else 0.500


def blend_final_probability(model_prob, win_pct_adjusted, common_opp_score,
                             recent_form, weights=None):
    """
    Blend model probability with SOS-adjusted win%, common opponents score,
    and recency-weighted form into one final probability.
    """
    if weights is None:
        weights = {"model": 0.5, "sos_adj": 0.2, "common_opp": 0.15, "recent_form": 0.15}

    final = (
        weights["model"] * model_prob
        + weights["sos_adj"] * win_pct_adjusted
        + weights["common_opp"] * common_opp_score
        + weights["recent_form"] * recent_form
    )
    return round(min(max(final, 0.01), 0.99), 4)


def build_phase2_features(team, opponent, team_games, opponent_games,
                           all_team_win_pct, sos_by_team, model_prob):
    """
    Master function — builds the full Phase 2 feature block for one matchup.
    Returns a dict matching the optional schema fields in picks schema v1.1.0.
    """
    sos_raw = compute_strength_of_schedule(team, team_games, all_team_win_pct)
    sos_second_order = compute_second_order_sos(team, team_games, sos_by_team)
    win_pct_raw = all_team_win_pct.get(team, 0.500)
    win_pct_adjusted = compute_win_pct_adjusted(win_pct_raw, sos_raw)
    common_opp_score = compute_common_opponents_score(team, opponent, team_games, opponent_games)
    recent_form = compute_recent_form_weighted(team_games)

    final_prob = blend_final_probability(
        model_prob=model_prob,
        win_pct_adjusted=win_pct_adjusted,
        common_opp_score=common_opp_score,
        recent_form=recent_form,
    )

    return {
        "sos_raw": round(sos_raw, 4),
        "sos_second_order": round(sos_second_order, 4),
        "win_pct_adjusted": win_pct_adjusted,
        "common_opponents_score": common_opp_score,
        "recent_form_weighted": recent_form,
        "model_prob_final": final_prob,
    }
