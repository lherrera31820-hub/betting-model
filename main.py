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

REQUEST_TIMEOUT = 20
MAX_RETRIES = 3


def get_api_key() -> str:
    key = os.environ.get("SPORTSDATAIO_API_KEY")
    if not key:
        print("ERROR: SPORTSDATAIO_API_KEY environment variable is not set.", file=sys.stderr)
        sys.exit(1)
    return key


def get_db_connection():
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("ERROR: DATABASE_URL environment variable is not set.", file=sys.stderr)
        sys.exit(1)
    return psycopg2.connect(database_url, connect_timeout=10)


def fetch_json(url: str, api_key: str) -> list:
    """GET a SportsDataIO endpoint with retries and basic backoff."""
    headers = {"Ocp-Apim-Subscription-Key": api_key}
    last_error = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
            if resp.status_code == 200:
                return resp.json()
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
            g.get("GameID") or g.get("GameId"),
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
            ))
    if not rows:
        return 0
    execute_values(
        cur,
        """
        INSERT INTO odds (
            game_id, sportsbook, market_type, home_line, away_line,
            home_price, away_price, total_points, over_price, under_price
        )
        VALUES %s;
        """,
        rows,
    )
    return len(rows)


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
                games = fetch_json(f"{base_url}/scores/json/ScoresByDate/{target_date}", api_key)
                rows_upserted += upsert_games(cur, sport, games)

                print("  fetching odds...")
                odds = fetch_json(f"{base_url}/odds/json/GameOddsByDate/{target_date}", api_key)
                rows_upserted += upsert_odds(cur, odds)

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
