# Merge notes: Betbot- → betting-model

This documents the merge of the `Betbot-` repo into `betting-model`, following
the previously agreed plan: use `betting-model` as the permanent base (cleaner
backend/ingestion + database), and fold the Betbot dashboard, prediction
model, and grading logic into it.

`Betbot-` itself was **not modified or deleted** — this is a copy-in, so
everything there keeps working exactly as before while this new structure
gets verified.

## What moved (Phase 1 + 2, done in this branch)

| From `Betbot-` | To `betting-model` |
|---|---|
| `model/` (whole folder: model.py, features.py, features_phase2.py, bet_types.py, config.py, odds.py, alerts.py, tracker.py, train_models.py, daily_runner.py, picks_writer_addon.py, tests, raw_schedule_2026.json, training_data.csv, BACKTEST_RESULTS.md) | `models/mlb/` (copied as-is; internal same-directory imports still work unchanged) |
| `daily_state.pkl`, `elo_state.pkl`, `model_state.pkl` | repo root (kept at root because `models/mlb/model.py` and `models/mlb/train_models.py` resolve these relative to the repo root, and `daily_runner.py` resolves `daily_state.pkl` relative to the working directory the workflow runs from) |
| `app/index.html`, `app/icon-*.png`, `app/manifest.webmanifest`, `app/service-worker.js` | `app/index.html`, `app/assets/*` |
| `dashboard/index.html` | `app/dashboard/index.html` |
| `data/picks.json` | `data/picks.json` |
| `schemas/picks/` | `schemas/picks/` |
| `fetch_mlb.py`, `fetch_nfl.py`, `fetch_injuries.py`, `fetch_moneyline_signals.py` | `ingest/` |
| `build_picks.py`, `kelly.py` | `engine/` |
| `clv_tracker.py`, `backtest.py` | `grading/` |
| `daily-picks.yml` | `.github/workflows/generate-picks.yml` (paths updated to `models/mlb/daily_runner.py`; the cross-repo "trigger Pages redeploy" step was removed — see "Still pending" below) |

`betting-model`'s own files were also relocated for a clean layout:
`main.py` → `ingest/main.py`, `database_setup.py` → `db/database_setup.py`.
`betting_workflow.yml` was updated to match.

## Known pre-existing inconsistency (not introduced by this merge)

In `Betbot-`, `fetch_mlb.py` / `fetch_nfl.py` / `fetch_injuries.py` /
`fetch_moneyline_signals.py` / `build_picks.py` write to `data/<file>.json`
(relative to cwd), but the repo root also had stale top-level copies of
`mlb_raw.json`, `nfl_raw.json`, `injuries_lineups.json`, `moneyline_signals.json`
left over from before the `data/` folder existed. Those stale root copies were
**not** carried over — the scripts will simply regenerate fresh `data/*.json`
files on their next run in this repo.

## Still pending (Phase 3–5 — not done automatically)

1. **Repo secrets.** `betting-model` currently has zero repo secrets configured.
   `DATABASE_URL`, `SPORTSDATAIO_API_KEY`, and `ODDS_API_KEY` all need to be
   added (`gh secret set NAME -R lherrera31820-hub/betting-model`) before
   either workflow will actually run successfully. Run
   `audit_workflow_secrets.py` against this repo to re-check at any time.
2. **Pages hosting decision.** `Betbot-` still owns the live GitHub Pages
   deployment (`deploy-pages.yml` + `monitor-pages.yml`), and those were
   intentionally **not** moved here yet. `generate-picks.yml` in this repo
   only commits a new `data/picks.json` to `betting-model` — it does not
   publish anything publicly. Decide one of:
   - Keep `Betbot-` as the Pages front-end and have it pull `picks.json`
     from `betting-model` (needs a cross-repo token secret), or
   - Move `deploy-pages.yml`/`monitor-pages.yml` here too and retire
     `Betbot-` Pages once this repo's dashboard is verified working
     end-to-end (this repo is currently **private**, so Pages would need
     to be made public or use a paid Pages plan).
3. **Standardize prediction schema across sports.** The plan's Phase 3 calls
   for one canonical prediction schema shared by every sport model and one
   canonical event-ID scheme for grading. Only the MLB model exists today
   (`models/mlb/`); NFL/props models referenced in the target tree
   (`models/nfl/`, `models/props/`) don't exist yet.
4. **Calibration/simulation/validation layers.** The target tree also calls
   for `calibration/`, `simulation/`, and `validation/` folders (Brier score,
   calibration slope, Monte Carlo, etc.) — none of that code exists in either
   repo yet, so those folders were intentionally not created empty.
5. **Retire `Betbot-`.** Only do this once `betting-model`'s ingestion +
   picks generation + dashboard have been run end-to-end successfully here.

## Recommended next command sequence

```bash
gh secret set DATABASE_URL -R lherrera31820-hub/betting-model
gh secret set SPORTSDATAIO_API_KEY -R lherrera31820-hub/betting-model
gh secret set ODDS_API_KEY -R lherrera31820-hub/betting-model
gh workflow run betting_workflow.yml -R lherrera31820-hub/betting-model
gh workflow run generate-picks.yml -R lherrera31820-hub/betting-model
```
