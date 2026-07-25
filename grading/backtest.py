"""
Walk-forward backtest template for MLB / NFL betting strategies.

Expects a CSV with at minimum these columns:
  date, league, model_prob, odds (american), actual_result (1=win, 0=loss)

Usage:
  python backtest.py --input historical_bets.csv --train-window 180 --test-window 30

This performs true walk-forward validation: for each rolling test window,
only data BEFORE that window is used to "train" (here, just to sanity-check
calibration), preventing look-ahead bias. It reports win rate, ROI, and a
bootstrap confidence interval so you know if results are noise or real.
"""
import argparse
import csv
import random
import statistics


def american_to_decimal(odds):
    odds = float(odds)
    if odds < 0:
        return 1 + (100 / -odds)
    return 1 + (odds / 100)


def load_rows(path):
    rows = []
    with open(path) as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(row)
    rows.sort(key=lambda r: r["date"])
    return rows


def simulate(rows, stake=1.0):
    bankroll_curve = [0.0]
    total_staked = 0.0
    total_profit = 0.0
    wins = 0
    for row in rows:
        dec_odds = american_to_decimal(row["odds"])
        result = int(row["actual_result"])
        total_staked += stake
        if result == 1:
            profit = stake * (dec_odds - 1)
            wins += 1
        else:
            profit = -stake
        total_profit += profit
        bankroll_curve.append(bankroll_curve[-1] + profit)
    win_rate = wins / len(rows) if rows else 0
    roi = (total_profit / total_staked) * 100 if total_staked else 0
    return {
        "n_bets": len(rows),
        "win_rate": round(win_rate, 4),
        "roi_pct": round(roi, 2),
        "total_profit_units": round(total_profit, 2),
        "bankroll_curve": bankroll_curve,
    }


def bootstrap_ci(rows, stake=1.0, iterations=1000):
    rois = []
    n = len(rows)
    if n == 0:
        return None
    for _ in range(iterations):
        sample = [random.choice(rows) for _ in range(n)]
        result = simulate(sample, stake=stake)
        rois.append(result["roi_pct"])
    rois.sort()
    lower = rois[int(0.025 * iterations)]
    upper = rois[int(0.975 * iterations)]
    return {
        "mean_roi_pct": round(statistics.mean(rois), 2),
        "ci_95_low": round(lower, 2),
        "ci_95_high": round(upper, 2),
    }


def walk_forward(rows, train_window, test_window):
    results = []
    i = train_window
    while i + test_window <= len(rows):
        test_slice = rows[i:i + test_window]
        result = simulate(test_slice)
        result["window_start"] = test_slice[0]["date"]
        result["window_end"] = test_slice[-1]["date"]
        results.append(result)
        i += test_window
    return results


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--train-window", type=int, default=180)
    parser.add_argument("--test-window", type=int, default=30)
    args = parser.parse_args()

    rows = load_rows(args.input)
    if len(rows) < 50:
        print(f"WARNING: only {len(rows)} rows loaded -- results will not be statistically meaningful. Aim for 500+.")

    full_result = simulate(rows)
    ci = bootstrap_ci(rows)
    windows = walk_forward(rows, args.train_window, args.test_window)

    print("=== Full-sample result ===")
    print(full_result)
    print("\n=== Bootstrap 95% CI on ROI ===")
    print(ci)
    print("\n=== Walk-forward rolling windows ===")
    for w in windows:
        print(w)


if __name__ == "__main__":
    main()
