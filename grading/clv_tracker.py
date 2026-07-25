"""
Simple CLV (Closing Line Value) tracker.

Log every bet you place into data/clv_log.csv with your odds at bet time.
Later, fill in the closing_odds column once the game goes off the board.
Run this script anytime to see your CLV% per bet and running average.

CSV columns: date,league,matchup,market,bet_odds,closing_odds

CLV% formula (American odds converted to implied prob first):
  CLV% = (your_implied_prob_inverse - closing_implied_prob_inverse)
  Simplified here using decimal odds ratio, which is the standard shortcut:
  CLV% = (closing_decimal_odds / your_decimal_odds - 1) * 100
"""
import csv
import os

LOG_PATH = "data/clv_log.csv"
FIELDS = ["date", "league", "matchup", "market", "bet_odds", "closing_odds"]


def american_to_decimal(odds):
    odds = float(odds)
    if odds < 0:
        return 1 + (100 / -odds)
    return 1 + (odds / 100)


def ensure_log_exists():
    if not os.path.exists(LOG_PATH):
        with open(LOG_PATH, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=FIELDS)
            writer.writeheader()


def log_bet(date, league, matchup, market, bet_odds, closing_odds=""):
    ensure_log_exists()
    with open(LOG_PATH, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writerow({
            "date": date, "league": league, "matchup": matchup,
            "market": market, "bet_odds": bet_odds, "closing_odds": closing_odds,
        })


def compute_clv_report():
    ensure_log_exists()
    with open(LOG_PATH) as f:
        rows = list(csv.DictReader(f))

    clv_values = []
    print(f"{'Date':<12}{'League':<8}{'Matchup':<28}{'Bet Odds':<10}{'Close Odds':<11}{'CLV%':<8}")
    for row in rows:
        if not row["closing_odds"]:
            continue
        bet_dec = american_to_decimal(row["bet_odds"])
        close_dec = american_to_decimal(row["closing_odds"])
        clv_pct = round((close_dec / bet_dec - 1) * 100, 2)
        clv_values.append(clv_pct)
        print(f"{row['date']:<12}{row['league']:<8}{row['matchup'][:26]:<28}{row['bet_odds']:<10}{row['closing_odds']:<11}{clv_pct:<8}")

    if clv_values:
        avg_clv = round(sum(clv_values) / len(clv_values), 2)
        print(f"\nBets with closing line recorded: {len(clv_values)}")
        print(f"Average CLV%: {avg_clv}")
        if len(clv_values) < 50:
            print("NOTE: fewer than 50 bets -- treat this average as very preliminary.")
    else:
        print("No bets with closing odds recorded yet.")


if __name__ == "__main__":
    compute_clv_report()
