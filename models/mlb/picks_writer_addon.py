
# ── Add at bottom of main() in daily_runner.py ──────────────────
import json, os

def write_picks_json(ev_bets, today_str, state):
    """Write today's picks to picks.json for the mobile app."""
    output = {
        "generated_at":  today_str + "T14:00:00",
        "date_display":  today_str,
        "bankroll":      round(state["bankroll"], 2),
        "sport":         "MLB",
        "picks": []
    }
    for b in ev_bets:
        output["picks"].append({
            "home_team":    b["home_team"],
            "away_team":    b["away_team"],
            "bet_side":     b["bet_side"],
            "bet_team":     b["bet_team"],
            "edge_pct":     b["edge_pct"],
            "model_prob":   b["model_prob"],
            "market_prob":  b["market_prob"],
            "best_odds":    b["best_odds"],
            "best_book":    b["best_book"],
            "kelly_bet":    b["kelly_bet_$"],
            "home_pitcher": b.get("home_pitcher", ""),
            "away_pitcher": b.get("away_pitcher", ""),
            "venue":        b.get("venue", ""),
            "tier":         "high" if b["edge_pct"] >= 5 else "medium" if b["edge_pct"] >= 3.5 else "low"
        })

    # Write to app folder — GitHub Actions commits this automatically
    os.makedirs("app", exist_ok=True)
    with open("app/picks.json", "w") as f:
        json.dump(output, f, indent=2)
    print(f"picks.json written with {len(ev_bets)} picks.")

# Call it at the end of main():
write_picks_json(ev_bets, today_str, state)
