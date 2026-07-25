"""
Fetches MoneyLine's /v1/edge feed (confirmed working endpoint) which includes
real modelProb, ev%, and bookmaker odds per outcome. This is the real model
signal source used to replace the placeholder probability logic.

As of this integration (July, NFL off-season) MoneyLine's edge feed is
returning MLB and soccer events -- NFL edge signals will populate once
the NFL season's markets are active. The fetch covers both leagues so
picks.json is powered by real signals wherever they exist, and falls back
to "no_model" (never a fake number) wherever they don't.
"""
import json
import os
import urllib.request

API_KEY = os.environ.get("ML_API_KEY", "")
BASE_URL = "https://mlapi.bet/v1"
TRACKED_LEAGUES = {"nfl", "mlb"}


def fetch_json(url):
    req = urllib.request.Request(url, headers={"x-api-key": API_KEY, "accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


def main():
    signals = []
    if not API_KEY:
        print("ML_API_KEY not set -- skipping MoneyLine signals fetch.")
    else:
        try:
            raw = fetch_json(f"{BASE_URL}/edge?type=ev&limit=500")
            for event in raw.get("data", []):
                league = event.get("leagueId")
                if league not in TRACKED_LEAGUES:
                    continue
                for edge in event.get("edges", []):
                    if edge.get("type") != "ev":
                        continue
                    ev_bet = edge.get("evBet", {})
                    signals.append({
                        "league": league,
                        "event_id": event.get("eventId"),
                        "market": edge.get("market"),
                        "outcome": edge.get("outcome"),
                        "description": edge.get("description"),
                        "point": edge.get("point"),
                        "bookmaker_id": ev_bet.get("bookmakerId"),
                        "odds": ev_bet.get("odds"),
                        "model_prob": ev_bet.get("modelProb"),
                        "ev_pct": ev_bet.get("ev"),
                    })
        except Exception as exc:
            print(f"MoneyLine edge fetch failed: {exc}")

    with open("data/moneyline_signals.json", "w") as f:
        json.dump({"edge_signals": signals}, f, indent=2)

    by_league = {}
    for s in signals:
        by_league[s["league"]] = by_league.get(s["league"], 0) + 1
    print(f"MoneyLine edge signals by league: {by_league} (total {len(signals)})")


if __name__ == "__main__":
    main()
