"""
College basketball data: ESPN schedule + scores (sportsdataverse releases on GitHub),
a ridge-regression team rating model, and team-name matching across feeds.
"""
import re
from datetime import datetime, timedelta
from difflib import SequenceMatcher

import numpy as np
import pandas as pd

from ..nba.stats import _hour, _parquet, REL
from ..stats import ET, covered_day
from . import config


def season_for(now=None):
    now = now or datetime.now(ET)
    return now.year + 1 if now.month >= 8 else now.year


def load_schedule(season):
    s = _parquet(f"{REL}/espn_mens_college_basketball_schedules/mbb_schedule_{season}.parquet", _hour())
    if s.empty:
        return s
    s = s[s["season_type"].isin([2, 3])].copy()
    s = s[(s["home_abbreviation"] != "TBD") & (s["away_abbreviation"] != "TBD")]
    s["kickoff"] = pd.to_datetime(s["date"], utc=True).dt.tz_convert(ET)
    s["game_id"] = s["game_id"].astype(str)
    s["home_team"] = s["home_short_display_name"].fillna(s["home_location"])
    s["away_team"] = s["away_short_display_name"].fillna(s["away_location"])
    return s.sort_values("kickoff").reset_index(drop=True)


def slate(sched):
    if sched.empty:
        return sched
    keep = sched["kickoff"].dt.date.map(
        lambda d: covered_day(d, config.GAME_WEEKDAYS, config.GAME_HOLIDAYS))
    return sched[keep]


def next_day(slate_df, now):
    up = slate_df[slate_df["kickoff"] > now]
    return None if up.empty else up["kickoff"].min().date().isoformat()


def team_logos(sched):
    out = {}
    for side in ("home", "away"):
        for t, u in zip(sched[f"{side}_team"], sched[f"{side}_logo"]):
            if isinstance(u, str):
                out[t] = u
    return out


def games_in_window(sched, hours, now):
    return sched[(sched["kickoff"] > now) & (sched["kickoff"] <= now + timedelta(hours=hours))]


# ------------------------------------------------------------------ ratings
def _completed(s):
    s = s[s["status_type_completed"] == True]  # noqa: E712
    return s.dropna(subset=["home_score", "away_score"])


def team_ratings(cur, prior=None, now=None):
    """
    Ridge fit of  pts = mu + off[team] - def[opp] + hca * (not neutral)  on every completed game.
    Early in the season last season's games are mixed in at a lower weight.
    Returns {team_id: {...}}, mu, hca.
    """
    now = now or datetime.now(ET)
    frames = [(_completed(cur), 1.0)]
    start = cur["kickoff"].min() if not cur.empty else None
    if prior is not None and not prior.empty and (start is None or now < start + timedelta(days=config.PRIOR_DAYS)):
        frames.append((_completed(prior), config.PRIOR_WEIGHT))
    # Drop games vs teams with almost no games on record (mostly non-Division I opponents):
    # ridge would rate them average and blow up the D-I team's numbers.
    cnt = {}
    for df, _ in frames:
        for col in ("home_id", "away_id"):
            for t in df[col].astype(str):
                cnt[t] = cnt.get(t, 0) + 1
    rows = []
    hfa = config.HOME_FIELD_PTS
    for df, w in frames:
        for r in df.itertuples():
            h, a = str(r.home_id), str(r.away_id)
            if cnt.get(h, 0) < 3 or cnt.get(a, 0) < 3:
                continue
            adj = 0.0 if r.neutral_site else hfa / 2          # home court fixed, not fitted
            rows.append((h, a, float(r.home_score) - adj, 0.0, w))
            rows.append((a, h, float(r.away_score) + adj, 0.0, w))
    if not rows:
        return {}, 72.0, config.HOME_FIELD_PTS
    teams = sorted({x[0] for x in rows} | {x[1] for x in rows})
    ix = {t: i for i, t in enumerate(teams)}
    n = len(teams)
    X = np.zeros((len(rows), 2 * n + 1))
    y = np.zeros(len(rows))
    W = np.zeros(len(rows))
    for k, (t, o, pts, h, w) in enumerate(rows):
        X[k, ix[t]] = 1.0            # offense of scoring team
        X[k, n + ix[o]] = -1.0       # defense of opponent
        X[k, 2 * n] = h / 2          # home court split: +half for home, -half for away
        y[k] = pts
        W[k] = w
    mu = float(np.average(y, weights=W))
    yc = y - mu
    sw = np.sqrt(W)
    Xw, yw = X * sw[:, None], yc * sw
    reg = np.full(2 * n + 1, config.RIDGE)
    beta = np.linalg.solve(Xw.T @ Xw + np.diag(reg), Xw.T @ yw)
    off, dfn, hca = beta[:n], beta[n:2 * n], hfa
    games = {}
    cur_done = _completed(cur)
    for col in ("home_id", "away_id"):
        for t in cur_done[col].astype(str):
            games[t] = games.get(t, 0) + 1
    out = {t: {"off": float(off[ix[t]]), "def": float(dfn[ix[t]]), "games": games.get(t, 0),
               "pf": round(mu + float(off[ix[t]]), 1), "pa": round(mu - float(dfn[ix[t]]), 1)}
           for t in teams}
    return out, mu, hca


