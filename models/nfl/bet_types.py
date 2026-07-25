"""
bet_types.py — Bet-type generation and settlement.

Turns a flat list of individual pick "legs" into two output categories:

  * Singles      — one standalone bet per pick (moneyline / spread / total).
  * Combinations — parlays and teasers built by combining eligible legs
                   (those clearing the combo edge threshold).

Every emitted bet carries a `bet_category` ("single" | "combination") and a
`bet_type` ("moneyline" | "spread" | "total" | "parlay" | "teaser"). Each
combination keeps the FULL detail of its constituent legs — game id, market,
selection, odds, edge and an independent per-leg `status` — so a single leg can
be settled/traced on its own while the parlay's rolled-up status is recomputed
from its legs (all legs must win for the parlay to win; pushed legs drop out).

Pure standard library — no pandas/numpy — so it imports cleanly in tests and in
both the model pipeline (model/daily_runner.py) and build_picks.py.
"""
from itertools import combinations

# Status vocabulary shared by singles and individual combination legs.
PENDING, WON, LOST, PUSH = "pending", "won", "lost", "push"

# Standard fixed payout for a 6-point teaser, keyed by leg count (American odds).
# Falls back to a derived value for leg counts outside the table.
TEASER_ODDS_BY_LEGS = {2: -110, 3: 160, 4: 300, 5: 450, 6: 600}

# Markets that can legally be teased (point-based markets only).
TEASABLE_MARKETS = ("spread", "total")

# Bound how many legs we combine over, to keep C(n, k) explosion in check.
_MAX_LEGS_CONSIDERED = 6
_MAX_COMBINATIONS = 25


def american_to_decimal(odds):
    odds = float(odds)
    if odds < 0:
        return 1 + (100 / -odds)
    return 1 + (odds / 100)


def decimal_to_american(decimal_odds):
    if decimal_odds <= 1:
        return 0
    if decimal_odds >= 2:
        return int(round((decimal_odds - 1) * 100))
    return int(round(-100 / (decimal_odds - 1)))


def american_to_implied_prob(odds):
    odds = float(odds)
    if odds < 0:
        return -odds / (-odds + 100)
    return 100 / (odds + 100)


def normalize_market(market):
    """Map a raw market string onto moneyline / spread / total."""
    m = (market or "").lower()
    if "spread" in m or m in ("ats", "runline", "run_line", "puckline"):
        return "spread"
    if "total" in m or m in ("ou", "over_under", "over/under"):
        return "total"
    if "moneyline" in m or m in ("ml", "h2h", "side", "moneyline"):
        return "moneyline"
    return m or "moneyline"


def _leg_id(pick):
    """Stable identifier for a leg, preferring an explicit id if present."""
    for key in ("leg_id", "pick_id", "id"):
        if pick.get(key):
            return str(pick[key])
    parts = [
        pick.get("league"),
        pick.get("game_id") or pick.get("event_id"),
        pick.get("market"),
        pick.get("selection"),
        pick.get("line"),
    ]
    return "-".join(str(p) for p in parts if p is not None).replace(" ", "_")


def make_leg(pick):
    """
    Normalise a raw pick into a leg record that stays individually trackable
    whether it is bet as a single or embedded in a combination.
    """
    return {
        "leg_id": _leg_id(pick),
        "game_id": pick.get("game_id") or pick.get("event_id"),
        "league": pick.get("league"),
        "market": normalize_market(pick.get("market") or pick.get("market_type")),
        "selection": pick.get("selection"),
        "line": pick.get("line"),
        "odds": pick.get("odds"),
        "model_prob": pick.get("model_prob"),
        "edge": pick.get("edge"),
        "status": pick.get("status", PENDING),
    }


def make_single(pick):
    """Build a standalone Single bet from a pick."""
    leg = make_leg(pick)
    return {
        "bet_id": leg["leg_id"],
        "bet_category": "single",
        "bet_type": leg["market"],
        "game_id": leg["game_id"],
        "league": leg["league"],
        "selection": leg["selection"],
        "line": leg["line"],
        "odds": leg["odds"],
        "model_prob": leg["model_prob"],
        "edge": leg["edge"],
        "status": leg["status"],
    }


def select_eligible_legs(picks, threshold):
    """Return legs whose edge clears the combo threshold (edge as a fraction)."""
    eligible = []
    for p in picks:
        edge = p.get("edge")
        if edge is not None and edge >= threshold:
            eligible.append(make_leg(p))
    # Highest edge first so combinations are deterministic and prioritised.
    eligible.sort(key=lambda leg: leg.get("edge") or 0, reverse=True)
    return eligible


def _combined_decimal(legs):
    dec = 1.0
    for leg in legs:
        dec *= american_to_decimal(leg["odds"])
    return dec


def _combined_model_prob(legs):
    """Independent-legs assumption: multiply per-leg model probabilities."""
    prob = 1.0
    have_any = False
    for leg in legs:
        mp = leg.get("model_prob")
        if mp is None:
            return None
        prob *= mp
        have_any = True
    return prob if have_any else None


