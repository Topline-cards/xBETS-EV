"""
Runs your CBB Daily Edges model against the app's ESPN schedule.
Spread/moneyline: your matchup edge x calibrated slope + 3 pts home court.
Total: Torvik tempo x efficiency (your sheet has no totals; this uses the same Torvik ratings).
"""
import os
from datetime import timedelta

import numpy as np
import pandas as pd

from .. import build as pb
from ..matchups import norm_cdf
from . import config, torvik as T


def _cache_dir():
    base = pb.DATA if pb.DATA.rstrip("/").endswith("cbb") else os.path.join(pb.DATA, "cbb")
    return os.path.join(base, "cache")


def fit(sched, today):
    """Load Torvik tables, blend, z-score, calibrate slopes on ESPN finals. Returns ctx or None."""
    T.set_cache_dir(_cache_dir())
    T.RUN_LOG.clear()
    cur_y = T.season_year(today)
    tables = T.load_all_tables(cur_y)
    names = []
    for k in ("cur_res", "prev_res"):
        if tables[k] is not None:
            names += tables[k]["team"].tolist()
    if not names:
        T.log("No Torvik ratings available")
        return None
    R = T.TeamResolver(names)
    done = sched[(sched["status_type_completed"] == True)].dropna(subset=["home_score", "away_score"])  # noqa: E712
    season_start = done["kickoff"].min().date() if len(done) else today
    recent = done[done["kickoff"].dt.date >= today - timedelta(days=T.DAYS_BACK)]
    finals = []
    for r in recent.itertuples():
        hk = R.resolve(r.home_location, r.home_short_display_name, record=False)
        ak = R.resolve(r.away_location, r.away_short_display_name, record=False)
        if hk and ak:
            finals.append({"home_key": hk, "away_key": ak, "home_score": float(r.home_score),
                           "away_score": float(r.away_score), "neutral": bool(r.neutral_site),
                           "date": r.kickoff.date()})
    T.log(f"Finals for calibration: {len(finals)} · season start {season_start}")
    ratings, info = T.blend_ratings(tables, R, today, season_start)
    Z = T.attach_zscores(ratings)
    slopes = T.calibrate_slopes(Z, finals, today)
    d1 = Z.drop(index=T.NON_D1_KEY)
    return {"R": R, "Z": Z, "slopes": slopes, "info": info,
            "avg_t": float(d1["adjt"].mean()) if d1["adjt"].notna().any() else 68.0,
            "avg_eff": float(d1["adjoe"].mean()), "sources": {k: (None if v is None else str(v["source"].iloc[0])
                                                                  if "source" in v else "Torvik")
                                                              for k, v in tables.items()},
            "log": list(T.RUN_LOG)}


def project_games(games, ctx):
    """Same shape as the other game models (so the app + gamelines.evaluate work), plus your fields."""
    out = {}
    if not ctx:
        return out
    R, Z, slopes = ctx["R"], ctx["Z"], ctx["slopes"]
    for _, g in games.iterrows():
        hk = R.resolve(g["home_location"], g["home_short_display_name"], record=False)
        ak = R.resolve(g["away_location"], g["away_short_display_name"], record=False)
        hk = hk if hk in Z.index else None
        ak = ak if ak in Z.index else None
        if not hk and not ak:
            continue
        non_d1 = "home" if not hk else ("away" if not ak else None)
        hk, ak = hk or T.NON_D1_KEY, ak or T.NON_D1_KEY
        h, a = Z.loc[hk], Z.loc[ak]
        if T.can_full(h, a):
            x, mode = T.matchup_edge_full(h, a), "FULL"
        elif not (pd.isna(h["z_adjem"]) or pd.isna(a["z_adjem"])):
            x, mode = T.matchup_edge_base(h, a), "BASE"
        else:
            continue
        neutral = bool(g.get("neutral_site"))
        margin = slopes[mode]["slope"] * x + (0.0 if neutral else T.HCA_POINTS)
        th = h["adjt"] if not pd.isna(h["adjt"]) else ctx["avg_t"]
        ta = a["adjt"] if not pd.isna(a["adjt"]) else ctx["avg_t"]
        poss = th * ta / ctx["avg_t"]
        total = (h["adjoe"] * a["adjde"] + a["adjoe"] * h["adjde"]) / ctx["avg_eff"] * poss / 100
        if not np.isfinite(total):
            continue
        ph, pa = (total + margin) / 2, (total - margin) / 2
        out[g["game_id"]] = {
            "game_id": g["game_id"], "home": g["home_team"], "away": g["away_team"],
            "kickoff": g["kickoff"].isoformat(), "neutral": neutral, "non_d1": non_d1,
            "proj_home": round(ph, 1), "proj_away": round(pa, 1),
            "proj_margin": round(margin, 1), "proj_total": round(total, 1),
            "home_win_prob": round(norm_cdf(margin / config.MARGIN_SD), 3),
            "model": mode, "slope": round(slopes[mode]["slope"], 2), "matchup_x": round(x, 3),
            "home_em": round(float(h["adjem"]), 1), "away_em": round(float(a["adjem"]), 1),
            "home_t": round(float(th), 1), "away_t": round(float(ta), 1),
            "league_avg": round(ctx["avg_eff"], 1),
        }
    return out


def tier(edge):
    """Your edge tiers (points between model and market)."""
    if edge is None:
        return None
    e = abs(edge)
    if e > T.MAX_TRUSTED_EDGE:
        return "CHECK"
    if e >= T.STRONG_EDGE:
        return "STRONG"
    if e >= T.GOOD_EDGE:
        return "GOOD"
    if e >= T.LEAN_EDGE:
        return "LEAN"
    return "PASS"


def main():
    """Probe: load everything on GitHub and save the caches."""
    from datetime import datetime
    from ..stats import ET
    from . import stats
    pb.DATA = os.path.join(pb.DATA, "cbb") if not pb.DATA.endswith("cbb") else pb.DATA
    now = datetime.now(ET)
    sched = stats.load_schedule(stats.season_for(now))
    ctx = fit(sched, now.date())
    sl = stats.slate(sched)
    d = stats.next_day(sl, now)
    g = sl[sl["kickoff"].dt.date.map(lambda x: x.isoformat()) == d]
    m = project_games(g, ctx) if ctx else {}
    out = {"log": T.RUN_LOG[-40:], "day": d, "games": len(g), "projected": len(m),
           "sample": list(m.values())[:6], "sources": ctx and ctx["sources"],
           "slopes": ctx and ctx["slopes"]}
    pb._save("model_check.json", out)
    print("\n".join(T.RUN_LOG))


if __name__ == "__main__":
    main()
