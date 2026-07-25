"""
odds.py — Fetch live moneyline odds, calculate EV, size bets via Kelly Criterion.
Uses The Odds API. Same math as models/mlb/odds.py, generalized to not assume
baseball-specific fields (pitchers) — NFL/NCAAF picks carry a "week"/"venue"
label instead.
"""

import requests

from config import (ODDS_API_KEY, ODDS_SPORT, ODDS_REGIONS, ODDS_MARKETS,
                     BANKROLL, KELLY_FRACTION, MIN_EDGE_PCT,
                     MAX_BET_PCT, MIN_BET_DOLLARS)

SHARP_BOOKS = ['pinnacle', 'circa', 'betrivers']


def american_to_prob(odds):
    if odds > 0:
        return 100 / (odds + 100)
    return abs(odds) / (abs(odds) + 100)


def remove_vig(p_home, p_away):
    total = p_home + p_away
    return p_home / total, p_away / total


def fetch_live_odds():
    """Fetch today's moneyline odds from The Odds API for this sport."""
    if not ODDS_API_KEY:
        print("  ODDS_API_KEY not set, cannot fetch odds")
        return []
    url = f"https://api.the-odds-api.com/v4/sports/{ODDS_SPORT}/odds/"
    params = {
        'apiKey': ODDS_API_KEY,
        'regions': ODDS_REGIONS,
        'markets': ODDS_MARKETS,
        'oddsFormat': 'american',
        'dateFormat': 'iso',
    }
    try:
        r = requests.get(url, params=params, timeout=15)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"Odds API error: {e}")
        return []


def get_sharp_line(game_data):
    for book in game_data.get('bookmakers', []):
        if book['key'] in SHARP_BOOKS:
            for market in book['markets']:
                if market['key'] == 'h2h':
                    outcomes = {o['name']: o['price'] for o in market['outcomes']}
                    home_name = game_data['home_team']
                    away_name = game_data['away_team']
                    if home_name in outcomes and away_name in outcomes:
                        return outcomes[home_name], outcomes[away_name], book['key']

    home_odds_list, away_odds_list = [], []
    home_name = game_data['home_team']
    away_name = game_data['away_team']
    for book in game_data.get('bookmakers', []):
        for market in book['markets']:
            if market['key'] == 'h2h':
                outcomes = {o['name']: o['price'] for o in market['outcomes']}
                if home_name in outcomes:
                    home_odds_list.append(outcomes[home_name])
                if away_name in outcomes:
                    away_odds_list.append(outcomes[away_name])
    if home_odds_list and away_odds_list:
        return sum(home_odds_list) / len(home_odds_list), sum(away_odds_list) / len(away_odds_list), 'average'
    return None, None, None


def get_best_available_line(game_data, side='home'):
    team_name = game_data['home_team'] if side == 'home' else game_data['away_team']
    best_odds = -10000
    best_book = None
    for book in game_data.get('bookmakers', []):
        for market in book['markets']:
            if market['key'] == 'h2h':
                for outcome in market['outcomes']:
                    if outcome['name'] == team_name and outcome['price'] > best_odds:
                        best_odds = outcome['price']
                        best_book = book['title']
    return (best_odds if best_odds > -10000 else None), best_book


def calculate_ev(model_prob, market_odds):
    if market_odds is None:
        return 0.0
    if market_odds > 0:
        payout = market_odds / 100
    else:
        payout = 100 / abs(market_odds)
    ev = (model_prob * payout) - (1 - model_prob)
    return ev * 100


def kelly_bet_size(model_prob, market_odds, bankroll=None):
    if bankroll is None:
        bankroll = BANKROLL
    if market_odds is None:
        return 0.0
    if market_odds > 0:
        b = market_odds / 100
    else:
        b = 100 / abs(market_odds)
    p = model_prob
    q = 1 - p
    kelly_f = (b * p - q) / b
    if kelly_f <= 0:
        return 0.0
    bet_fraction = kelly_f * KELLY_FRACTION
    bet_amount = bet_fraction * bankroll
    bet_amount = min(bet_amount, bankroll * MAX_BET_PCT)
    bet_amount = max(bet_amount, MIN_BET_DOLLARS) if bet_amount > 0 else 0
    return round(bet_amount, 2)


def _last_word(name):
    return (name or "").lower().split()[-1] if name else ""


def find_ev_bets(predictions, live_odds_data, bankroll=None):
    """
    predictions: list of dicts with home_team, away_team, p_home, week/venue.
    live_odds_data: list from fetch_live_odds().
    Returns: list of bet recommendations, sorted by edge desc.
    """
    if bankroll is None:
        bankroll = BANKROLL

    odds_lookup = {}
    for game in live_odds_data:
        key = (_last_word(game.get('home_team')), _last_word(game.get('away_team')))
        odds_lookup[key] = game

    bets = []
    for pred in predictions:
        h_key = _last_word(pred['home_team'])
        a_key = _last_word(pred['away_team'])
        game_data = odds_lookup.get((h_key, a_key)) or odds_lookup.get((a_key, h_key))
        if not game_data:
            continue

        p_home = pred['p_home']
        p_away = 1 - p_home

        h_sharp_odds, a_sharp_odds, ref_book = get_sharp_line(game_data)
        if h_sharp_odds is None:
            continue
        h_implied = american_to_prob(h_sharp_odds)
        a_implied = american_to_prob(a_sharp_odds)
        h_no_vig, a_no_vig = remove_vig(h_implied, a_implied)

        h_best_odds, h_best_book = get_best_available_line(game_data, 'home')
        a_best_odds, a_best_book = get_best_available_line(game_data, 'away')

        h_ev = calculate_ev(p_home, h_best_odds)
        a_ev = calculate_ev(p_away, a_best_odds)

        for side, ev, odds, book, prob, mkt_prob in [
            ('HOME', h_ev, h_best_odds, h_best_book, p_home, h_no_vig),
            ('AWAY', a_ev, a_best_odds, a_best_book, p_away, a_no_vig),
        ]:
            edge = ev
            if edge >= MIN_EDGE_PCT:
                bet_size = kelly_bet_size(prob, odds, bankroll)
                if bet_size >= MIN_BET_DOLLARS:
                    bets.append({
                        'home_team':   pred['home_team'],
                        'away_team':   pred['away_team'],
                        'bet_side':    side,
                        'bet_team':    pred['home_team'] if side == 'HOME' else pred['away_team'],
                        'model_prob':  round(prob * 100, 1),
                        'market_prob': round(mkt_prob * 100, 1),
                        'edge_pct':    round(edge, 2),
                        'best_odds':   odds,
                        'best_book':   book,
                        'ref_book':    ref_book,
                        'kelly_bet_$': bet_size,
                        'venue':       pred.get('venue', ''),
                    })
    return sorted(bets, key=lambda x: x['edge_pct'], reverse=True)
