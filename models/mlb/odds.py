"""
odds.py — Fetch live moneyline odds, calculate EV, size bets via Kelly Criterion.
Uses The Odds API (free tier: 500 credits/month).
"""

import requests
import numpy as np
from config import (ODDS_API_KEY, ODDS_SPORT, ODDS_REGIONS, ODDS_MARKETS,
                    BANKROLL, KELLY_FRACTION, MIN_EDGE_PCT,
                    MAX_BET_PCT, MIN_BET_DOLLARS)


SHARP_BOOKS = ['pinnacle', 'circa', 'betrivers']
SOFT_BOOKS  = ['draftkings', 'fanduel', 'betmgm', 'caesars', 'pointsbet']


def american_to_prob(odds):
    """Convert American odds to implied probability (no vig)."""
    if odds > 0:
        return 100 / (odds + 100)
    else:
        return abs(odds) / (abs(odds) + 100)


def remove_vig(p_home, p_away):
    """Remove bookmaker vig to get true implied probabilities."""
    total = p_home + p_away
    return p_home / total, p_away / total


def fetch_live_odds():
    """Fetch today's MLB moneyline odds from The Odds API."""
    url = f"https://api.the-odds-api.com/v4/sports/{ODDS_SPORT}/odds/"
    params = {
        'apiKey': ODDS_API_KEY,
        'regions': ODDS_REGIONS,
        'markets': ODDS_MARKETS,
        'oddsFormat': 'american',
        'dateFormat': 'iso',
    }
    try:
        r = requests.get(url, params=params, timeout=10)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"Odds API error: {e}")
        return []


def get_sharp_line(game_data):
    """
    Return sharpest available line for home/away.
    Priority: Pinnacle > Circa > average of all books.
    """
    for book in game_data.get('bookmakers', []):
        if book['key'] in SHARP_BOOKS:
            for market in book['markets']:
                if market['key'] == 'h2h':
                    outcomes = {o['name']: o['price'] for o in market['outcomes']}
                    home_name = game_data['home_team']
                    away_name = game_data['away_team']
                    if home_name in outcomes and away_name in outcomes:
                        return outcomes[home_name], outcomes[away_name], book['key']

    # Fallback: average across all books
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
        return np.mean(home_odds_list), np.mean(away_odds_list), 'average'
    return None, None, None


def get_best_available_line(game_data, side='home'):
    """Get best (highest) available American odds for a side across all books."""
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
    return best_odds if best_odds > -10000 else None, best_book


def calculate_ev(model_prob, market_odds):
    """
    Calculate expected value percentage.
    model_prob: our estimated P(home win)
    market_odds: American odds for home team at best book
    """
    if market_odds is None:
        return 0.0
    implied_prob = american_to_prob(market_odds)
    # EV = (model_prob * payout) - (1 - model_prob)
    if market_odds > 0:
        payout = market_odds / 100
    else:
        payout = 100 / abs(market_odds)
    ev = (model_prob * payout) - (1 - model_prob)
    return ev * 100  # return as percentage


def kelly_bet_size(model_prob, market_odds, bankroll=None):
    """
    Calculate Kelly Criterion bet size.
    Returns dollar amount to bet (or 0 if no edge).
    """
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
    # Apply fractional Kelly
    bet_fraction = kelly_f * KELLY_FRACTION
    bet_amount = bet_fraction * bankroll
    # Cap at max bet
    bet_amount = min(bet_amount, bankroll * MAX_BET_PCT)
    bet_amount = max(bet_amount, MIN_BET_DOLLARS) if bet_amount > 0 else 0
    return round(bet_amount, 2)


def find_ev_bets(predictions, live_odds_data, bankroll=None):
    """
    Match model predictions to live odds, find +EV bets.
    predictions: list of dicts with home_team, away_team, p_home
    live_odds_data: list from fetch_live_odds()
    Returns: list of bet recommendations
    """
    if bankroll is None:
        bankroll = BANKROLL

    # Build odds lookup by team names
    odds_lookup = {}
    for game in live_odds_data:
        key = (game['home_team'].lower().split()[-1],
               game['away_team'].lower().split()[-1])
        odds_lookup[key] = game

    bets = []
    for pred in predictions:
        h_key = pred['home_team'].lower().split()[-1]
        a_key = pred['away_team'].lower().split()[-1]
        game_data = odds_lookup.get((h_key, a_key)) or odds_lookup.get((a_key, h_key))
        if not game_data:
            continue

        p_home = pred['p_home']
        p_away = 1 - p_home

        # Sharp line implied probs
        h_sharp_odds, a_sharp_odds, ref_book = get_sharp_line(game_data)
        if h_sharp_odds is None:
            continue
        h_implied = american_to_prob(h_sharp_odds)
        a_implied = american_to_prob(a_sharp_odds)
        h_no_vig, a_no_vig = remove_vig(h_implied, a_implied)

        # Best available line for betting
        h_best_odds, h_best_book = get_best_available_line(game_data, 'home')
        a_best_odds, a_best_book = get_best_available_line(game_data, 'away')

        # EV calculation
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
                        'home_team':    pred['home_team'],
                        'away_team':    pred['away_team'],
                        'bet_side':     side,
                        'bet_team':     pred['home_team'] if side=='HOME' else pred['away_team'],
                        'model_prob':   round(prob * 100, 1),
                        'market_prob':  round(mkt_prob * 100, 1),
                        'edge_pct':     round(edge, 2),
                        'best_odds':    odds,
                        'best_book':    book,
                        'ref_book':     ref_book,
                        'kelly_bet_$':  bet_size,
                        'home_pitcher': pred.get('home_pitcher', ''),
                        'away_pitcher': pred.get('away_pitcher', ''),
                        'venue':        pred.get('venue', ''),
                    })
    return sorted(bets, key=lambda x: x['edge_pct'], reverse=True)
