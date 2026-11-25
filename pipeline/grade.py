"""
Grades the paper-trading log once box scores are in (nflverse updates
overnight). Writes docs/data/results.json with a summary per mode.

  python -m pipeline.grade
"""
from datetime import datetime

from . import config, odds, stats
from .build import _load, _save
from .stats import ET


def grade(now=None):
    now = now or datetime.now(ET)
    season = stats.season_for(now)
    log = _load("log.json", {"picks": {}})
    pending = [r for r in log["picks"].values() if r["result"] is None
               and datetime.fromisoformat(r["kickoff"]) < now]
    if pending:
        wk = stats.load_weekly(season)
        wk = wk.assign(pkey=wk["player_display_name"].map(odds.pkey))
        sched = stats.load_schedule(season).set_index("game_id")
        for r in pending:
            if r.get("kind", "prop") != "prop":
                _grade_game(r, sched)
                continue
            # Wait until nflverse has published box scores for this team's game,
            # otherwise a missing player would wrongly count as "did not play".
            if r.get("team"):
                team_rows = wk[(wk["week"] == r["week"]) & (wk["team"] == r["team"])]
            else:   # unknown team: grade only if the player shows up
                team_rows = wk[(wk["week"] == r["week"]) & (wk["pkey"] == r["pkey"])]
            if team_rows.empty:
                if (now - datetime.fromisoformat(r["kickoff"])).days >= 4:
                    r.update(result="void", actual=None, profit=0.0)   # no box score ever came
                continue
            col = config.PROP_STATS[r["stat"]][0]
            row = team_rows[team_rows["pkey"] == r["pkey"]]
            if row.empty:
                r.update(result="void", actual=None, profit=0.0)
                continue
            actual = float(row.iloc[0][col] or 0)
            if actual == r["line"]:
                res, prof = "push", 0.0
            else:
                win = actual > r["line"] if r["side"] == "over" else actual < r["line"]
                res = "win" if win else "loss"
                prof = round(r["units"] * (r["dec"] - 1), 2) if win else -r["units"]
            r.update(result=res, actual=actual, profit=prof)
    _save("log.json", log)
    _save("results.json", summarize(log))
    # every graded bet, keyed the way the app saves "My Results" bets
    graded = {}
    for r in log["picks"].values():
        if r["result"] is None:
            continue
        k = f"{r['week']}|{r['pkey']}|{r['stat']}|{r['side']}|{r['line']}"
        graded[k] = {"result": r["result"], "actual": r.get("actual")}
    _save("graded.json", graded)
    return sum(1 for r in log["picks"].values() if r["result"] is None
               and datetime.fromisoformat(r["kickoff"]) < now)


def _settle(r, win, push=False):
    if push:
        r.update(result="push", profit=0.0)
    elif win:
        r.update(result="win", profit=round(r["units"] * (r["dec"] - 1), 2))
    else:
        r.update(result="loss", profit=-r["units"])


def _grade_game(r, sched):
    if r.get("game_id") not in sched.index:
        r.update(result="void", actual=None, profit=0.0)
        return
    g = sched.loc[r["game_id"]]
    if g["home_score"] != g["home_score"]:          # NaN: not final yet
        return
    hs, as_ = float(g["home_score"]), float(g["away_score"])
    if r["kind"] == "total":
        tot = hs + as_
        r["actual"] = tot
        _settle(r, tot > r["line"] if r["side"] == "over" else tot < r["line"], tot == r["line"])
    elif r["kind"] == "ml":
        r["actual"] = f"{int(as_)}-{int(hs)}"
        _settle(r, hs > as_ if r["side"] == "home" else as_ > hs, hs == as_)
    else:                                            # spread, line = home spread
        adj = hs + r["line"] - as_
        r["actual"] = f"{int(as_)}-{int(hs)}"
        _settle(r, adj > 0 if r["side"] == "home" else adj < 0, adj == 0)


def summarize(log, sport="nfl"):
    from . import ev
    out = {"lanes": ev.lane_stats(log, sport)}
    for mode in ("combined", "ev", "cheat"):
        rs = [r for r in log["picks"].values() if r["mode"] == mode]
        graded = [r for r in rs if r["result"] in ("win", "loss", "push")]
        risked = sum(r["units"] for r in graded if r["result"] != "push")
        profit = sum(r["profit"] for r in graded)
        clv = [r["clv_pct"] for r in rs if r.get("clv_pct") is not None]
        out[mode] = {
            "picks": len(rs), "graded": len(graded),
            "wins": sum(r["result"] == "win" for r in graded),
            "losses": sum(r["result"] == "loss" for r in graded),
            "pushes": sum(r["result"] == "push" for r in graded),
            "units_risked": round(risked, 2), "profit_units": round(profit, 2),
            "roi_pct": round(profit / risked * 100, 1) if risked else None,
            "avg_clv_pct": round(sum(clv) / len(clv), 2) if clv else None,
            "beat_close_pct": round(sum(c > 0 for c in clv) / len(clv) * 100, 1)
            if clv else None,
            # playbook: show expected vs actual so normal downswings don't look like a broken system
            "expected_units": round(sum((r.get("ev_pct") or 0) / 100 * r["units"] for r in graded
                                        if r["result"] != "push"), 2),
        }
    recent = sorted(log["picks"].values(), key=lambda r: r["kickoff"], reverse=True)
    out["recent"] = recent[:150]
    return out


if __name__ == "__main__":
    grade()
    print("graded")
