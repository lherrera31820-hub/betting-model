# ============================================================
# NFL BETTING MODEL CONFIG
# ============================================================
#
# Mirrors models/mlb/config.py's shape so odds.py / bet_types.py / the
# daily runner pattern stay consistent across sports. NFL has no free
# historical stats source equivalent to MLB's statsapi, so this sport uses
# an Elo-only model (see elo.py) seeded neutral and updated from each day's
# completed games, fetched from SportsDataIO — see sportsdata.py.

import os

# --- Sportsbook API (live odds) ---
ODDS_API_KEY = os.environ.get("ODDS_API_KEY", "")   # https://the-odds-api.com
ODDS_SPORT   = "americanfootball_nfl"
ODDS_REGIONS = "us"
ODDS_MARKETS = "h2h"                                 # moneyline

# --- Schedule / scores API ---
SPORTSDATAIO_API_KEY = os.environ.get("SPORTSDATAIO_API_KEY", "")
SPORTSDATAIO_HOST     = "https://api.sportsdata.io/v3/nfl"
# NFL uses ScoresByDate (confirmed via ingest/main.py's GAMES_ENDPOINT_NAME map).
GAMES_ENDPOINT_NAME   = "ScoresByDate"

# --- Bankroll & Betting Rules ---
BANKROLL         = 1000.0
KELLY_FRACTION   = 0.25                 # Quarter Kelly (conservative)
MIN_EDGE_PCT     = 3.0                  # only bet when edge > 3%
MAX_BET_PCT      = 0.05                 # never bet more than 5% of bankroll
MIN_BET_DOLLARS  = 10.0                 # minimum bet size

# --- Elo Settings ---
# NFL's per-game sample size is much smaller than MLB's (~17 games/season vs
# ~162), so ratings should move more per game (higher K) to converge within
# a season. Home-field advantage in Elo points is a commonly cited NFL value.
ELO_K   = 20
ELO_HFA = 55

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
