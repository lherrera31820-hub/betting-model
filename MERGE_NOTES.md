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

1. **Repo secrets — still open.** `betting-model` currently has zero repo
   secrets configured. `DATABASE_URL`, `SPORTSDATAIO_API_KEY`, and
   `ODDS_API_KEY` all need to be added before either workflow will actually
   run successfully. This can't be done on the user's behalf (secret values
   are never readable, even from Betbot-'s existing ones) — run these
   yourself:
   ```bash
   gh secret set DATABASE_URL -R lherrera31820-hub/betting-model
   gh secret set SPORTSDATAIO_API_KEY -R lherrera31820-hub/betting-model
   gh secret set ODDS_API_KEY -R lherrera31820-hub/betting-model
   ```
   Run `audit_workflow_secrets.py` against this repo to re-check at any time.
2. **Pages hosting decision — RESOLVED (2026-07-25).** Chose to move hosting
   into `betting-model` and retire `Betbot-`'s Pages site. What changed:
   - `betting-model` was switched from **private to public** (required for
     free-tier Pages).
   - `deploy-pages.yml` and `monitor-pages.yml` were copied here from
     `Betbot-` and adapted: the build step now flattens `app/index.html` +
     `app/dashboard/index.html` + icons/manifest/service-worker to the site
     root and copies `data/picks.json` alongside them (backend code —
     `models/`, `ingest/`, `engine/`, `grading/`, `db/`, `*.pkl`,
     `training_data.csv` — is intentionally excluded from the published
     Pages artifact even though the repo itself is public).
   - `deploy-pages.yml` now triggers on pushes to `app/**` or
     `data/picks.json`, so `generate-picks.yml`'s daily commit to
     `data/picks.json` automatically triggers a redeploy — no cross-repo
     token needed.
   - `app/dashboard/index.html`'s hardcoded `OWNER`/`REPO` and issue/repo
     links were repointed from `Betbot-` to `betting-model`.
   - `scripts/configure-pages-environment.sh` was copied over; run it once
     to enable Pages with `build_type=workflow` on this repo:
     ```bash
     ./scripts/configure-pages-environment.sh lherrera31820-hub betting-model
     ```
   - `Betbot-` was left untouched (its own `deploy-pages.yml` will keep
     running against its own stale `picks.json` until it's retired per
     item 5 below — consider disabling it once this repo's site is verified
     live).
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

## NFL ingestion bug (2026-07-25)

While double-checking the MLB endpoint-name fix against NFL, found a second, unrelated bug:
NFL's `ScoresByDate` response has no bare `GameID` field (only `ScoreID` and `GlobalGameID`),
so `upsert_games()`'s `GameID`/`GameId` lookup evaluated to `None`, tripping the `game_id NOT NULL`
constraint. Fixed by adding `GlobalGameID`/`ScoreID` as fallbacks (MLB/NBA behavior is unchanged
since their responses have `GameID`).

Also found: SportsDataIO's `GameOddsByDate` returns HTTP 404 (not an empty array) for dates with
no odds data (off-season, or dates outside the odds archive window). `fetch_json()` now has a
`tolerate_404` flag; the odds fetch uses it so this no longer crashes the whole run — it just
upserts 0 odds rows for that date.

Verified: NFL `2025-09-07` → 13 rows upserted (odds not available for this old date, tolerated).
MLB `2026-07-25` (today) → still 285 rows upserted, confirming no regression.

Untested: NCAAF/NCAAB may have similar field-name quirks — worth checking before relying on them.

## NCAAF/NCAAB field-name check (2026-07-25)

Checked the two remaining untested sports per the earlier follow-up note:

- **NCAAF**: found the games-by-date endpoint name was wrong. The `GAMES_ENDPOINT_NAME`
  mapping had `ncaaf: "ScoresByDate"` (an unconfirmed guess, pattern-matched from NFL) but
  SportsDataIO's CFB API actually uses `GamesByDate` for this -- `ScoresByDate` 404s.
  Fixed the mapping. Once using the right endpoint, NCAAF's game objects DO have a normal
  `GameID` field (no field-mapping bug like NFL's), so the existing `GameID`/`GlobalGameID`
  fallback chain in `upsert_games()` handles it fine as-is.
  Verified: NCAAF `2025-09-06` -> 79 rows upserted successfully.

- **NCAAB**: blocked before even reaching the games/field-name question -- the teams endpoint
  itself returns `HTTP 401 Unauthorized: "You are not authorized to access this endpoint.
  Please contact sales@sportsdata.io for authorization."` This is a subscription/plan
  limitation on the `SPORTSDATAIO_API_KEY`, not a code bug. College basketball (CBB) access
  needs to be added to the SportsDataIO plan before this sport can be ingested at all.