def project_games(games, ratings, mu, hca):
    """Same output shape as the NFL/NBA game model, so the app and gamelines.evaluate work."""
    from ..matchups import norm_cdf
    w = config.GAME_MODEL_WEIGHT
    out = {}
    for _, g in games.iterrows():
        rh, ra = ratings.get(str(g["home_id"])), ratings.get(str(g["away_id"]))
        if not rh or not ra or min(rh["games"], ra["games"]) < config.MIN_TEAM_GAMES:
            continue
        h = 0.0 if g.get("neutral_site") else hca
        ph = mu + w * (rh["off"] - ra["def"]) + h / 2
        pa = mu + w * (ra["off"] - rh["def"]) - h / 2
        out[g["game_id"]] = {
            "game_id": g["game_id"], "home": g["home_team"], "away": g["away_team"],
            "kickoff": g["kickoff"].isoformat(), "neutral": bool(g.get("neutral_site")),
            "proj_home": round(ph, 1), "proj_away": round(pa, 1),
            "proj_margin": round(ph - pa, 1), "proj_total": round(ph + pa, 1),
            "home_pf": rh["pf"], "home_pa": rh["pa"], "away_pf": ra["pf"], "away_pa": ra["pa"],
            "home_games": rh["games"], "away_games": ra["games"], "league_avg": round(mu, 1),
            "home_win_prob": round(norm_cdf((ph - pa) / config.MARGIN_SD), 3),
        }
    return out


# ------------------------------------------------------------------ name matching
_SUBS = [(r"&", " and "), (r"\bst\.?$", " state"), (r"^st\.? ", "saint "), (r"\bst\.? ", "state "),
         (r"\buniv(ersity)?\b", ""), (r"\bthe\b", ""), (r"[^a-z0-9() ]", " ")]


def _n(x):
    x = str(x or "").lower().strip()
    for a, b in _SUBS:
        x = re.sub(a, b, x)
    return re.sub(r"\s+", " ", x).strip()


def aliases(row, side):
    names = {row.get(f"{side}_display_name"), row.get(f"{side}_location"),
             row.get(f"{side}_short_display_name"), row.get(f"{side}_abbreviation")}
    loc, nick = row.get(f"{side}_location"), row.get(f"{side}_name")
    if loc and nick:
        names.add(f"{loc} {nick}")
    return {_n(x) for x in names if isinstance(x, str) and x}


def _sim(name, al):
    n = _n(name)
    if not n:
        return 0.0
    if n in al:
        return 1.0
    return max(SequenceMatcher(None, n, a).ratio() for a in al)


def matcher(day_games):
    """Returns match(home_name, away_name) -> (game row, swapped) or (None, None)."""
    cands = [(r, aliases(r, "home"), aliases(r, "away")) for r in day_games.to_dict("records")]
    cache = {}

    def match(home, away):
        k = (home, away)
        if k in cache:
            return cache[k]
        scored = []
        for r, ah, aa in cands:
            direct = min(_sim(home, ah), _sim(away, aa))
            swap = min(_sim(home, aa), _sim(away, ah))
            scored.append((max(direct, swap), swap > direct, r))
        scored.sort(key=lambda x: -x[0])
        res = (None, None)
        if scored and scored[0][0] >= config.MATCH_MIN and (
                len(scored) == 1 or scored[0][0] - scored[1][0] >= config.MATCH_MARGIN):
            res = (scored[0][2], scored[0][1])
        cache[k] = res
        return res
    return match
