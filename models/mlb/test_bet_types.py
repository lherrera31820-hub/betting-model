"""
test_bet_types.py — standalone proof of the bet-type generator + settlement.

Covers:
  1. config parsing for the BET_TYPES / COMBO_EDGE_THRESHOLD settings
  2. generator output: correct Singles vs Combinations under an edge threshold
  3. individual-leg trackability inside a combination (independent status)
  4. dashboard categorisation output (every bet carries bet_category + bet_type)

Plain asserts, no pytest — run with `python model/test_bet_types.py`.
"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from bet_types import (
    generate_bets, generate_combinations, make_single, select_eligible_legs,
    settle_combination, update_leg_status, build_parlay,
    american_to_decimal, decimal_to_american, WON, LOST, PENDING, PUSH,
)


def sample_picks():
    """Three moneyline + two point-market picks with mixed edges."""
    return [
        {"game_id": "g1", "league": "MLB", "market": "moneyline",
         "selection": "Dodgers", "line": None, "odds": -120,
         "model_prob": 0.62, "edge": 0.08},
        {"game_id": "g2", "league": "MLB", "market": "moneyline",
         "selection": "Yankees", "line": None, "odds": 110,
         "model_prob": 0.55, "edge": 0.06},
        {"game_id": "g3", "league": "NFL", "market": "spread",
         "selection": "Chiefs -3", "line": -3.0, "odds": -110,
         "model_prob": 0.57, "edge": 0.055},
        {"game_id": "g4", "league": "NFL", "market": "total",
         "selection": "Over 44.5", "line": 44.5, "odds": -105,
         "model_prob": 0.56, "edge": 0.04},
        {"game_id": "g5", "league": "MLB", "market": "moneyline",
         "selection": "Rays", "line": None, "odds": 130,
         "model_prob": 0.44, "edge": 0.01},  # below threshold
    ]


def test_config_parsing():
    from config import _parse_bet_type_mode, bet_type_config

    assert _parse_bet_type_mode("individual") == "individual"
    assert _parse_bet_type_mode("COMBINED") == "combined"
    assert _parse_bet_type_mode("  both ") == "both"
    assert _parse_bet_type_mode("garbage") == "both"   # unknown -> default
    assert _parse_bet_type_mode(None) == "both"

    cfg = bet_type_config()
    for key in ("mode", "combo_edge_threshold", "min_legs", "max_legs", "teaser_points"):
        assert key in cfg, f"missing config key {key}"
    assert cfg["mode"] in ("individual", "combined", "both")
    print("PASS: config parsing")


def test_odds_roundtrip():
    for a in (-120, -110, 100, 130, 250):
        d = american_to_decimal(a)
        assert abs(decimal_to_american(d) - a) <= 1, f"odds roundtrip failed for {a}"
    print("PASS: odds conversion roundtrip")


def test_eligibility_threshold():
    picks = sample_picks()
    eligible = select_eligible_legs(picks, 0.05)
    ids = {leg["selection"] for leg in eligible}
    # 0.08, 0.06, 0.055 clear 0.05; 0.04 and 0.01 do not.
    assert ids == {"Dodgers", "Yankees", "Chiefs -3"}, ids
    # Sorted by edge desc.
    assert eligible[0]["selection"] == "Dodgers"
    print("PASS: eligibility threshold selection")


def test_mode_individual():
    out = generate_bets(sample_picks(), {"mode": "individual", "combo_edge_threshold": 0.05,
                                         "min_legs": 2, "max_legs": 3})
    assert len(out["singles"]) == 5
    assert out["combinations"] == []
    print("PASS: mode=individual yields only singles")


def test_mode_combined():
    out = generate_bets(sample_picks(), {"mode": "combined", "combo_edge_threshold": 0.05,
                                         "min_legs": 2, "max_legs": 3})
    assert out["singles"] == []
    assert len(out["combinations"]) > 0
    print("PASS: mode=combined yields only combinations")


def test_mode_both_and_categorisation():
    out = generate_bets(sample_picks(), {"mode": "both", "combo_edge_threshold": 0.05,
                                         "min_legs": 2, "max_legs": 3})
    assert len(out["singles"]) == 5
    assert len(out["combinations"]) > 0

    for s in out["singles"]:
        assert s["bet_category"] == "single"
        assert s["bet_type"] in ("moneyline", "spread", "total")
        assert "status" in s
    for c in out["combinations"]:
        assert c["bet_category"] == "combination"
        assert c["bet_type"] in ("parlay", "teaser")
        assert c["num_legs"] >= 2
        # Each leg retains its own identifiers for independent tracking.
        for leg in c["legs"]:
            for field in ("leg_id", "game_id", "market", "selection", "odds", "edge", "status"):
                assert field in leg, f"leg missing {field}"
    print("PASS: mode=both categorisation (bet_category/bet_type on every bet)")


def test_teasers_only_point_markets():
    combos = generate_combinations(sample_picks(), {"combo_edge_threshold": 0.05,
                                                    "min_legs": 2, "max_legs": 3,
                                                    "teaser_points": 6.0})
    teasers = [c for c in combos if c["bet_type"] == "teaser"]
    # Only Chiefs -3 (spread) clears 0.05 among point markets -> not enough for a
    # 2-leg teaser, so none should be produced.
    assert teasers == [], "teaser built from <2 eligible point-market legs"

    # Lower the threshold so the total (0.04) also qualifies -> one 2-leg teaser.
    combos2 = generate_combinations(sample_picks(), {"combo_edge_threshold": 0.03,
                                                     "min_legs": 2, "max_legs": 2,
                                                     "teaser_points": 6.0})
    teasers2 = [c for c in combos2 if c["bet_type"] == "teaser"]
    assert len(teasers2) == 1, teasers2
    t = teasers2[0]
    assert t["teaser_points"] == 6.0
    for leg in t["legs"]:
        assert leg["market"] in ("spread", "total")
        assert "teased_line" in leg  # line moved in bettor's favour
    print("PASS: teasers built only from point markets, lines teased")


def test_parlay_odds_and_prob():
    legs = select_eligible_legs(sample_picks(), 0.05)[:2]  # Dodgers, Yankees
    parlay = build_parlay(legs)
    expected_dec = american_to_decimal(legs[0]["odds"]) * american_to_decimal(legs[1]["odds"])
    assert parlay["odds"] == decimal_to_american(expected_dec)
    # Combined model prob = product of leg probs.
    assert abs(parlay["model_prob"] - round(0.62 * 0.55, 4)) < 1e-9
    print("PASS: parlay odds and combined probability")


def test_individual_leg_trackability():
    legs = select_eligible_legs(sample_picks(), 0.05)[:3]
    parlay = build_parlay(legs)
    assert parlay["status"] == PENDING

    # Resolve one leg WON — parlay still pending (others open), leg is independent.
    l0 = parlay["legs"][0]["leg_id"]
    assert update_leg_status(parlay, l0, WON) is True
    assert parlay["legs"][0]["status"] == WON
    assert parlay["status"] == PENDING

    # A different leg loses -> whole parlay lost, but the won leg keeps its status.
    l1 = parlay["legs"][1]["leg_id"]
    update_leg_status(parlay, l1, LOST)
    assert parlay["legs"][0]["status"] == WON
    assert parlay["legs"][1]["status"] == LOST
    assert parlay["status"] == LOST
    print("PASS: individual leg trackability inside a combination")


def test_settlement_rules():
    def combo(statuses):
        return settle_combination({"legs": [{"status": s} for s in statuses]})

    assert combo([WON, WON, WON])["status"] == WON
    assert combo([WON, LOST, WON])["status"] == LOST
    assert combo([WON, PENDING])["status"] == PENDING
    assert combo([WON, PUSH])["status"] == WON      # push drops out, rest won
    assert combo([PUSH, PUSH])["status"] == PUSH
    assert combo([PUSH, PENDING])["status"] == PENDING
    print("PASS: parlay settlement (all-win, push-reduction) rules")


def main():
    test_config_parsing()
    test_odds_roundtrip()
    test_eligibility_threshold()
    test_mode_individual()
    test_mode_combined()
    test_mode_both_and_categorisation()
    test_teasers_only_point_markets()
    test_parlay_odds_and_prob()
    test_individual_leg_trackability()
    test_settlement_rules()
    print("\nALL BET-TYPE TESTS PASSED")


if __name__ == "__main__":
    main()
