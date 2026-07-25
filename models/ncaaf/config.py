# ============================================================
# NCAAF BETTING MODEL CONFIG
# ============================================================
#
# Mirrors models/nfl/config.py. NCAAF has ~130 FBS teams with wide talent
# gaps between conferences, so home-field advantage and per-game rating
# swings (K) are both a bit higher than the NFL defaults.

import os

# --- Sportsbook API (live odds) ---
ODDS_API_KEY = os.environ.get("ODDS_API_KEY", "")
ODDS_SPORT   = "americanfootball_ncaaf"
ODDS_REGIONS = "us"
ODDS_MARKETS = "h2h"

# --- Schedule / scores API ---
SPORTSDATAIO_API_KEY = os.environ.get("SPORTSDATAIO_API_KEY", "")
SPORTSDATAIO_HOST     = "https://api.sportsdata.io/v3/cfb"
# NCAAF (SportsDataIO's CFB product) uses GamesByDate, NOT ScoresByDate —
# confirmed via ingest/main.py's GAMES_ENDPOINT_NAME map (live-tested
# 2026-07-25; ScoresByDate 404s for this sport).
GAMES_ENDPOINT_NAME   = "GamesByDate"

# --- Bankroll & Betting Rules ---
BANKROLL         = 1000.0
KELLY_FRACTION   = 0.25
MIN_EDGE_PCT     = 3.0
MAX_BET_PCT      = 0.05
MIN_BET_DOLLARS  = 10.0

# --- Elo Settings ---
ELO_K   = 24
ELO_HFA = 65

# --- Bet-type generation (same semantics as models/mlb/config.py) ---
_VALID_BET_TYPE_MODES = ("individual", "combined", "both")


def _parse_bet_type_mode(raw):
    mode = (raw or "both").strip().lower()
    return mode if mode in _VALID_BET_TYPE_MODES else "both"


BET_TYPE_MODE = _parse_bet_type_mode(os.environ.get("BET_TYPES", "both"))

COMBO_EDGE_THRESHOLD = float(
    os.environ.get("COMBO_EDGE_THRESHOLD", MIN_EDGE_PCT / 100.0)
)
COMBO_MIN_LEGS   = int(os.environ.get("COMBO_MIN_LEGS", "2"))
COMBO_MAX_LEGS   = int(os.environ.get("COMBO_MAX_LEGS", "3"))
TEASER_POINTS    = float(os.environ.get("TEASER_POINTS", "6.0"))


def bet_type_config():
    return {
        "mode": BET_TYPE_MODE,
        "combo_edge_threshold": COMBO_EDGE_THRESHOLD,
        "min_legs": COMBO_MIN_LEGS,
        "max_legs": COMBO_MAX_LEGS,
        "teaser_points": TEASER_POINTS,
    }