def _teaser_odds(num_legs):
    if num_legs in TEASER_ODDS_BY_LEGS:
        return TEASER_ODDS_BY_LEGS[num_legs]
    # Rough fallback: each leg ~ -110 legged into a parlay-style product.
    dec = american_to_decimal(-110) ** num_legs
    return decimal_to_american(dec)


def _combination(legs, bet_type, odds, teaser_points=None):
    implied = american_to_implied_prob(odds) if odds else None
    model_prob = _combined_model_prob(legs)
    edge = round(model_prob - implied, 4) if (model_prob is not None and implied is not None) else None
    leg_ids = "+".join(leg["leg_id"] for leg in legs)
    combo = {
        "bet_id": f"{bet_type}:{leg_ids}",
        "bet_category": "combination",
        "bet_type": bet_type,
        "num_legs": len(legs),
        "odds": odds,
        "implied_prob": round(implied, 4) if implied is not None else None,
        "model_prob": round(model_prob, 4) if model_prob is not None else None,
        "edge": edge,
        # Each leg keeps its own identifiers + independent status.
        "legs": [dict(leg) for leg in legs],
        "status": PENDING,
    }
    if teaser_points is not None:
        combo["teaser_points"] = teaser_points
    settle_combination(combo)
    return combo


def build_parlay(legs):
    """Combine legs into a parlay: decimal odds multiply out."""
    odds = decimal_to_american(_combined_decimal(legs))
    return _combination(list(legs), "parlay", odds)


def build_teaser(legs, teaser_points):
    """
    Combine point-based legs into a teaser. Each leg's line is moved in the
    bettor's favour by `teaser_points`; payout uses the standard teaser table.
    """
    teased = []
    for leg in legs:
        t = dict(leg)
        if t.get("line") is not None:
            # Move the line toward the bettor: spreads/overs get points added,
            # which for a favourite selection means a friendlier number.
            t["teased_line"] = round(t["line"] + teaser_points, 1)
        teased.append(t)
    odds = _teaser_odds(len(teased))
    return _combination(teased, "teaser", odds, teaser_points=teaser_points)


def _bounded_combos(legs, min_legs, max_legs):
    """Yield leg tuples for sizes in [min_legs, max_legs], capped in count."""
    pool = legs[:_MAX_LEGS_CONSIDERED]
    hi = min(max_legs, len(pool))
    emitted = 0
    for size in range(max(2, min_legs), hi + 1):
        for combo in combinations(pool, size):
            if emitted >= _MAX_COMBINATIONS:
                return
            emitted += 1
            yield combo


def generate_combinations(picks, config):
    """Build parlays and teasers from picks that clear the combo threshold."""
    threshold = config.get("combo_edge_threshold", 0.0)
    min_legs = config.get("min_legs", 2)
    max_legs = config.get("max_legs", 3)
    teaser_points = config.get("teaser_points", 6.0)

    eligible = select_eligible_legs(picks, threshold)
    combos = []
    for legs in _bounded_combos(eligible, min_legs, max_legs):
        combos.append(build_parlay(list(legs)))

    teasable = [leg for leg in eligible if leg["market"] in TEASABLE_MARKETS]
    for legs in _bounded_combos(teasable, min_legs, max_legs):
        combos.append(build_teaser(list(legs), teaser_points))

    return combos


def generate_bets(picks, config):
    """
    Top-level generator. Returns singles and/or combinations according to the
    configured mode ("individual" | "combined" | "both").
    """
    mode = config.get("mode", "both")
    result = {"mode": mode, "singles": [], "combinations": []}

    if mode in ("individual", "both"):
        result["singles"] = [make_single(p) for p in picks]

    if mode in ("combined", "both"):
        result["combinations"] = generate_combinations(picks, config)

    return result


def settle_combination(combo):
    """
    Recompute a combination's rolled-up status from its legs.
      * any leg LOST         -> combination LOST
      * push legs drop out    (standard parlay reduction)
      * all remaining WON     -> combination WON
      * otherwise             -> PENDING
    Mutates and returns the combo.
    """
    statuses = [leg.get("status", PENDING) for leg in combo.get("legs", [])]
    if any(s == LOST for s in statuses):
        combo["status"] = LOST
    else:
        active = [s for s in statuses if s != PUSH]
        if not active:
            combo["status"] = PUSH
        elif all(s == WON for s in active):
            combo["status"] = WON
        else:
            combo["status"] = PENDING
    return combo


def update_leg_status(combo, leg_id, status):
    """
    Independently update one leg inside a combination, then re-settle the
    rolled-up parlay/teaser status. Returns True if the leg was found.
    """
    found = False
    for leg in combo.get("legs", []):
        if leg.get("leg_id") == leg_id:
            leg["status"] = status
            found = True
    if found:
        settle_combination(combo)
    return found
