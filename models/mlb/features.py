"""
features.py — All feature engineering for the MLB betting model.
Tier 1: Season pitcher stats, recent ERA (last 3 starts), bullpen ERA (last 7 days)
Tier 2: Park factors, pitcher rest days, umpire factor, team rest
Tier 3: BABIP regression signal, series game number, K/BB trend
"""

import numpy as np
import statsapi
import time

LEAGUE_AVG = {'era':4.50,'whip':1.30,'ip':50,'so':50,'bb':20,'hr':7}

PARK_FACTORS = {
    'Coors Field':115,'Great American Ball Park':108,'Globe Life Field':107,
    'Fenway Park':106,'Wrigley Field':105,'Truist Park':104,
    'Minute Maid Park':103,'Angel Stadium':102,'Guaranteed Rate Field':103,
    'Citizens Bank Park':103,'Chase Field':104,'Yankee Stadium':102,
    'Comerica Park':99,'Kauffman Stadium':98,'Target Field':99,
    'T-Mobile Park':97,'Dodger Stadium':98,'PetCo Park':96,
    'Oracle Park':95,'Tropicana Field':97,'American Family Field':100,
    'Progressive Field':100,'Nationals Park':99,'Busch Stadium':99,
    'loanDepot park':97,'Camden Yards':103,'PNC Park':98,
    'Citi Field':100,'Rogers Centre':102,
}

DOMES = ['Tropicana','Rogers','Minute Maid','American Family','Globe Life','loanDepot','Chase']


def get_park_factor(venue):
    for k, v in PARK_FACTORS.items():
        if k.lower() in venue.lower():
            return v
    return 100


def get_umpire_factor(venue):
    for d in DOMES:
        if d.lower() in venue.lower():
            return 0.0
    return 0.05  # outdoor — slight variance; replace with umpireScorecards.com data when available


def fetch_pitcher_stats(name, season=None):
    """Fetch season ERA, WHIP, FIP components from MLB Stats API."""
    try:
        res = statsapi.lookup_player(name, season=season)
        if not res:
            return None
        data = statsapi.player_stat_data(res[0]['id'], group='pitching', type='season', sportId=1)
        if data and data.get('stats'):
            s = data['stats'][0]['stats']
            return {
                'era':  float(s.get('era',  4.50)) if s.get('era',  '--') != '--' else 4.50,
                'whip': float(s.get('whip', 1.30)) if s.get('whip', '--') != '--' else 1.30,
                'ip':   float(s.get('inningsPitched', 50)) if s.get('inningsPitched', '--') != '--' else 50,
                'so':   float(s.get('strikeOuts', 50)),
                'bb':   float(s.get('baseOnBalls', 20)),
                'hr':   float(s.get('homeRunsAllowed', s.get('homeRuns', 7))),
            }
    except Exception:
        pass
    return None


def fip_calc(p):
    """Calculate FIP from pitcher stat dict."""
    if p['ip'] > 10:
        return min(max((13 * p['hr'] + 3 * p['bb'] - 2 * p['so']) / p['ip'] + 3.2, 1.5), 9.0)
    return 4.50


def get_recent_era(pitcher_name, current_date, app_log, season_stats):
    """ERA from pitcher's last 3 starts before current_date."""
    past = [g for g in app_log.get(pitcher_name, []) if g['date'] < current_date][-3:]
    if not past:
        return season_stats.get(pitcher_name, LEAGUE_AVG)['era']
    total_ra = sum(g['ra'] for g in past)
    total_ip = sum(g['ip'] for g in past)
    return (total_ra / total_ip * 9) if total_ip > 0 else season_stats.get(pitcher_name, LEAGUE_AVG)['era']


def get_bullpen_era(team_id, current_date, team_game_log, pitcher_stats_dict):
    """Estimate bullpen ERA from team's last 7 games."""
    past = [g for g in team_game_log.get(team_id, []) if g['date'] < current_date][-7:]
    if not past:
        return 4.50
    bp_runs = []
    for g in past:
        sp_era = pitcher_stats_dict.get(g['sp'], LEAGUE_AVG)['era']
        est_sp_runs = (sp_era / 9) * 5.0
        bp_runs.append(max(0, g['ra'] - est_sp_runs))
    return np.mean(bp_runs) / 4.0 * 9


