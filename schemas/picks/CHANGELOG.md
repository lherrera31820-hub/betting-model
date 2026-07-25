# Picks Schema Changelog

## 1.1.0 (Phase 2)
- Added optional `sos_raw` — first-order strength of schedule
- Added optional `sos_second_order` — RPI-style second-order SOS
- Added optional `win_pct_adjusted` — SOS-adjusted win percentage
- Added optional `common_opponents_score` — relative strength vs shared opponents
- Added optional `recent_form_weighted` — exponentially decayed recent form
- Added optional `model_prob_final` — blended final probability (model + SOS + common opp + form)
- Fully backward compatible with 1.0.0 — no required fields changed

## 1.0.0 (Phase 1)
- Initial picks schema: game bets + player props, edge %, Kelly stake, confidence tier
