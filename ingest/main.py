"""
main.py
-------
Pulls games + odds data from SportsDataIO for a given sport/date and upserts
it into PostgreSQL. Designed to be triggered by GitHub Actions
(workflow_dispatch or a schedule), but also runs fine locally.

Usage:
    python main.py --sport nfl --date 2026-09-07
    python main.py --sport mlb                     # defaults to today (UTC)

Environment variables required:
    DATABASE_URL            postgresql://... connection string
    SPORTSDATAIO_API_KEY    SportsDataIO subscription key
"""

import os
import sys
import argparse
import time
import json
import difflib
from datetime import date, datetime, timezone

import requests
import psycopg2
from psycopg2.extras import Json, execute_values

# SportsDataIO base hosts differ slightly per sport; this covers the ones
# most commonly used for team-sport scores + odds feeds.
SPORT_HOSTS = {
    "nfl": "https://api.sportsdata.io/v3/nfl",
    "mlb": "https://api.sportsdata.io/v3/mlb",
    "nba": "https://api.sportsdata.io/v3/nba",
    "ncaaf": "https://api.sportsdata.io/v3/cfb",
    "ncaab": "https://api.sportsdata.io/v3/cbb",
}

# SportsDataIO does NOT use one consistent endpoint name for "games/scores on
# a given date" across sports: football leagues (NFL, confirmed; CFB, matches
# the same football-league pattern) use ScoresByDate, while MLB, NBA, and CBB
# (confirmed via SportsDataIO's own docs) use GamesByDate. Using the wrong
# name returns an HTTP 404, not an auth error, which is what broke the first
# live MLB run.
GAMES_ENDPOINT_NAME = {
    "nfl": "ScoresByDate",
    "mlb": "GamesByDate",
    "nba": "GamesByDate",
    "ncaaf": "GamesByDate",  # confirmed via live test 2026-07-25; ScoresByDate 404s for CFB
    "ncaab": "GamesByDate",
}

# The Odds API (https://the-odds-api.com) uses its own sport keys.
# Used as a SECOND, optional odds source alongside SportsDataIO — not a
# replacement. Only the sports also covered by SPORT_HOSTS are mapped here.
ODDS_API_SPORT_KEYS = {
    "nfl": "americanfootball_nfl",
    "mlb": "baseball_mlb",
    "nba": "basketball_nba",
    "ncaaf": "americanfootball_ncaaf",
    "ncaab": "basketball_ncaab",
}
ODDS_API_BASE = "https://api.the-odds-api.com/v4"

REQUEST_TIMEOUT = 20
MAX_RETRIES = 3


def get_api_key() -> str:
    key = os.environ.get("SPORTSDATAIO_API_KEY")
    if not key:
        print("ERROR: SPORTSDATAIO_API_KEY environment variable is not set.", file=sys.stderr)
        sys.exit(1)
    return key


def get_odds_api_key() -> str | None:
    """Optional. If not set, the Odds API step is skipped (SportsDataIO odds still run)."""
    return os.environ.get("ODDS_API_KEY")


def get_db_connection():
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("ERROR: DATABASE_URL environment variable is not set.", file=sys.stderr)
        sys.exit(1)
    return psycopg2.connect(database_url, connect_timeout=10)


def fetch_json(url: str, api_key: str, tolerate_404: bool = False) -> list:
    """GET a SportsDataIO endpoint with retries and basic backoff.

    tolerate_404: if True, an HTTP 404 is treated as "no data available for
    this query" and returns an empty list instead of raising. SportsDataIO
    returns 404 (not an empty 200 array) for odds endpoints on dates with no
    games/odds -- e.g. off-season dates, or historical dates old enough that
    they've moved to SportsDataIO's separate Betting Data Archive. This is
    expected and should not fail the whole ingestion run; core endpoints
    like teams/games should NOT set this, since a 404 there usually means a
    real problem (bad URL, wrong sport, etc.).
    """
    headers = {"Ocp-Apim-Subscription-Key": api_key}
    last_error = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
            if resp.status_code == 200:
                return resp.json()
            if resp.status_code == 404 and tolerate_404:
                print(f"  no data available (404) for {url} -- treating as empty")
                return []
            last_error = f"HTTP {resp.status_code}: {resp.text[:300]}"
        except requests.RequestException as exc:
            last_error = str(exc)
        print(f"  attempt {attempt}/{MAX_RETRIES} failed: {last_error}")
        time.sleep(2 * attempt)
    raise RuntimeError(f"Failed to fetch {url} after {MAX_RETRIES} attempts: {last_error}")


def upsert_teams(cur, sport_key: str, teams: list):
    if not teams:
        return
    cur.execute("SELECT league_id FROM leagues WHERE sport_key = %s;", (sport_key,))
    league_id = cur.fetchone()[0]

    rows = [
        (t.get("TeamID") or t.get("GlobalTeamID"), league_id, t.get("Key"), t.get("FullName") or t.get("Name"))
        for t in teams
        if t.get("TeamID") or t.get("GlobalTeamID")
    ]
    execute_values(
        cur,
        """
        INSERT INTO teams (team_id, league_id, key, full_name)
        VALUES %s
        ON CONFLICT (team_id) DO UPDATE SET
            key = EXCLUDED.key,
            full_name = EXCLUDED.full_name,
            updated_at = now();
        """,
        rows,
    )


