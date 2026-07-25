"""
sportsdata.py — Fetch NFL schedule/scores from SportsDataIO for a given date.

Mirrors the field-name defensiveness already established in ingest/main.py
(different SportsDataIO endpoints use slightly different key names for the
same concept depending on sport/endpoint), so this is deliberately tolerant
of missing/alternate keys rather than assuming one exact shape.
"""

import requests

from config import SPORTSDATAIO_API_KEY, SPORTSDATAIO_HOST, GAMES_ENDPOINT_NAME

REQUEST_TIMEOUT = 20


def fetch_games_by_date(date_str):
    """
    date_str: 'YYYY-MM-DD'. Returns a list of normalized game dicts:
      {game_id, home_team, away_team, home_score, away_score, status, date}
    Returns [] on any error or off-season 404 (treated as "no games").
    """
    if not SPORTSDATAIO_API_KEY:
        print("  SPORTSDATAIO_API_KEY not set, cannot fetch schedule")
        return []

    url = f"{SPORTSDATAIO_HOST}/scores/json/{GAMES_ENDPOINT_NAME}/{date_str}"
    headers = {"Ocp-Apim-Subscription-Key": SPORTSDATAIO_API_KEY}
    try:
        resp = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
        if resp.status_code == 404:
            print(f"  no games found for {date_str} (404 — likely off-season)")
            return []
        resp.raise_for_status()
        raw = resp.json()
    except requests.RequestException as exc:
        print(f"  SportsDataIO error fetching {date_str}: {exc}")
        return []

    games = []
    for g in raw or []:
        home = g.get("HomeTeamName") or g.get("HomeTeam") or g.get("HomeTeamKey")
        away = g.get("AwayTeamName") or g.get("AwayTeam") or g.get("AwayTeamKey")
        if not home or not away:
            continue
        games.append({
            "game_id":    g.get("GameID") or g.get("GameId") or g.get("GlobalGameID") or g.get("ScoreID"),
            "home_team":  home,
            "home_key":   g.get("HomeTeam") or g.get("HomeTeamKey") or home,
            "away_team":  away,
            "away_key":   g.get("AwayTeam") or g.get("AwayTeamKey") or away,
            "home_score": g.get("HomeScore") if g.get("HomeScore") is not None else g.get("HomeTeamScore"),
            "away_score": g.get("AwayScore") if g.get("AwayScore") is not None else g.get("AwayTeamScore"),
            "status":     g.get("Status") or "",
            "date":       g.get("DateTime") or g.get("Day") or date_str,
            "week":       g.get("Week"),
        })
    return games
