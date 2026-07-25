# Betting Model — Data Pipeline + Dashboard

Automated ingestion of SportsDataIO/The Odds API games and odds into a
PostgreSQL database, an MLB prediction/pick-generation model, and the
Betbot dashboard front-end — all merged into this repo. See
`MERGE_NOTES.md` for what moved from the old `Betbot-` repo and what still
needs follow-up.

See `DEPLOYMENT_GUIDE.md` for the original ingestion setup walkthrough.

## Layout

| Path | Purpose |
|---|---|
| `ingest/main.py` | Pulls games/odds from SportsDataIO (primary) and, if `ODDS_API_KEY` is set, supplementary odds from The Odds API; upserts into Postgres |
| `ingest/fetch_nfl.py`, `ingest/fetch_mlb.py`, `ingest/fetch_injuries.py`, `ingest/fetch_moneyline_signals.py` | Supplemental fetchers (from Betbot-) that write JSON snapshots under `data/` |
| `db/database_setup.py` | Creates the Postgres schema; also runs a `--test` connectivity check |
| `models/mlb/` | MLB prediction model (features, Elo, ensemble model, daily runner) — from Betbot- `model/` |
| `engine/build_picks.py`, `engine/kelly.py` | Pick assembly + Kelly stake sizing |
| `grading/backtest.py`, `grading/clv_tracker.py` | Backtesting and closing-line-value tracking |
| `app/` | Betbot dashboard/PWA front-end (`index.html`, `dashboard/`, `assets/`) |
| `data/picks.json` | Latest generated picks, consumed by the dashboard |
| `schemas/picks/` | Versioned JSON schema + changelog for `picks.json` |
| `.github/workflows/betting_workflow.yml` | Data ingestion workflow (manual + scheduled) |
| `.github/workflows/generate-picks.yml` | Daily picks-generation workflow (folded in from Betbot-'s `daily-picks.yml`) |
| `requirements.txt` | Ingestion dependencies |
| `models/mlb/requirements.txt` | Model/prediction dependencies |
| `.env.example` | Template for local environment variables (never commit a real `.env`) |

## Local test

```bash
cp .env.example .env      # fill in real values
pip install -r requirements.txt
python db/database_setup.py          # create tables
python ingest/main.py --sport nfl --date 2026-09-07
python db/database_setup.py --test   # confirm rows landed

# Picks model (run from repo root so its state files resolve correctly)
pip install -r models/mlb/requirements.txt
python models/mlb/daily_runner.py
```

## Required repo secrets

| Secret | Used by |
|---|---|
| `DATABASE_URL` | `betting_workflow.yml` (ingestion) |
| `SPORTSDATAIO_API_KEY` | `betting_workflow.yml` (ingestion) |
| `ODDS_API_KEY` | `betting_workflow.yml` (optional secondary odds) and `generate-picks.yml` (picks generation) |

Run `python audit_workflow_secrets.py --repo lherrera31820-hub/betting-model`
(see the separate secrets-audit script) to verify these are all configured
before relying on either workflow.
