"""
Cheat-sheet model for moneyline / spread / total.

Same idea as the player cheat sheet, at team level:
  team points = league avg x (team's offense vs avg) x (opponent defense vs avg),
  with only part of the effect trusted (GAME_MODEL_WEIGHT), plus home field.
  Win / cover / over chances come from a normal curve around the projection.
"""
from . import config
from .matchups import norm_cdf


def project_games(games, ratings, lg):
    out = {}
    w = config.GAME_MODEL_WEIGHT
    for _, g in games.iterrows():
        h, a = g["home_team"], g["away_team"]
        rh, ra = ratings.get(h), ratings.get(a)
        if not rh or not ra:
            continue
        raw_h = lg * (rh["pf"] / lg) * (ra["pa"] / lg)
        raw_a = lg * (ra["pf"] / lg) * (rh["pa"] / lg)
        ph = lg + w * (raw_h - lg) + config.HOME_FIELD_PTS / 2
        pa = lg + w * (raw_a - lg) - config.HOME_FIELD_PTS / 2
        out[g["game_id"]] = {
            "game_id": g["game_id"], "home": h, "away": a,
            "kickoff": g["kickoff"].isoformat(),
            "proj_home": round(ph, 1), "proj_away": round(pa, 1),
            "proj_margin": round(ph - pa, 1), "proj_total": round(ph + pa, 1),
            "home_pf": rh["pf"], "home_pa": rh["pa"], "away_pf": ra["pf"], "away_pa": ra["pa"],
            "home_games": rh["games"], "away_games": ra["games"], "league_avg": lg,
            "home_win_prob": round(norm_cdf((ph - pa) / config.MARGIN_SD), 3),
        }
    return out


def model_prob(m, kind, side, line):
    """Model chance this side wins."""
    if kind == "ml":
        p = m["home_win_prob"]
        return p if side == "home" else 1 - p
    if kind == "spread":                       # line is the HOME spread
        p_home = norm_cdf((m["proj_margin"] + line) / config.MARGIN_SD)
        return p_home if side == "home" else 1 - p_home
    if kind == "total":
        p_over = norm_cdf((m["proj_total"] - line) / config.TOTAL_SD)
        return p_over if side == "over" else 1 - p_over
    return None


def evaluate(m, kind, side, line, book_implied):
    """Cheat-sheet read on one side of a game line."""
    mp = model_prob(m, kind, side, line)
    if kind == "spread":
        edge = m["proj_margin"] + line if side == "home" else -(m["proj_margin"] + line)
        agree = edge >= config.SPREAD_MIN_EDGE
        edge_txt = f"{edge:+.1f} pts"
    elif kind == "total":
        edge = m["proj_total"] - line if side == "over" else line - m["proj_total"]
        agree = edge >= config.TOTAL_MIN_EDGE
        edge_txt = f"{edge:+.1f} pts"
    else:
        edge = (mp - book_implied) * 100
        agree = edge >= config.ML_MIN_EDGE_PCT
        edge_txt = f"{edge:+.1f}%"
    return {"model_prob": round(mp, 3), "model_edge": round(edge, 2),
            "model_edge_txt": edge_txt, "cheat_side": side if agree else None}
