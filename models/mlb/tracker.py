"""
tracker.py — Log predictions, track results, calculate ROI and CLV.
All data stored in bets_log.csv — auto-created if missing.
"""

import pandas as pd
import os
from datetime import datetime

LOG_FILE = 'bets_log.csv'

COLUMNS = [
    'date','home_team','away_team','bet_side','bet_team',
    'model_prob','market_prob','edge_pct','best_odds','best_book',
    'kelly_bet_$','result','profit_loss','closing_odds','clv','notes'
]


def load_log():
    if os.path.exists(LOG_FILE):
        return pd.read_csv(LOG_FILE)
    return pd.DataFrame(columns=COLUMNS)


def log_bets(bets, today_str):
    """Append today's bets to log. result/closing_odds filled later."""
    df = load_log()
    new_rows = []
    for b in bets:
        new_rows.append({
            'date':         today_str,
            'home_team':    b['home_team'],
            'away_team':    b['away_team'],
            'bet_side':     b['bet_side'],
            'bet_team':     b['bet_team'],
            'model_prob':   b['model_prob'],
            'market_prob':  b['market_prob'],
            'edge_pct':     b['edge_pct'],
            'best_odds':    b['best_odds'],
            'best_book':    b['best_book'],
            'kelly_bet_$':  b['kelly_bet_$'],
            'result':       'PENDING',
            'profit_loss':  0.0,
            'closing_odds': None,
            'clv':          None,
            'notes':        '',
        })
    df = pd.concat([df, pd.DataFrame(new_rows)], ignore_index=True)
    df.to_csv(LOG_FILE, index=False)
    print(f"Logged {len(new_rows)} bets to {LOG_FILE}")


def update_results(game_results):
    """
    Update pending bets with results.
    game_results: list of dicts with home_team, away_team, home_win, closing_home_odds, closing_away_odds
    """
    df = load_log()
    pending = df[df['result'] == 'PENDING']
    if pending.empty:
        return

    for _, bet in pending.iterrows():
        for gr in game_results:
            if (bet['home_team'].lower() in gr['home_team'].lower() or
                gr['home_team'].lower() in bet['home_team'].lower()):
                idx = bet.name
                home_win = gr['home_win']
                won = (bet['bet_side'] == 'HOME' and home_win == 1) or \
                      (bet['bet_side'] == 'AWAY' and home_win == 0)
                odds = bet['best_odds']
                if won:
                    pnl = (odds / 100 if odds > 0 else 100 / abs(odds)) * bet['kelly_bet_$']
                else:
                    pnl = -bet['kelly_bet_$']

                closing_odds = gr.get('closing_home_odds' if bet['bet_side']=='HOME' else 'closing_away_odds')
                clv = None
                if closing_odds:
                    clv = round(bet['best_odds'] - closing_odds, 1)

                df.at[idx, 'result']       = 'WIN' if won else 'LOSS'
                df.at[idx, 'profit_loss']  = round(pnl, 2)
                df.at[idx, 'closing_odds'] = closing_odds
                df.at[idx, 'clv']          = clv
                break

    df.to_csv(LOG_FILE, index=False)
    print("Results updated.")


def sync_live_results(picks_data, leg_results):
    """
    Live-sync layer for the categorised picks.json produced by the generator.

    `leg_results` maps an individual leg/pick identifier to its resolved status
    ("won" | "lost" | "push" | "pending"). Each single-game pick AND each leg
    inside a parlay/teaser is updated INDEPENDENTLY from the same feed, so a
    single leg can resolve before the rest of its combination. After a leg is
    updated the combination's rolled-up status is recomputed (all legs must win;
    pushed legs drop out) via bet_types.update_leg_status.

    This extends — it does not replace — update_results(), which still settles the
    single-game CSV bet log. Returns the mutated picks_data.
    """
    from bet_types import update_leg_status, settle_combination

    # 1. Single-game picks (real-time, unchanged tracking semantics).
    for single in picks_data.get("picks", []):
        key = single.get("bet_id") or single.get("pick_id")
        if key in leg_results:
            single["status"] = leg_results[key]

    # 2. Individual legs inside combinations, each synced independently, then
    #    the parlay/teaser outcome is rolled up from its legs.
    for combo in picks_data.get("combinations", []):
        touched = False
        for leg in combo.get("legs", []):
            lid = leg.get("leg_id")
            if lid in leg_results:
                update_leg_status(combo, lid, leg_results[lid])
                touched = True
        if not touched:
            settle_combination(combo)

    return picks_data


def get_performance_stats():
    """Return ROI/CLV summary as a dict (for picks.json)."""
    df = load_log()
    settled = df[df['result'].isin(['WIN','LOSS'])]
    if settled.empty:
        return {
            'settled_bets': 0,
            'win_rate': None,
            'total_wagered': 0.0,
            'total_pnl': 0.0,
            'roi': None,
            'avg_clv': None,
        }
    total_wagered = float(settled['kelly_bet_$'].sum())
    total_pnl     = float(settled['profit_loss'].sum())
    roi           = (total_pnl / total_wagered * 100) if total_wagered > 0 else 0.0
    win_rate      = float((settled['result'] == 'WIN').mean() * 100)
    clv_data      = settled['clv'].dropna()
    avg_clv       = float(clv_data.mean()) if len(clv_data) > 0 else None
    return {
        'settled_bets': int(len(settled)),
        'win_rate': round(win_rate, 1),
        'total_wagered': round(total_wagered, 2),
        'total_pnl': round(total_pnl, 2),
        'roi': round(roi, 2),
        'avg_clv': round(avg_clv, 1) if avg_clv is not None else None,
    }


def print_summary():
    """Print ROI and CLV summary."""
    df = load_log()
    settled = df[df['result'].isin(['WIN','LOSS'])]
    if settled.empty:
        print("No settled bets yet.")
        return
    total_wagered  = settled['kelly_bet_$'].sum()
    total_pnl      = settled['profit_loss'].sum()
    roi            = (total_pnl / total_wagered * 100) if total_wagered > 0 else 0
    win_rate       = (settled['result'] == 'WIN').mean() * 100
    clv_data       = settled['clv'].dropna()
    avg_clv        = clv_data.mean() if len(clv_data) > 0 else None

    print(f"\n===== BETTING MODEL PERFORMANCE =====")
    print(f"Settled bets:   {len(settled)}")
    print(f"Win rate:       {win_rate:.1f}%")
    print(f"Total wagered:  ${total_wagered:.2f}")
    print(f"Total P&L:      ${total_pnl:+.2f}")
    print(f"ROI:            {roi:+.2f}%")
    if avg_clv is not None:
        print(f"Avg CLV:        {avg_clv:+.1f} points")
    print(f"======================================\n")