def upsert_games(cur, sport_key: str, games: list):
    if not games:
        return 0
    cur.execute("SELECT league_id FROM leagues WHERE sport_key = %s;", (sport_key,))
    league_id = cur.fetchone()[0]

    rows = []
    for g in games:
        rows.append((
            g.get("GameID") or g.get("GameId") or g.get("GlobalGameID") or g.get("ScoreID"),
            league_id,
            g.get("Season"),
            str(g.get("Week") or g.get("SeriesInfo") or g.get("SeasonType") or ""),
            g.get("DateTime") or g.get("Day"),
            g.get("HomeTeamID") or g.get("GlobalHomeTeamID"),
            g.get("AwayTeamID") or g.get("GlobalAwayTeamID"),
            g.get("HomeScore") or g.get("HomeTeamRuns"),
            g.get("AwayScore") or g.get("AwayTeamRuns"),
            g.get("Status"),
            Json(g),
        ))

    execute_values(
        cur,
        """
        INSERT INTO games (
            game_id, league_id, season, week_or_series, game_date,
            home_team_id, away_team_id, home_score, away_score, status, raw_payload
        )
        VALUES %s
        ON CONFLICT (game_id) DO UPDATE SET
            home_score = EXCLUDED.home_score,
            away_score = EXCLUDED.away_score,
            status = EXCLUDED.status,
            raw_payload = EXCLUDED.raw_payload,
            updated_at = now();
        """,
        rows,
    )
    return len(rows)


def upsert_odds(cur, odds_payload: list):
    """SportsDataIO odds (primary source)."""
    if not odds_payload:
        return 0
    rows = []
    for game_odds in odds_payload:
        game_id = game_odds.get("GameId") or game_odds.get("GameID")
        for line in game_odds.get("PregameOdds", []) or []:
            rows.append((
                game_id,
                line.get("Sportsbook"),
                "spread_moneyline_total",
                line.get("HomePointSpread"),
                line.get("AwayPointSpread"),
                line.get("HomeMoneyLine"),
                line.get("AwayMoneyLine"),
                line.get("OverUnder"),
                line.get("OverPayout"),
                line.get("UnderPayout"),
                "sportsdataio",
            ))
    if not rows:
        return 0
    execute_values(
        cur,
        """
        INSERT INTO odds (
            game_id, sportsbook, market_type, home_line, away_line,
            home_price, away_price, total_points, over_price, under_price, data_source
        )
        VALUES %s;
        """,
        rows,
    )
    return len(rows)


def _normalize_name(name: str) -> str:
    return (name or "").lower().replace(".", "").strip()


def _names_match(db_name: str, api_name: str) -> bool:
    a, b = _normalize_name(db_name), _normalize_name(api_name)
    if not a or not b:
        return False
    if a in b or b in a:
        return True
    return difflib.SequenceMatcher(None, a, b).ratio() > 0.6


def match_game_id(cur, sport_key: str, home_team_name: str, away_team_name: str, commence_time: str):
    """The Odds API doesn't share game IDs with SportsDataIO, so games are
    matched by team name + date against what SportsDataIO already inserted
    for this same run."""
    game_date = (commence_time or "")[:10]
    if not game_date:
        return None
    cur.execute(
        """
        SELECT g.game_id, ht.full_name, at.full_name
        FROM games g
        JOIN leagues l ON g.league_id = l.league_id
        JOIN teams ht ON g.home_team_id = ht.team_id
        JOIN teams at ON g.away_team_id = at.team_id
        WHERE l.sport_key = %s AND g.game_date::date = %s::date;
        """,
        (sport_key, game_date),
    )
    for game_id, home_full, away_full in cur.fetchall():
        if _names_match(home_full, home_team_name) and _names_match(away_full, away_team_name):
            return game_id
    return None


