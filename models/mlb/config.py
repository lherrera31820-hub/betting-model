# ============================================================
# BETTING MODEL CONFIG — Edit these once, never touch again
# ============================================================

import os

# --- Sportsbook API ---
# Read from env (set ODDS_API_KEY as a GitHub Actions repo secret for real data).
ODDS_API_KEY = os.environ.get("ODDS_API_KEY", "")   # https://the-odds-api.com (free tier)
ODDS_SPORT   = "baseball_mlb"
ODDS_REGIONS = "us"
ODDS_MARKETS = "h2h"                     # moneyline

# --- Alert Settings (choose one or both) ---
# Gmail
GMAIL_ADDRESS  = "your_email@gmail.com"
GMAIL_PASSWORD = "your_app_password"     # Gmail App Password (not regular password)
ALERT_TO_EMAIL = "your_phone@txt.att.net"  # SMS via email gateway (AT&T example)

# Twilio (texts) — optional, more reliable than email-to-SMS
TWILIO_SID    = "YOUR_TWILIO_SID"
TWILIO_TOKEN  = "YOUR_TWILIO_TOKEN"
TWILIO_FROM   = "+1XXXXXXXXXX"
TWILIO_TO     = "+1XXXXXXXXXX"          # your cell number

# --- Bankroll & Betting Rules ---
BANKROLL         = 1000.0               # your total bankroll in $
KELLY_FRACTION   = 0.25                 # Quarter Kelly (conservative)
MIN_EDGE_PCT     = 3.0                  # only bet when edge > 3%
MAX_BET_PCT      = 0.05                 # never bet more than 5% of bankroll
MIN_BET_DOLLARS  = 10.0                 # minimum bet size

# --- Model Settings ---
LOOKBACK_GAMES   = 15                   # rolling window for team stats
MIN_PRIOR_GAMES  = 5                    # min games before model trusts rolling stats
ELO_K            = 30
ELO_HFA          = 70                   # home field advantage in Elo points

# --- Bet-type generation ---
# Controls which categories of bets the generator produces:
#   "individual" -> only Singles (moneyline / spread / total, each standalone)
#   "combined"   -> only Combinations (parlays / teasers built from eligible legs)
#   "both"       -> Singles AND Combinations (default)
# Override with the BET_TYPES env var / GitHub Actions repo variable.
_VALID_BET_TYPE_MODES = ("individual", "combined", "both")


def _parse_bet_type_mode(raw):
    """Normalise the BET_TYPES setting; unknown values fall back to 'both'."""
    mode = (raw or "both").strip().lower()
    return mode if mode in _VALID_BET_TYPE_MODES else "both"


BET_TYPE_MODE = _parse_bet_type_mode(os.environ.get("BET_TYPES", "both"))

# Minimum edge (as a fraction, e.g. 0.05 == 5%) a single pick must clear to be
# eligible as a leg in a parlay/teaser. Defaults to MIN_EDGE_PCT so combinations
# reuse the same edge bar as singles unless explicitly overridden.
COMBO_EDGE_THRESHOLD = float(
    os.environ.get("COMBO_EDGE_THRESHOLD", MIN_EDGE_PCT / 100.0)
)

# Parlay/teaser construction limits.
COMBO_MIN_LEGS   = int(os.environ.get("COMBO_MIN_LEGS", "2"))    # min legs per combination
COMBO_MAX_LEGS   = int(os.environ.get("COMBO_MAX_LEGS", "3"))    # max legs per combination
TEASER_POINTS    = float(os.environ.get("TEASER_POINTS", "6.0")) # points added to each teaser leg


def bet_type_config():
    """Bundle the bet-type settings into a single dict for the generator."""
    return {
        "mode": BET_TYPE_MODE,
        "combo_edge_threshold": COMBO_EDGE_THRESHOLD,
        "min_legs": COMBO_MIN_LEGS,
        "max_legs": COMBO_MAX_LEGS,
        "teaser_points": TEASER_POINTS,
    }
