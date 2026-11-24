"""
Grades NBA picks from ESPN box scores (posted overnight).
Writes docs/data/nba/{log,results,graded}.json.

  python -m pipeline.nba.grade
"""
from datetime import datetime

from .. import build as pb
from ..grade import _settle, summarize
from ..stats import ET
from . import config, stats
from .build import nba_data


def grade(now=None):
    with nba_data():
        return _grade(now or datetime.now(ET))


def _grade(now):
    log = pb._load("log.json", {"picks": {}})
    pending = [r for r in log["picks"].values() if r["result"] is None
               and datetime.fromisoformat(r["kickoff"]) < now]
    if pending:
        season = stats.season_for(datetime.fromisoformat(min(r["kickoff"] for r in pending)))
        box = stats.load_box(season)
        by_game = {gid: g for gid, g in box.groupby("game_id")} if not box.empty else {}
        for r in pending:
            g = by_game.get(str(r.get("game_id")))
            if g is None:
                if (now - datetime.fromisoformat(r["kickoff"])).days >= 4:
                    r.update(result="void", actual=None, profit=0.0)     # no box score ever came
                continue
            if r.get("kind", "prop") != "prop":
                _grade_game(r, g)
                continue
            row = g[(g["pkey"] == r["pkey"]) & (g["did_not_play"] != True) & (g["minutes"].fillna(0) > 0)]  # noqa: E712
            if row.empty:
                r.update(result="void", actual=None, profit=0.0)          # DNP: books void
                continue
            actual = float(row.iloc[0][config.PROP_STATS[r["stat"]][0]] or 0)
            if actual == r["line"]:
                r.update(result="push", actual=actual, profit=0.0)
            else:
                win = actual > r["line"] if r["side"] == "over" else actual < r["line"]
                r["actual"] = actual
                _settle(r, win)
    pb._save("log.json", log)
    pb._save("results.json", summarize(log))
    graded = {f"{r['week']}|{r['pkey']}|{r['stat']}|{r['side']}|{r['line']}":
              {"result": r["result"], "actual": r.get("actual")}
              for r in log["picks"].values() if r["result"] is not None}
    pb._save("graded.json", graded)
    return sum(1 for r in log["picks"].values() if r["result"] is None
               and datetime.fromisoformat(r["kickoff"]) < now)


def _grade_game(r, g):
    home = g[g["home_away"] == "home"]
    if home.empty or home.iloc[0]["team_score"] != home.iloc[0]["team_score"]:
        return
    hs, as_ = float(home.iloc[0]["team_score"]), float(home.iloc[0]["opponent_team_score"])
    if r["kind"] == "total":
        tot = hs + as_
        r["actual"] = tot
        _settle(r, tot > r["line"] if r["side"] == "over" else tot < r["line"], tot == r["line"])
    elif r["kind"] == "ml":
        r["actual"] = f"{int(as_)}-{int(hs)}"
        _settle(r, hs > as_ if r["side"] == "home" else as_ > hs, hs == as_)
    else:
        adj = hs + r["line"] - as_
        r["actual"] = f"{int(as_)}-{int(hs)}"
        _settle(r, adj > 0 if r["side"] == "home" else adj < 0, adj == 0)


if __name__ == "__main__":
    print("pending:", grade())
