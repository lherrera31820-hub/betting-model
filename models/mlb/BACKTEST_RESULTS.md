# Backtest Results — Real Ensemble (team-form features)

## Model

Ensemble of **logistic regression + gradient boosting + random forest**, blended by simple average of the three predicted P(home win) probabilities. The logistic-regression input is standardized; the tree models use raw features.

## Dataset

- Source: MLB Stats API `schedule` endpoint (regular season, 2026).
- Completed games parsed from bulk fetch: **1445**
- Modeling rows after requiring >= 15 prior games for both teams: **1218**
- Full date range used: **2026-04-12 .. 2026-07-12**
- Total external HTTP/API calls for the entire task: **5** (one bulk `schedule` call per month; zero per-game calls).

## Time-based split (chronological, never random)

- Train: **974** games, 2026-04-12 .. 2026-06-25 (earliest ~80%)
- Held-out test: **244** games, 2026-06-25 .. 2026-07-12 (most recent ~20%)

## Held-out metrics

- Accuracy: **0.5410**
- Log loss: **0.6925**
- Brier score: **0.2495**
- Test-set home-win base rate: 0.4918 (a naive always-home baseline would score this accuracy)

## Calibration table (held-out)

| Predicted bucket | N | Mean predicted | Actual home-win rate |
|---|---|---|---|
| 0.0–0.1 | 0 | — | — |
| 0.1–0.2 | 0 | — | — |
| 0.2–0.3 | 0 | — | — |
| 0.3–0.4 | 7 | 0.387 | 0.429 |
| 0.4–0.5 | 81 | 0.463 | 0.432 |
| 0.5–0.6 | 120 | 0.544 | 0.533 |
| 0.6–0.7 | 32 | 0.635 | 0.500 |
| 0.7–0.8 | 4 | 0.719 | 0.500 |
| 0.8–0.9 | 0 | — | — |
| 0.9–1.0 | 0 | — | — |

## Honest limitations

**Team-form features only.** This version deliberately trains on team-form / situational features derived entirely from the bulk schedule + final scores: trailing-15-game win%, average runs scored/allowed, run-differential differential, home/away park factor, and rest-day differential. It does **not** include starting-pitcher ERA/FIP/WHIP or bullpen ERA. Those require a separate MLB Stats API call per game per pitcher, which is exactly what caused a prior attempt at this task to time out after ~3 hours with zero commits. Pitcher-level detail is a documented future improvement, not part of this artifact.

## Why no historical ROI is reported

No historical closing-odds archive is available, so there is no honest way to compute a historical betting ROI: ROI requires the market price you would actually have gotten at bet time, and fabricating or back-filling those odds would produce a misleading number. This deliverable therefore reports only genuine held-out **classifier** metrics on real game outcomes. Real ROI and CLV will accrue **going forward**: `model/tracker.py` logs each live pick with the odds actually taken (`log_bets`), settles it against the real result (`update_results`), and reports cumulative ROI/CLV (`get_performance_stats`) into `data/picks.json` on every daily run.
