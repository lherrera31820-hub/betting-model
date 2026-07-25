"""
Fetches NFL player props from MoneyLine API using the actual nested response shape.
Requires ML_API_KEY environment variable.
"""
import json
import os
import urllib.request

API_KEY = os.environ.get("ML_API_KEY", "")
BASE_URL = "https://mlapi.bet/v1"


def fetch_json(url):
    req = urllib.request.Request(url, headers={"x-api-key": API_KEY, "accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


def main():
    payload = {"events": [], "props": []}
    if not API_KEY:
        print("ML_API_KEY not set -- writing empty placeholder file.")
        with open("data/nfl_raw.json", "w") as f:
            json.dump(payload, f, indent=2)
        return

    try:
        props_raw = fetch_json(f"{BASE_URL}/player-props?league=nfl")
        for event in props_raw.get("data", []):
            payload["events"].append({
                "event_id": event.get("eventId"),
                "league": event.get("leagueId", "nfl"),
                "home_team": event.get("homeTeamName"),
                "away_team": event.get("awayTeamName"),
                "start_time": event.get("startTime"),
                "status": None,
            })
            for player in event.get("players", []):
                for market in player.get("markets", []):
                    for line in market.get("lines", []):
                        for offer in line.get("offers", []):
                            payload["props"].append({
                                "event_id": event.get("eventId"),
                                "player_id": player.get("playerId"),
                                "player_name": player.get("playerName"),
                                "team_abbr": player.get("teamAbbr"),
                                "market_type": market.get("marketType"),
                                "market_name": market.get("marketName"),
                                "format": market.get("format"),
                                "line": line.get("point"),
                                "selection": offer.get("selection"),
                                "price": offer.get("price"),
                                "implied_probability": offer.get("impliedProbability"),
                                "bookmaker_id": offer.get("bookmakerId"),
                                "bookmaker_name": offer.get("bookmakerName"),
                                "is_best": offer.get("isBest"),
                                "last_update": offer.get("lastUpdate"),
                            })
    except Exception as exc:
        print(f"MoneyLine props fetch failed: {exc}")

    with open("data/nfl_raw.json", "w") as f:
        json.dump(payload, f, indent=2)

    print(f"NFL events: {len(payload['events'])}, props: {len(payload['props'])}")


if __name__ == "__main__":
    main()
