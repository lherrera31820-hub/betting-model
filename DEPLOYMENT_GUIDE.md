# Automated Sports Betting Model — Deployment Guide

This guide covers the complete file manifest, GitHub configuration, and
step-by-step deployment for a pipeline that automatically pulls games and
odds data from [SportsDataIO](https://sportsdata.io) and writes it into a
PostgreSQL database on a schedule (or on demand) using GitHub Actions.

---

## 1. Project file manifest

```
betting-model/
├── .env.example                      # template for local env vars (never commit real .env)
├── .gitignore                        # excludes .env, __pycache__, logs, etc.
├── requirements.txt                  # Python dependencies
├── main.py                           # SportsDataIO → PostgreSQL ingestion script
├── database_setup.py                 # creates schema + connectivity test (--test)
├── README.md                         # quick reference
├── DEPLOYMENT_GUIDE.md               # this file
└── .github/
    └── workflows/
        └── betting_workflow.yml      # workflow_dispatch + scheduled Actions pipeline
```

**What each file does:**

- **`requirements.txt`** — pins `requests`, `psycopg2-binary`, `python-dotenv`.
- **`database_setup.py`** — idempotent DDL for `leagues`, `teams`, `games`, `odds`,
  `predictions`, and `ingestion_log` tables. Run with `--test` to only check
  connectivity (used as the final Actions step to confirm the database is reachable).
- **`main.py`** — accepts `--sport` (`nfl`, `mlb`, `nba`, `ncaaf`, `ncaab`) and
  `--date` (`YYYY-MM-DD`), calls SportsDataIO's `teams`, `ScoresByDate`, and
  `GameOddsByDate` endpoints, and upserts results into Postgres. Every run
  writes a row to `ingestion_log` (success/failure + row counts), so you can
  always confirm a run actually landed data.
- **`.github/workflows/betting_workflow.yml`** — runs the two scripts above in
  CI, triggered manually (`workflow_dispatch`, with a sport/date picker) or
  daily on a cron schedule.

---

## 2. Prerequisites

1. A GitHub account with a new (or existing) repository for this project.
2. A PostgreSQL database reachable from the public internet, with its
   connection string in the form:
   `postgresql://user:password@host:5432/dbname`
   (Any managed Postgres works — Neon, Supabase, Railway, RDS, etc. Free tiers
   on Neon or Supabase are sufficient for this workload.)
3. A SportsDataIO API key from your [SportsDataIO account dashboard](https://sportsdata.io/member/subscriptions).
4. Git and Python 3.11+ installed locally (only needed if you want to test
   before pushing — GitHub Actions installs its own Python).

---

## 3. Initialize the repository

From the folder containing the files above:

```bash
cd betting-model
git init
git add .
git commit -m "Initial commit: betting model data pipeline"
```

Create the remote repository (pick one):

**Option A — GitHub CLI:**
```bash
gh repo create betting-model --private --source=. --remote=origin
git push -u origin main
```

**Option B — GitHub web UI:**
1. Go to [github.com/new](https://github.com/new).
2. Name the repository (e.g. `betting-model`), set it to **Private**, and
   do **not** initialize with a README (you already have one locally).
3. Click **Create repository**, then run the commands GitHub shows you:
   ```bash
   git remote add origin https://github.com/<your-username>/betting-model.git
   git branch -M main
   git push -u origin main
   ```

Confirm the push worked by refreshing the repository page — you should see
all files, including the `.github/workflows/` folder.

---

## 4. Configure GitHub repository secrets

The workflow reads credentials from encrypted repository secrets — never
hard-code API keys or database URLs in the YAML or Python files.

1. In your repository, go to **Settings** → **Secrets and variables** → **Actions**.
2. Click **New repository secret** and add each of the following (name must
   match exactly, since `main.py` and the workflow reference these names):

| Secret name | Value |
|---|---|
| `DATABASE_URL` | Full Postgres connection string, e.g. `postgresql://user:pass@host:5432/dbname` |
| `SPORTSDATAIO_API_KEY` | Your SportsDataIO subscription key (primary data source) |
| `ODDS_API_KEY` | Your [The Odds API](https://the-odds-api.com) key (optional, supplementary odds source — the workflow runs fine without it, it just skips that step) |

3. Click **Add secret** after each one. Secrets are write-only after saving —
   you can update them later but not view the value again.
4. (Optional) If you want to restrict who can trigger the workflow manually,
   go to **Settings** → **Actions** → **General** and review the
   "Who can approve workflow runs" / branch protection settings.

---

## 5. The workflow_dispatch YAML

This is already saved at `.github/workflows/betting_workflow.yml` in the
manifest above. Key points:

- **`workflow_dispatch`** exposes a manual "Run workflow" button in the
  Actions tab, with a `sport` dropdown (`nfl`, `mlb`, `nba`, `ncaaf`, `ncaab`)
  and an optional `date` text input (defaults to today if left blank).
- **`schedule`** runs the same job automatically once a day at `13:00 UTC`
  (8:00 AM CDT / 7:00 AM CST). GitHub Actions cron does not adjust for
  daylight saving on its own — shift the hour by one in November/March if you
  want a fixed local time.
- Both trigger paths run the same three steps: install dependencies,
  create/verify the database schema, run the ingestion, then run a final
  `database_setup.py --test` connectivity check so every run's logs end with
  a clear pass/fail signal.

```yaml
on:
  workflow_dispatch:
    inputs:
      sport:
        description: "Sport to ingest"
        required: true
        default: "nfl"
        type: choice
        options: [nfl, mlb, nba, ncaaf, ncaab]
      date:
        description: "Date to ingest (YYYY-MM-DD). Leave blank for today."
        required: false
        default: ""
  schedule:
    - cron: "0 13 * * *"
```

---

## 6. Trigger the first workflow run

**Option A — GitHub web UI:**
1. Go to the **Actions** tab of your repository.
2. Click **Betting Model Data Pipeline** in the left sidebar (GitHub may ask
   you to confirm you want Actions enabled the first time — click **I
   understand my workflows, go ahead and enable them**).
3. Click **Run workflow**, choose a sport (e.g. `nfl`), leave `date` blank,
   and click the green **Run workflow** button.
4. Refresh after a few seconds — a new run will appear. Click into it to
   watch live logs for each step.

**Option B — GitHub CLI:**
```bash
gh workflow run betting_workflow.yml -f sport=nfl -f date=
gh run watch
```

---

## 7. Confirm database connectivity

The workflow's last step runs `python database_setup.py --test`, which
prints:
- The Postgres server version and current server time.
- The list of tables found in the `public` schema.

In the Actions run logs, look for a step called **Confirm database
connectivity** — if it prints table names (`games`, `odds`, `teams`,
`predictions`, `ingestion_log`, `leagues`) and no traceback, the pipeline is
correctly reaching your database.

You can double check independently with any Postgres client:
```sql
SELECT sport_key, run_date, rows_upserted, status, finished_at
FROM ingestion_log
ORDER BY finished_at DESC
LIMIT 5;
```
A `success` row with `rows_upserted > 0` confirms both the SportsDataIO
fetch and the database write worked end to end.

To see the split between sources once both are wired up:
```sql
SELECT data_source, count(*) FROM odds GROUP BY data_source;
```

---

## 8. Troubleshooting

| Symptom | Likely cause |
|---|---|
| `ERROR: DATABASE_URL environment variable is not set` | Secret name typo, or secret not added at the repo level |
| `HTTP 401` from SportsDataIO | Wrong or expired `SPORTSDATAIO_API_KEY`, or the key doesn't have access to that sport's package |
| Workflow runs but `rows_upserted = 0` | No games scheduled for that date/sport — try a date during an active season |
| `psycopg2.OperationalError: connection timed out` | Database provider is blocking GitHub's IP ranges — check your provider's network/firewall rules and allow all IPs (`0.0.0.0/0`) or use a provider with a pooled/proxy connection string |

---

## 9. Security notes

- Never commit `.env` — it's already excluded via `.gitignore`.
- Rotate your `SPORTSDATAIO_API_KEY` and `DATABASE_URL` password periodically,
  updating the corresponding GitHub secret each time.
- Keep the repository **private** since it will reference your data
  provider and database in commit history/logs.
