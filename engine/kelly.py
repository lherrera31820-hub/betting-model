"""
Fractional Kelly stake calculator.

Usage as a library:
    from kelly import kelly_fraction, recommended_stake

Usage from CLI:
    python kelly.py --model-prob 0.58 --odds -110 --fraction 0.25 --bankroll 1000
"""
import argparse


def american_to_decimal(odds):
    odds = float(odds)
    if odds < 0:
        return 1 + (100 / -odds)
    return 1 + (odds / 100)


def kelly_fraction(model_prob, decimal_odds):
    """
    Full Kelly fraction of bankroll to stake.
    b = decimal_odds - 1 (net odds)
    q = 1 - model_prob
    f* = (b*p - q) / b
    """
    b = decimal_odds - 1
    if b <= 0:
        return 0.0
    p = model_prob
    q = 1 - p
    f_star = (b * p - q) / b
    return max(0.0, f_star)


def recommended_stake(model_prob, american_odds, bankroll, kelly_multiplier=0.25, max_stake_pct=0.03):
    """
    kelly_multiplier: 0.25 = quarter Kelly (recommended default), 0.5 = half Kelly.
    max_stake_pct: hard cap on stake as a percent of bankroll, regardless of Kelly output.
    """
    decimal_odds = american_to_decimal(american_odds)
    f_star = kelly_fraction(model_prob, decimal_odds)
    fractional = f_star * kelly_multiplier
    capped = min(fractional, max_stake_pct)
    stake = round(bankroll * capped, 2)
    return {
        "full_kelly_fraction": round(f_star, 4),
        "fractional_kelly_used": round(fractional, 4),
        "capped_fraction": round(capped, 4),
        "stake_amount": stake,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-prob", type=float, required=True)
    parser.add_argument("--odds", type=float, required=True, help="American odds, e.g. -110 or +150")
    parser.add_argument("--bankroll", type=float, required=True)
    parser.add_argument("--fraction", type=float, default=0.25, help="Kelly multiplier, default 0.25 (quarter Kelly)")
    parser.add_argument("--max-stake-pct", type=float, default=0.03, help="Hard cap on stake as pct of bankroll")
    args = parser.parse_args()

    result = recommended_stake(
        model_prob=args.model_prob,
        american_odds=args.odds,
        bankroll=args.bankroll,
        kelly_multiplier=args.fraction,
        max_stake_pct=args.max_stake_pct,
    )
    print(result)


if __name__ == "__main__":
    main()
