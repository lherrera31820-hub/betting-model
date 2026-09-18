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
| `prediction_core/` | Prediction Core v1 — calibrated probs, market join, locked CLEAR gates, candidate JSON for Main |
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

## Prediction Core v1 (MLB / NFL / CFB)

Start-over prediction layer for Luis Herrera’s sports desk. Soccer is **OFF**.
Main is the **sole CLEAR writer** — this package only emits candidate JSON.
Luis scores **side pass/fail** first; CLV is stamped internally for promote/kill.

Never invents MODEL%, lines, injuries, or stats. If a feed is missing the
ticket is HOLD/FILL, never CLEAR.

### How to run

```bash
pip install -r requirements-dev.txt   # pytest
# from repo root
python -m prediction_core.cli predict-slate \
  --slate tests/fixtures/slate.json \
  --out prediction_core/fixtures/candidates.json

python -m pytest
```

The CLI prints CLEAR / FILL / HOLD plus reject codes (no network).

### Candidate JSON schema (`prediction_core.candidates.v1`)

| Field | Meaning |
|---|---|
| `writer` | Always `Main` |
| `scoreboard` | `side_pass_fail` |
| `clv_policy` | `internal_promote_kill` |
| `daily.cap_u` | Soft CLEAR cap (~3–5u, default 5) |
| `candidates[].status` | `CLEAR` / `FILL` / `HOLD` |
| `candidates[].model_pct` | Stamped Sim % or computed from supplied inputs — never guessed |
| `candidates[].edge` | Prob points (ML/F5) or spread points (NFL/CFB) |
| `candidates[].reject_codes` | Locked `REJECT_*` tags |
| `candidates[].prices` | Open / best / close stamps (close stays null if not supplied) |
| `candidates[].clv` | Internal only; `UNAVAILABLE` when close is missing |

Slate input shape is documented in `prediction_core/pipeline.py`.

### Locked CLEAR bars (Sep 15)

- **CFB** dogs-first. Favorites (especially home / G6 / −3 −7 −10) need edge ≥ ~5 pts **and** model ≥ 70, else HOLD.
- **Daily** soft cap ~3–5u (no 12–15 sprays).
- **MLB +ML** only if model ≥ 65 **and** edge ≥ 8. Opener/PRIM → reject.
- **F5** skip American ≤ −200 unless model ≥ 75 **and** a real F5 market is stamped.

Sample output from the fixture slate: `prediction_core/fixtures/candidates.json`
(CFB dog CLEAR, soft +ML FILL, home fav CFB HOLD, plus opener / F5 juice / soccer / no-model).

### Stub vs live

| Piece | Live in v1 | Stub / caller-supplied |
|---|---|---|
| `markets.py` de-vig, edge, open/best/close join, CLV | Math | No odds fetch; stamps must be passed in |
| `calibration.py` Brier / log loss / reliability / PAVA | Math | Weekly walk-forward isotonic is an offline job |
| `models/mlb.py` Poisson / NegBin + Elo hook | Runnable sim | λ, park, SP, Savant PA, WX must be passed — hooks listed on the prediction |
| `models/nfl.py` Gaussian cover + w≤0.22 blend | Runnable | μ and σ required (no invented league σ) |
| `models/cfb.py` SP+/FEI/Massey/Elo ensemble | Equal-weight average of provided margins | Live SP+/FEI import not wired |
| `gates.py` | Deterministic Sep 15 + SOP numeric bars | Does not write the CLEAR card |
| Sport-bot Sit/SP/WX research | — | Packet strings on the slate; missing packet → no CLEAR |
