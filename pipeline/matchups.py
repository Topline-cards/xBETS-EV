"""
Cheat sheet engine: turns defense-vs-position into player-level projections.

Example: the Colts allow 30% more WR receiving yards than league average.
A Vikings WR averaging 70 yds gets projected at 70 * (1 + 0.5 * 0.30) = 80.5
(MATCHUP_WEIGHT = 0.5 because defense numbers are noisy).
"""
from math import erf, sqrt

from . import config


def norm_cdf(x):
    return 0.5 * (1 + erf(x / sqrt(2)))


def opponent_map(games):
    """{team: (opponent, game_id, kickoff_iso, home/away)} for this week's games."""
    m = {}
    for _, g in games.iterrows():
        k = g["kickoff"].isoformat()
        m[g["home_team"]] = (g["away_team"], g["game_id"], k, "home")
        m[g["away_team"]] = (g["home_team"], g["game_id"], k, "away")
    return m


def build_projections(baselines, allowed, league, games):
    """Every player/stat for this week's games with matchup factor + projection."""
    opp = opponent_map(games)
    rows = []
    for r in baselines.itertuples():
        if r.team not in opp:
            continue
        opp_team, game_id, kickoff, side = opp[r.team]
        lg = league.get((r.position, r.stat))
        dv = allowed.get((opp_team, r.position, r.stat))
        if not lg or dv is None:
            continue
        lo, hi = config.MATCHUP_FACTOR_RANGE
        factor = min(max(dv / lg, lo), hi)
        proj = r.baseline * (1 + config.MATCHUP_WEIGHT * (factor - 1))
        rows.append({
            "player": r.player, "player_id": r.player_id, "team": r.team,
            "opp": opp_team, "position": r.position, "stat": r.stat,
            "stat_label": config.PROP_STATS[r.stat][2],
            "game_id": game_id, "kickoff": kickoff, "home_away": side,
            "headshot": r.headshot,
            "baseline": r.baseline, "sd": r.sd, "games": r.games, "last10": r.last10,
            "def_allowed": round(dv, 1), "league_avg": round(lg, 1),
            "matchup_factor": round(factor, 3),
            "matchup_pct": round((factor - 1) * 100, 1),
            "projection": round(proj, 1),
        })
    return rows


def rank_of(allowed, opp_team, position, stat):
    """1 = allows the most (best matchup), 32 = allows the least."""
    vals = sorted(
        ((t, v) for (t, p, s), v in allowed.items() if p == position and s == stat),
        key=lambda x: -x[1],
    )
    for i, (t, _) in enumerate(vals, 1):
        if t == opp_team:
            return i
    return None


def evaluate_line(proj_row, line):
    """
    Cheat-sheet read on a specific prop line.
    Returns dict with side ('over'/'under'/None), edge %, hit rate, model prob.
    """
    p = proj_row["projection"]
    edge = (p - line) / line * 100 if line else 0
    hits = [v for v in proj_row["last10"]]
    over_rate = sum(v > line for v in hits) / len(hits) if hits else None
    sd = proj_row["sd"] or max(p * 0.35, 1.0)
    model_over = 1 - norm_cdf((line - p) / sd)

    side = None
    f = proj_row["matchup_factor"]
    if edge >= config.CHEAT_MIN_EDGE_PCT and f >= config.MATCHUP_GOOD:
        side = "over"
    elif edge <= -config.CHEAT_MIN_EDGE_PCT and f <= config.MATCHUP_BAD:
        side = "under"
    return {
        "cheat_side": side,
        "proj_edge_pct": round(edge, 1),
        "hit_rate_over": round(over_rate, 2) if over_rate is not None else None,
        "model_prob_over": round(model_over, 3),
    }
