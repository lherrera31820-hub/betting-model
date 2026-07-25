"""
Fetches today's MLB schedule + probable pitchers from the free MLB Stats API.
No API key required.
"""
import json
import datetime
import urllib.request

TODAY = datetime.date.today().isoformat()
SCHEDULE_URL = (
    f"https://statsapi.mlb.com/api/v1/schedule"
    f"?sportId=1&date={TODAY}&hydrate=probablePitcher,team,linescore"
)


def fetch_json(url):
    with urllib.request.urlopen(url, timeout=20) as resp:
        return json.loads(resp.read().decode())


def main():
    try:
        raw = fetch_json(SCHEDULE_URL)
    except Exception as exc:
        print(f"MLB fetch failed: {exc}")
        raw = {"dates": []}

    games = []
    for date_block in raw.get("dates", []):
        for game in date_block.get("games", []):
            away = game.get("teams", {}).get("away", {})
            home = game.get("teams", {}).get("home", {})
            games.append({
                "game_id": game.get("gamePk"),
                "date": TODAY,
                "away_team": away.get("team", {}).get("name"),
                "home_team": home.get("team", {}).get("name"),
                "away_probable_pitcher": away.get("probablePitcher", {}).get("fullName"),
                "home_probable_pitcher": home.get("probablePitcher", {}).get("fullName"),
                "status": game.get("status", {}).get("detailedState"),
            })

    with open("data/mlb_raw.json", "w") as f:
        json.dump({"date": TODAY, "games": games}, f, indent=2)

    print(f"MLB: wrote {len(games)} games for {TODAY}")


if __name__ == "__main__":
    main()
