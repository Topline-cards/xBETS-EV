"""
NBA data: ESPN box scores + schedule (sportsdataverse releases on GitHub,
updated daily) and ESPN's injury report.
"""
import io
import re
import time
from datetime import datetime, timedelta
from functools import lru_cache

import pandas as pd
import requests

from .. import odds
from ..stats import ET, covered_day
from . import config

REL = "https://github.com/sportsdataverse/sportsdataverse-data/releases/download"
INJURIES = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/injuries"


def season_for(now=None):
    """ESPN labels a season by the year it ends (2026-27 -> 2027)."""
    now = now or datetime.now(ET)
    return now.year + 1 if now.month >= 8 else now.year


@lru_cache(maxsize=8)
def _parquet(url, hour):
    """Download once per hour per process."""
    for i in range(3):
        r = requests.get(url, timeout=60)
        if r.status_code == 200:
            return pd.read_parquet(io.BytesIO(r.content))
        if r.status_code == 404:
            return pd.DataFrame()
        time.sleep(3 * (i + 1))
    r.raise_for_status()


def _hour():
    return datetime.now(ET).strftime("%Y%m%d%H")


# ------------------------------------------------------------------ schedule
def load_schedule(season):
    s = _parquet(f"{REL}/espn_nba_schedules/nba_schedule_{season}.parquet", _hour())
    if s.empty:
        return s
    s = s[s["season_type"].isin([2, 3])]
    s = s[(s["home_abbreviation"] != "TBD") & (s["away_abbreviation"] != "TBD")].copy()
    s["kickoff"] = pd.to_datetime(s["date"], utc=True).dt.tz_convert(ET)
    s["game_id"] = s["game_id"].astype(str)
    s = s.rename(columns={"home_abbreviation": "home_team", "away_abbreviation": "away_team"})
    return s.sort_values("kickoff").reset_index(drop=True)


def slate(sched):
    """Games the app covers: Monday-Friday and Christmas Day."""
    if sched.empty:
        return sched
    keep = sched["kickoff"].dt.date.map(
        lambda d: covered_day(d, config.GAME_WEEKDAYS, config.GAME_HOLIDAYS))
    return sched[keep]


def next_day(slate_df, now):
    """ET date (YYYY-MM-DD) of the next covered game day that hasn't fully started."""
    up = slate_df[slate_df["kickoff"] > now]
    if up.empty:
        return None
    return up["kickoff"].min().date().isoformat()


def team_logos(sched):
    out = {}
    for side in ("home", "away"):
        for a, u in zip(sched[f"{side}_team"], sched[f"{side}_logo"]):
            if isinstance(u, str):
                out[a] = u
    return out


@lru_cache(maxsize=1)
def _name_map():
    s = load_schedule(season_for())
    m = {}
    for side in ("home", "away"):
        for a, full, nick in zip(s[f"{side}_team"], s[f"{side}_display_name"], s[f"{side}_name"]):
            m[odds.pkey(full)] = a
            m[odds.pkey(nick)] = a
            m[a.lower()] = a
    m.update({"la clippers": "LAC", "los angeles clippers": "LAC", "gsw": "GS", "nyk": "NY",
              "nop": "NO", "sas": "SA", "uta": "UTAH", "was": "WSH", "pho": "PHX", "bkn": "BKN",
              "brk": "BKN", "cha": "CHA", "okc": "OKC"})
    return m


def team_abbr(name, hint=None):
    """'Los Angeles Lakers' / 'Lakers' / 'LAL' -> ESPN abbreviation."""
    m = _name_map()
    for x in (name, hint):
        if not isinstance(x, str) or not x:
            continue
        k = odds.pkey(x)
        if k in m:
            return m[k]
        if x.lower() in m:
            return m[x.lower()]
        for nick in sorted(m, key=len, reverse=True):
            if len(nick) > 3 and k.endswith(" " + nick):
                return m[nick]
    return None


