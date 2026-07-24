"""
database_setup.py
------------------
Creates (or verifies) the PostgreSQL schema used by the betting model, and
doubles as a connectivity smoke test for GitHub Actions.

Usage:
    python database_setup.py            # create/verify all tables
    python database_setup.py --test     # connectivity check only (no DDL)

Requires the DATABASE_URL environment variable, e.g.:
    postgresql://username:password@host:5432/dbname
"""

import os
import sys
import argparse
import psycopg2
from datetime import datetime, timezone

SCHEMA_STATEMENTS = [
    # Reference table: one row per sport/league
    """
    CREATE TABLE IF NOT EXISTS leagues (
        league_id       SERIAL PRIMARY KEY,
        sport_key       TEXT UNIQUE NOT NULL,      -- e.g. 'nfl', 'mlb', 'nba'
        display_name    TEXT NOT NULL,
        created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
    );
    """,

    # Teams
    """
    CREATE TABLE IF NOT EXISTS teams (
        team_id             BIGINT PRIMARY KEY,        -- SportsDataIO TeamID
        league_id           INT REFERENCES leagues(league_id),
        key                 TEXT NOT NULL,             -- e.g. 'KC', 'NYY'
        full_name           TEXT NOT NULL,
        updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
    );
    """,

    # Games / events
    """
    CREATE TABLE IF NOT EXISTS games (
        game_id             BIGINT PRIMARY KEY,        -- SportsDataIO GameID
        league_id           INT REFERENCES leagues(league_id),
        season              INT,
        week_or_series      TEXT,
        game_date           TIMESTAMPTZ,
        home_team_id        BIGINT REFERENCES teams(team_id),
        away_team_id        BIGINT REFERENCES teams(team_id),
        home_score          INT,
        away_score          INT,
        status              TEXT,                      -- Scheduled, InProgress, Final, etc.
        raw_payload         JSONB,                      -- full SportsDataIO record for auditing
        inserted_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
    );
    """,

    # Betting odds (moneyline / spread / total) per game, per sportsbook, per pull
    """
    CREATE TABLE IF NOT EXISTS odds (
        odds_id             BIGSERIAL PRIMARY KEY,
        game_id             BIGINT REFERENCES games(game_id),
        sportsbook          TEXT,
        market_type         TEXT,                       -- 'moneyline', 'spread', 'total'
        home_line           NUMERIC,
        away_line           NUMERIC,
        home_price          INT,
        away_price          INT,
        total_points        NUMERIC,
        over_price          INT,
        under_price         INT,
        captured_at         TIMESTAMPTZ NOT NULL DEFAULT now()
    );
    """,

    # Model predictions, so results can be back-tested against actual outcomes
    """
    CREATE TABLE IF NOT EXISTS predictions (
        prediction_id       BIGSERIAL PRIMARY KEY,
        game_id             BIGINT REFERENCES games(game_id),
        model_version       TEXT NOT NULL,
        predicted_winner    BIGINT REFERENCES teams(team_id),
        win_probability     NUMERIC,
        predicted_spread    NUMERIC,
        predicted_total     NUMERIC,
        edge_pct            NUMERIC,                    -- model edge vs. market price
        created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
    );
    """,

    # Every workflow run logs itself here — makes "did the last run actually work" trivial to check
    """
    CREATE TABLE IF NOT EXISTS ingestion_log (
        run_id              BIGSERIAL PRIMARY KEY,
        sport_key           TEXT,
        run_date            DATE,
        rows_upserted       INT,
        status              TEXT,                       -- 'success', 'failed'
        error_message       TEXT,
        github_run_id       TEXT,
        started_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
        finished_at         TIMESTAMPTZ
    );
    """,

    # Seed the leagues table (idempotent)
    """
    INSERT INTO leagues (sport_key, display_name) VALUES
        ('nfl', 'NFL'),
        ('mlb', 'MLB'),
        ('nba', 'NBA'),
        ('ncaaf', 'NCAA Football'),
        ('ncaab', 'NCAA Basketball')
    ON CONFLICT (sport_key) DO NOTHING;
    """,

    # Tags each odds row by where it came from (SportsDataIO is the primary
    # feed; The Odds API is an optional, additive secondary feed).
    "ALTER TABLE odds ADD COLUMN IF NOT EXISTS data_source TEXT NOT NULL DEFAULT 'sportsdataio';",

    # Helpful indexes
    "CREATE INDEX IF NOT EXISTS idx_games_date ON games (game_date);",
    "CREATE INDEX IF NOT EXISTS idx_odds_game ON odds (game_id);",
    "CREATE INDEX IF NOT EXISTS idx_odds_source ON odds (data_source);",
    "CREATE INDEX IF NOT EXISTS idx_predictions_game ON predictions (game_id);",
]


def get_connection():
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("ERROR: DATABASE_URL environment variable is not set.", file=sys.stderr)
        sys.exit(1)
    return psycopg2.connect(database_url, connect_timeout=10)


def run_schema():
    conn = get_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                for statement in SCHEMA_STATEMENTS:
                    cur.execute(statement)
        print("Schema created/verified successfully:")
        for table in ("leagues", "teams", "games", "odds", "predictions", "ingestion_log"):
            print(f"  - {table}")
    finally:
        conn.close()


def test_connection():
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT version(), now();")
            version, server_time = cur.fetchone()
            print("Database connectivity OK")
            print(f"  Server:   {version.split(',')[0]}")
            print(f"  Server time: {server_time}")

            cur.execute("""
                SELECT table_name FROM information_schema.tables
                WHERE table_schema = 'public'
                ORDER BY table_name;
            """)
            tables = [row[0] for row in cur.fetchall()]
            print(f"  Tables found ({len(tables)}): {', '.join(tables) if tables else 'none yet — run without --test first'}")
    finally:
        conn.close()
    print(f"  Check completed at {datetime.now(timezone.utc).isoformat()}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Create/verify betting model database schema.")
    parser.add_argument("--test", action="store_true", help="Only test connectivity, skip DDL.")
    args = parser.parse_args()

    if args.test:
        test_connection()
    else:
        run_schema()