def get_rolling_team_stats(team_id, team_stats_dict, n=15):
    """Rolling win%, avg runs scored/allowed over last n games."""
    if team_id not in team_stats_dict or not team_stats_dict[team_id]:
        return 0.5, 4.5, 4.5, 0
    r = team_stats_dict[team_id][-n:]
    return (
        sum(x[0] for x in r) / len(r),
        np.mean([x[1] for x in r]),
        np.mean([x[2] for x in r]),
        len(r),
    )


def build_feature_vector(game, team_stats, pitcher_stats_dict, app_log,
                          team_game_log, series_tracker, team_last_game,
                          pitcher_last_start, team_season_rs):
    """
    Build full feature vector for a single game dict.
    game keys: home_id, away_id, home_pitcher, away_pitcher, venue, date
    Returns: feature dict
    """
    d = game['date']
    hid, aid = game['home_id'], game['away_id']
    hp_name, ap_name = game['home_pitcher'], game['away_pitcher']

    # Rolling team stats
    hwp, hrs, hra, hg = get_rolling_team_stats(hid, team_stats)
    awp, ars, ara, ag = get_rolling_team_stats(aid, team_stats)

    # Pitcher season stats
    hp = pitcher_stats_dict.get(hp_name, LEAGUE_AVG)
    ap_s = pitcher_stats_dict.get(ap_name, LEAGUE_AVG)

    # Recent ERA (last 3 starts)
    h_recent_era = get_recent_era(hp_name, d, app_log, pitcher_stats_dict)
    a_recent_era = get_recent_era(ap_name, d, app_log, pitcher_stats_dict)

    # Bullpen ERA
    h_bp = get_bullpen_era(hid, d, team_game_log, pitcher_stats_dict)
    a_bp = get_bullpen_era(aid, d, team_game_log, pitcher_stats_dict)

    # Park / umpire
    pf = get_park_factor(game['venue'])
    ump = get_umpire_factor(game['venue'])

    # Team rest
    rest_h = min((d - team_last_game[hid]).days, 5) if hid in team_last_game else 3
    rest_a = min((d - team_last_game[aid]).days, 5) if aid in team_last_game else 3

    # Pitcher rest
    p_rest_h = min((d - pitcher_last_start[hp_name]).days, 10) if hp_name in pitcher_last_start else 5
    p_rest_a = min((d - pitcher_last_start[ap_name]).days, 10) if ap_name in pitcher_last_start else 5

    # BABIP regression signal
    h_babip = hrs - team_season_rs.get(hid, 4.5)
    a_babip = ars - team_season_rs.get(aid, 4.5)

    # Series game number
    key = tuple(sorted([hid, aid]))
    if key not in series_tracker:
        series_tracker[key] = {'last_date': d, 'game_num': 1}
    else:
        gap = (d - series_tracker[key]['last_date']).days
        series_tracker[key]['game_num'] = series_tracker[key]['game_num'] + 1 if gap <= 4 else 1
        series_tracker[key]['last_date'] = d
    series_num = series_tracker[key]['game_num']

    return {
        # Base
        'home_win_pct': hwp, 'away_win_pct': awp, 'win_pct_diff': hwp - awp,
        'home_avg_rs': hrs, 'away_avg_rs': ars, 'home_avg_ra': hra, 'away_avg_ra': ara,
        'run_diff_diff': (hrs - hra) - (ars - ara),
        # Tier 1 — Season pitcher
        'home_sp_era': hp['era'], 'away_sp_era': ap_s['era'],
        'era_diff': ap_s['era'] - hp['era'],
        'home_sp_fip': fip_calc(hp), 'away_sp_fip': fip_calc(ap_s),
        'fip_diff': fip_calc(ap_s) - fip_calc(hp),
        'home_sp_whip': hp['whip'], 'away_sp_whip': ap_s['whip'],
        # Tier 1 — Recent starts
        'home_recent_era': h_recent_era, 'away_recent_era': a_recent_era,
        'recent_era_diff': a_recent_era - h_recent_era,
        # Tier 1 — Bullpen
        'home_bp_era': h_bp, 'away_bp_era': a_bp, 'bp_era_diff': a_bp - h_bp,
        # Tier 2
        'park_factor': pf, 'ump_factor': ump,
        'rest_diff': rest_h - rest_a,
        'home_pitcher_rest': p_rest_h, 'away_pitcher_rest': p_rest_a,
        'pitcher_rest_diff': p_rest_h - p_rest_a,
        # Tier 3
        'home_babip_signal': h_babip, 'away_babip_signal': a_babip,
        'babip_diff': h_babip - a_babip,
        'series_game_num': series_num,
        'home_games': hg, 'away_games': ag,
    }