def stat_from_text(text):
    """SharpAPI market_type / Pinnacle special_units -> stat key."""
    t = " " + (text or "").lower().replace("_", " ").replace("&", "+") + " "
    bad = ("1st", "first", "quarter", "half", "minutes", "most", "h2h", "combo", "each", "either",
           "double", "triple", "race", "alt ", "milestone")
    if any(b in t for b in bad):
        return None
    pts, reb, ast = ("point" in t or "pts" in t), ("rebound" in t or "rebs" in t), ("assist" in t or "asts" in t)
    if pts and reb and ast:
        return "pra"
    if sum((pts, reb, ast)) > 1 or "+" in t:
        return None                     # other combos: not tracked
    if "three" in t or "3 point" in t or "3pt" in t:
        return "fg3"
    if pts:
        return "pts"
    if reb:
        return "reb"
    if ast:
        return "ast"
    return None


# ------------------------------------------------------------------ box scores
def load_box(season):
    b = _parquet(f"{REL}/espn_nba_player_boxscores/player_box_{season}.parquet", _hour())
    if b.empty:
        return b
    b = b[b["season_type"].isin([2, 3])].copy()
    b["game_id"] = b["game_id"].astype(str)
    b["when"] = pd.to_datetime(b["game_date_time"], utc=True).dt.tz_convert(ET)
    b["pra"] = b["points"].fillna(0) + b["rebounds"].fillna(0) + b["assists"].fillna(0)
    b["pkey"] = b["athlete_display_name"].map(odds.pkey)
    b["pos"] = b["athlete_position_abbreviation"].fillna("").str[:1].replace({"": "F"})
    return b.sort_values("when").reset_index(drop=True)


def played(b):
    return b[(b["did_not_play"] != True) & (b["minutes"].fillna(0) > 0)]  # noqa: E712


def _lbl(ts):
    return f"{ts.month}/{ts.day}"


def defense_vs_position(box, prior=None):
    """
    {(defense, pos, stat): avg allowed per game}, {(pos, stat): league avg},
    plus logs[(defense, pos, stat)] = last 8 games.
    Early in the season, last season's games are mixed in.
    """
    p = played(box)
    per_team_games = p.groupby("opponent_team_abbreviation")["game_id"].nunique()
    if prior is not None and not prior.empty and (per_team_games.median() if len(per_team_games) else 0) < config.DEF_MIN_GAMES:
        p = pd.concat([played(prior).groupby("opponent_team_abbreviation").tail(150), p])
    cols = [c for c, _ in config.PROP_STATS.values()]
    g = (p.groupby(["opponent_team_abbreviation", "game_id", "pos"])
         .agg({**{c: "sum" for c in cols}, "when": "first", "team_abbreviation": "first", "season": "first"})
         .reset_index())
    allowed, league, logs = {}, {}, {}
    for key, (col, _) in config.PROP_STATS.items():
        for pos in config.POSITIONS:
            gp = g[g["pos"] == pos]
            if gp.empty:
                continue
            by = gp.groupby("opponent_team_abbreviation")[col].mean()
            league[(pos, key)] = float(by.mean())
            for team, v in by.items():
                allowed[(team, pos, key)] = float(v)
            for team, gg in gp.sort_values("when").groupby("opponent_team_abbreviation"):
                logs[(team, pos, key)] = [
                    {"season": int(r.season), "wk": r.when.date().isoformat(), "lbl": _lbl(r.when),
                     "vs": r.team_abbreviation, "v": round(float(getattr(r, col) or 0), 1)}
                    for r in gg.itertuples()][-8:]
    return allowed, league, logs


