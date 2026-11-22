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
        for r in pending:
            # Wait until nflverse has published box scores for this team's game,
            # otherwise a missing player would wrongly count as "did not play".
            if r.get("team"):
                team_rows = wk[(wk["week"] == r["week"]) & (wk["team"] == r["team"])]
            else:   # unknown team: grade only if the player shows up
                team_rows = wk[(wk["week"] == r["week"]) & (wk["pkey"] == r["pkey"])]
            if team_rows.empty:
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


def summarize(log):
    out = {}
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
        }
    recent = sorted(log["picks"].values(), key=lambda r: r["kickoff"], reverse=True)
    out["recent"] = recent[:150]
    return out


if __name__ == "__main__":
    grade()
    print("graded")
