"""
Fetches NFL and MLB injury/lineup context so props can be filtered around
players who are out, questionable, or have a role change.

NFL: MoneyLine API injuries endpoint (requires ML_API_KEY).
MLB: MLB Stats API roster/injury status (free, no key needed).
"""
import json
import os
import datetime
import urllib.request

API_KEY = os.environ.get("ML_API_KEY", "")
TODAY = datetime.date.today().isoformat()
ML_BASE_URL = "https://mlapi.bet/v1"


def fetch_json(url, headers=None):
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


def fetch_nfl_injuries():
    injuries = []
    if not API_KEY:
        print("ML_API_KEY not set -- skipping NFL injuries.")
        return injuries
    try:
        raw = fetch_json(
            f"{ML_BASE_URL}/injuries?league=nfl",
            headers={"x-api-key": API_KEY, "accept": "application/json"},
        )
        for row in raw.get("data", []):
            injuries.append({
                "league": "NFL",
                "player_id": row.get("playerId"),
                "player_name": row.get("playerName"),
                "team_abbr": row.get("teamAbbr"),
                "status": row.get("status"),
                "designation": row.get("designation"),
                "last_update": row.get("lastUpdate"),
            })
    except Exception as exc:
        print(f"NFL injuries fetch failed: {exc}")
    return injuries


def fetch_mlb_lineups():
    lineups = []
    try:
        url = (
            f"https://statsapi.mlb.com/api/v1/schedule"
            f"?sportId=1&date={TODAY}&hydrate=lineups,probablePitcher"
        )
        raw = fetch_json(url)
        for date_block in raw.get("dates", []):
            for game in date_block.get("games", []):
                game_id = game.get("gamePk")
                for side in ("away", "home"):
                    team = game.get("teams", {}).get(side, {})
                    lineup = game.get("lineups", {}).get(f"{side}PlayerNames", []) if game.get("lineups") else []
                    lineups.append({
                        "league": "MLB",
                        "game_id": game_id,
                        "team_side": side,
                        "team_name": team.get("team", {}).get("name"),
                        "probable_pitcher": team.get("probablePitcher", {}).get("fullName"),
                        "confirmed_lineup": lineup,
                    })
    except Exception as exc:
        print(f"MLB lineup fetch failed: {exc}")
    return lineups


def main():
    payload = {
        "date": TODAY,
        "nfl_injuries": fetch_nfl_injuries(),
        "mlb_lineups": fetch_mlb_lineups(),
    }
    with open("data/injuries_lineups.json", "w") as f:
        json.dump(payload, f, indent=2)
    print(
        f"NFL injuries: {len(payload['nfl_injuries'])}, "
        f"MLB lineup rows: {len(payload['mlb_lineups'])}"
    )


if __name__ == "__main__":
    main()