def player_baselines(box, prior=None):
    """One row per (player, stat) with recent average, sd, log, last-10 values."""
    p = played(box)
    if prior is not None and not prior.empty:
        p = pd.concat([played(prior), p])
    p = p.sort_values("when")
    out = []
    for pid, g in p.groupby("athlete_id"):
        last = g.iloc[-1]
        if last["season"] != box["season"].max():
            continue                                  # not on a roster this season
        if g["minutes"].tail(5).mean() < config.MIN_MINUTES:
            continue
        for key, (col, _) in config.PROP_STATS.items():
            vals = g[col].fillna(0).tolist()
            recent = vals[-config.BASELINE_GAMES:]
            if len(recent) < config.MIN_GAMES:
                continue
            mean = sum(recent) / len(recent)
            if mean < config.MIN_BASELINE.get(key, 0.1):
                continue
            sd = pd.Series(vals[-15:]).std()
            log = [{"season": int(r.season), "wk": r.when.date().isoformat(), "lbl": _lbl(r.when),
                    "opp": r.opponent_team_abbreviation, "v": round(float(getattr(r, col) or 0), 1),
                    "ha": r.home_away, "min": int(r.minutes or 0)}
                   for r in g.tail(10).itertuples()]
            out.append({
                "player_id": str(pid), "player": last["athlete_display_name"], "pkey": last["pkey"],
                "team": last["team_abbreviation"], "position": last["pos"],
                "headshot": last["athlete_headshot_href"], "stat": key,
                "baseline": round(mean, 1), "sd": round(float(sd), 1) if sd == sd else None,
                "games": len(recent), "last10": [round(float(v), 1) for v in vals[-10:]], "log": log,
                "minutes": round(float(g["minutes"].tail(5).mean()), 1),
            })
    return pd.DataFrame(out)


def history_vs(boxes):
    """{(player_id, opp): [{"season","wk","v": {stat: value}}]} from several seasons."""
    p = played(pd.concat([b for b in boxes if b is not None and not b.empty])).sort_values("when")
    out = {}
    for r in p.itertuples():
        out.setdefault((str(r.athlete_id), r.opponent_team_abbreviation), []).append(
            {"season": int(r.season), "wk": r.when.date().isoformat(), "lbl": _lbl(r.when),
             "v": {k: round(float(getattr(r, c) or 0), 1) for k, (c, _) in config.PROP_STATS.items()}})
    return out


def team_ratings(box, prior=None):
    """Points for / against per game from final scores. Early season mixes in last season."""
    def per_game(b):
        if b is None or b.empty:
            return pd.DataFrame()
        return (b.groupby(["game_id", "team_abbreviation"])
                .agg(pf=("team_score", "first"), pa=("opponent_team_score", "first"), when=("when", "first"))
                .reset_index())
    cur = per_game(box)
    n = cur.groupby("team_abbreviation").size()
    df = cur
    if prior is not None and (n.median() if len(n) else 0) < config.DEF_MIN_GAMES:
        df = pd.concat([per_game(prior).sort_values("when").groupby("team_abbreviation").tail(15), cur])
    df = df.dropna(subset=["pf", "pa"])
    r = {t: {"pf": round(float(g["pf"].mean()), 1), "pa": round(float(g["pa"].mean()), 1),
             "games": int(len(g))} for t, g in df.groupby("team_abbreviation")}
    lg = round(float(df["pf"].mean()), 1) if len(df) else 115.0
    return r, lg


def availability():
    """ESPN injury report: {pkey: {"status", "injury", "team", "hide", "why"}}."""
    out = {}
    try:
        j = requests.get(INJURIES, timeout=20).json()
    except Exception as e:
        print("injury feed failed:", e)
        return out
    for team in j.get("injuries", []):
        for it in team.get("injuries", []):
            a = it.get("athlete") or {}
            name = a.get("displayName")
            st = (it.get("status") or "").strip()
            if not name or not st:
                continue
            det = it.get("details") or {}
            inj = det.get("type")
            fantasy = ((det.get("fantasyStatus") or {}).get("description") or "")
            status = st
            if fantasy.upper() == "OUT":
                status = "Out"
            hide = status in config.HIDE_INJURY_STATUS
            out[odds.pkey(name)] = {
                "status": status, "injury": inj if isinstance(inj, str) else None,
                "team": ((a.get("team") or {}).get("abbreviation")), "hide": hide,
                "why": f"{status}" + (f" ({inj})" if inj else "") if hide else None,
                "note": re.sub(r"\s+", " ", it.get("shortComment") or "")[:200],
            }
    return out


def games_in_window(sched, hours, now):
    return sched[(sched["kickoff"] > now) & (sched["kickoff"] <= now + timedelta(hours=hours))]
