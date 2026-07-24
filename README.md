# Betting Model — Data Pipeline

Automated ingestion of SportsDataIO games and odds into a PostgreSQL database,
scheduled and manually triggerable via GitHub Actions.

See `DEPLOYMENT_GUIDE.md` for the full setup walkthrough.

## Quick reference

| File | Purpose |
|---|---|
| `requirements.txt` | Python dependencies |
| `main.py` | Pulls games/odds from SportsDataIO, upserts into Postgres |
| `database_setup.py` | Creates schema; also runs a `--test` connectivity check |
| `.github/workflows/betting_workflow.yml` | GitHub Actions workflow (manual + scheduled) |
| `.env.example` | Template for local environment variables (never commit real `.env`) |

## Local test

```bash
cp .env.example .env      # fill in real values
pip install -r requirements.txt
python database_setup.py          # create tables
python main.py --sport nfl --date 2026-09-07
python database_setup.py --test   # confirm rows landed
```
