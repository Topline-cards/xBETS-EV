"""
Grades college basketball picks from ESPN final scores.
Writes docs/data/cbb/{log,results,graded}.json.
"""
from datetime import datetime

from .. import build as pb
from ..grade import _settle, summarize
from ..stats import ET
from . import stats
from .build import cbb_data


def grade(now=None):
    with cbb_data():
        return _grade(now or datetime.now(ET))


def _grade(now):
    log = pb._load("log.json", {"picks": {}})
    pending = [r for r in log["picks"].values() if r["result"] is None
               and datetime.fromisoformat(r["kickoff"]) < now]
    if pending:
        sched = stats.load_schedule(stats.season_for(datetime.fromisoformat(min(r["kickoff"] for r in pending))))
        done = sched[sched["status_type_completed"] == True].set_index("game_id")  # noqa: E712
        for r in pending:
            gid = str(r.get("game_id"))
            if gid not in done.index:
                if (now - datetime.fromisoformat(r["kickoff"])).days >= 4:
                    r.update(result="void", actual=None, profit=0.0)   # postponed / never finished
                continue
            row = done.loc[gid]
            hs, as_ = float(row["home_score"]), float(row["away_score"])
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
    pb._save("log.json", log)
    pb._save("results.json", summarize(log, "cbb"))
    pb._save("graded.json", {f"{r['week']}|{r['pkey']}|{r['stat']}|{r['side']}|{r['line']}":
                             {"result": r["result"], "actual": r.get("actual")}
                             for r in log["picks"].values() if r["result"] is not None})
    return sum(1 for r in log["picks"].values() if r["result"] is None
               and datetime.fromisoformat(r["kickoff"]) < now)


if __name__ == "__main__":
    print("pending:", grade())
