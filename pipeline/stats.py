"""
Stats + schedule from nflverse (via nflreadpy). Free, no key, works on GitHub.

Ported from the original Jupyter cheat sheet:
  * week 1      -> prior season only
  * weeks 2-3   -> blend of prior season + this season so far
  * week 4+     -> this season only
"""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import nflreadpy as nflr
import pandas as pd

from . import config

ET = ZoneInfo("America/New_York")


# ------------------------------------------------------------------ schedule
def load_schedule(season):
    s = nflr.load_schedules([season]).to_pandas()
    s = s[s["game_type"] == "REG"].copy()
    s["kickoff"] = pd.to_datetime(s["gameday"] + " " + s["gametime"]).dt.tz_localize(ET)
    return s


def current_week(sched, now=None):
    """Week of the next game that hasn't kicked off yet."""
    now = now or datetime.now(ET)
    upcoming = sched[sched["kickoff"] > now]
    if upcoming.empty:
        return int(sched["week"].max())
    return int(upcoming.sort_values("kickoff").iloc[0]["week"])


def games_in_window(sched, hours, now=None):
    """Games kicking off in the next `hours` hours (and not started yet)."""
    now = now or datetime.now(ET)
    w = sched[(sched["kickoff"] > now) & (sched["kickoff"] <= now + timedelta(hours=hours))]
    return w


def season_for(now=None):
    now = now or datetime.now(ET)
    return now.year if now.month >= 3 else now.year - 1


# ------------------------------------------------------------------ player stats
def load_weekly(season, before_week=None):
    df = nflr.load_player_stats([season]).to_pandas()
    df = df[df["season_type"] == "REG"]
    if before_week is not None:
        df = df[df["week"] < before_week]
    return df


def _mode(week):
    if week <= 1:
        return "prior_only"
    if week in (2, 3):
        return "blend"
    return "current_only"


def stats_frames(season, week):
    """Return (frame used for defense numbers, frame used for player baselines)."""
    mode = _mode(week)
    cur = load_weekly(season, before_week=week) if mode != "prior_only" else None
    prior = load_weekly(season - 1) if mode != "current_only" else None
    if mode == "prior_only":
        return prior, prior, mode
    if mode == "current_only":
        return cur, cur, mode
    # blend: defense uses both seasons; player baselines use both too (recent games first)
    both = pd.concat([prior, cur], ignore_index=True)
    return both, both, mode


# ------------------------------------------------------------------ defense vs position
def defense_vs_position(df):
    """
    {(defense_team, position, stat_key): avg allowed per game}
    plus league averages {(position, stat_key): avg}.
    Same idea as the notebook: sum what each position group did against a
    defense in each game, then average over games.
    """
    cols = sorted({c for c, _, _ in config.PROP_STATS.values()})
    d = df[df["position"].isin(["QB", "RB", "WR", "TE"])]
    per_game = (
        d.groupby(["opponent_team", "season", "week", "position"])[cols]
        .sum()
        .reset_index()
    )
    allowed, league = {}, {}
    for key, (col, positions, _) in config.PROP_STATS.items():
        for pos in positions:
            g = per_game[per_game["position"] == pos]
            if g.empty:
                continue
            by_def = g.groupby("opponent_team")[col].mean()
            league[(pos, key)] = float(by_def.mean())
            for team, v in by_def.items():
                allowed[(team, pos, key)] = float(v)
    return allowed, league


# ------------------------------------------------------------------ player baselines
def player_baselines(df):
    """
    One row per (player, stat): recent average, std dev, games, last-10 values,
    plus the player's current team and position.
    """
    df = df.sort_values(["season", "week"])
    latest = df.groupby("player_id").tail(1).set_index("player_id")
    out = []
    for pid, g in df.groupby("player_id"):
        pos = latest.loc[pid, "position"]
        if pos not in ("QB", "RB", "WR", "TE"):
            continue
        for key, (col, positions, _) in config.PROP_STATS.items():
            if pos not in positions:
                continue
            vals = g[col].fillna(0).tolist()
            recent = vals[-config.BASELINE_GAMES:]
            if len(recent) < config.MIN_GAMES:
                continue
            mean = sum(recent) / len(recent)
            if mean < config.MIN_BASELINE.get(key, 0.1):
                continue
            sd = pd.Series(vals[-10:]).std() if len(vals) >= 3 else None
            out.append({
                "player_id": pid,
                "player": latest.loc[pid, "player_display_name"],
                "team": latest.loc[pid, "team"],
                "position": pos,
                "headshot": latest.loc[pid, "headshot_url"],
                "stat": key,
                "baseline": round(mean, 1),
                "sd": round(float(sd), 1) if sd and sd == sd else None,
                "games": len(recent),
                "last10": [round(float(v), 1) for v in vals[-10:]],
            })
    return pd.DataFrame(out)