def upsert_odds_from_oddsapi(cur, sport_key: str, events: list):
    """The Odds API odds (secondary/supplementary source). Requires games to
    already be inserted for this date via SportsDataIO in the same run."""
    if not events:
        return 0
    rows = []
    unmatched = 0
    for event in events:
        home_team = event.get("home_team")
        away_team = event.get("away_team")
        game_id = match_game_id(cur, sport_key, home_team, away_team, event.get("commence_time"))
        if game_id is None:
            unmatched += 1
            continue
        for bookmaker in event.get("bookmakers", []):
            home_line = away_line = home_price = away_price = None
            total_points = over_price = under_price = None
            for market in bookmaker.get("markets", []):
                key = market.get("key")
                outcomes = market.get("outcomes", [])
                if key == "h2h":
                    for o in outcomes:
                        if o.get("name") == home_team:
                            home_price = o.get("price")
                        elif o.get("name") == away_team:
                            away_price = o.get("price")
                elif key == "spreads":
                    for o in outcomes:
                        if o.get("name") == home_team:
                            home_line = o.get("point")
                        elif o.get("name") == away_team:
                            away_line = o.get("point")
                elif key == "totals":
                    for o in outcomes:
                        if o.get("name") == "Over":
                            total_points = o.get("point")
                            over_price = o.get("price")
                        elif o.get("name") == "Under":
                            under_price = o.get("price")
            rows.append((
                game_id,
                bookmaker.get("title"),
                "spread_moneyline_total",
                home_line, away_line, home_price, away_price,
                total_points, over_price, under_price,
                "theoddsapi",
            ))
    if unmatched:
        print(f"  [odds api] {unmatched} event(s) could not be matched to a SportsDataIO game and were skipped")
    if not rows:
        return 0
    execute_values(
        cur,
        """
        INSERT INTO odds (
            game_id, sportsbook, market_type, home_line, away_line,
            home_price, away_price, total_points, over_price, under_price, data_source
        )
        VALUES %s;
        """,
        rows,
    )
    return len(rows)


def fetch_oddsapi_events(sport_key: str, odds_api_key: str) -> list:
    the_odds_api_sport = ODDS_API_SPORT_KEYS.get(sport_key)
    if not the_odds_api_sport:
        print(f"  [odds api] no Odds API mapping for sport '{sport_key}', skipping")
        return []
    url = f"{ODDS_API_BASE}/sports/{the_odds_api_sport}/odds"
    params = {
        "apiKey": odds_api_key,
        "regions": "us",
        "markets": "h2h,spreads,totals",
        "oddsFormat": "american",
        "dateFormat": "iso",
    }
    last_error = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)
            if resp.status_code == 200:
                return resp.json()
            last_error = f"HTTP {resp.status_code}: {resp.text[:300]}"
        except requests.RequestException as exc:
            last_error = str(exc)
        print(f"  [odds api] attempt {attempt}/{MAX_RETRIES} failed: {last_error}")
        time.sleep(2 * attempt)
    print(f"  [odds api] giving up after {MAX_RETRIES} attempts: {last_error}")
    return []


def log_run(cur, sport_key: str, run_date: str, rows_upserted: int, status: str, error_message: str = None):
    cur.execute(
        """
        INSERT INTO ingestion_log (sport_key, run_date, rows_upserted, status, error_message, github_run_id, finished_at)
        VALUES (%s, %s, %s, %s, %s, %s, now());
        """,
        (sport_key, run_date, rows_upserted, status, error_message, os.environ.get("GITHUB_RUN_ID")),
    )


def run(sport: str, target_date: str):
    sport = sport.lower()
    if sport not in SPORT_HOSTS:
        print(f"ERROR: unsupported sport '{sport}'. Choose from: {', '.join(SPORT_HOSTS)}", file=sys.stderr)
        sys.exit(1)

    api_key = get_api_key()
    base_url = SPORT_HOSTS[sport]

    print(f"[{datetime.now(timezone.utc).isoformat()}] Ingesting {sport.upper()} for {target_date}")

    conn = get_db_connection()
    rows_upserted = 0
    try:
        with conn:
            with conn.cursor() as cur:
                print("  fetching teams...")
                teams = fetch_json(f"{base_url}/scores/json/teams", api_key)
                upsert_teams(cur, sport, teams)

                print("  fetching games...")
                games_endpoint = GAMES_ENDPOINT_NAME[sport]
                games = fetch_json(f"{base_url}/scores/json/{games_endpoint}/{target_date}", api_key)
                rows_upserted += upsert_games(cur, sport, games)

                print("  fetching odds (SportsDataIO)...")
                odds = fetch_json(f"{base_url}/odds/json/GameOddsByDate/{target_date}", api_key, tolerate_404=True)
                rows_upserted += upsert_odds(cur, odds)

                odds_api_key = get_odds_api_key()
                if odds_api_key:
                    print("  fetching odds (The Odds API)...")
                    events = fetch_oddsapi_events(sport, odds_api_key)
                    rows_upserted += upsert_odds_from_oddsapi(cur, sport, events)
                else:
                    print("  ODDS_API_KEY not set, skipping The Odds API step")

                log_run(cur, sport, target_date, rows_upserted, "success")

        print(f"Done. Upserted {rows_upserted} rows for {sport.upper()} on {target_date}.")

    except Exception as exc:
        conn.rollback()
        with conn:
            with conn.cursor() as cur:
                log_run(cur, sport, target_date, rows_upserted, "failed", str(exc))
        print(f"ERROR during ingestion: {exc}", file=sys.stderr)
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Ingest SportsDataIO games/odds into PostgreSQL.")
    parser.add_argument("--sport", default=os.environ.get("DEFAULT_SPORT", "nfl"), help="nfl, mlb, nba, ncaaf, ncaab")
    parser.add_argument("--date", default="", help="YYYY-MM-DD, defaults to today (UTC)")
    args = parser.parse_args()

    target_date = args.date.strip() or date.today().isoformat()
    run(args.sport, target_date)
