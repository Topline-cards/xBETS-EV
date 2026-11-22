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
TEAM_NICK = {
    "Cardinals": "ARI", "Falcons": "ATL", "Ravens": "BAL", "Bills": "BUF", "Panthers": "CAR",
    "Bears": "CHI", "Bengals": "CIN", "Browns": "CLE", "Cowboys": "DAL", "Broncos": "DEN",
    "Lions": "DET", "Packers": "GB", "Texans": "HOU", "Colts": "IND", "Jaguars": "JAX",
    "Chiefs": "KC", "Rams": "LA", "Chargers": "LAC", "Raiders": "LV", "Dolphins": "MIA",
    "Vikings": "MIN", "Patriots": "NE", "Saints": "NO", "Giants": "NYG", "Jets": "NYJ",
    "Eagles": "PHI", "Steelers": "PIT", "Seahawks": "SEA", "49ers": "SF", "Buccaneers": "TB",
    "Titans": "TEN", "Commanders": "WAS",
}
ABBR_ALIASES = {"LAR": "LA", "JAC": "JAX", "WSH": "WAS", "OAK": "LV", "SD": "LAC"}


def team_abbr(name, abbr=None):
    """'Philadelphia Eagles' / 'PHI Eagles' / 'PHI' -> 'PHI' (nflverse codes)."""
    if abbr:
        a = ABBR_ALIASES.get(abbr.upper(), abbr.upper())
        if a in TEAM_NICK.values():
            return a
    for nick, a in TEAM_NICK.items():
        if name and nick.lower() in name.lower():
            return a
    return None


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
    # who each defense played that week (the offense team)
    offense = d.groupby(["opponent_team", "season", "week"])["team"].first()
    allowed, league = {}, {}
    defense_vs_position.logs = {}
    for key, (col, positions, _) in config.PROP_STATS.items():
        for pos in positions:
            g = per_game[per_game["position"] == pos]
            if g.empty:
                continue
            by_def = g.groupby("opponent_team")[col].mean()
            league[(pos, key)] = float(by_def.mean())
            for team, v in by_def.items():
                allowed[(team, pos, key)] = float(v)
            # per-game log: what this defense allowed to the position each week
            for team, gg in g.sort_values(["season", "week"]).groupby("opponent_team"):
                defense_vs_position.logs[(team, pos, key)] = [
                    {"season": int(r.season), "wk": int(r.week),
                     "vs": offense.get((team, r.season, r.week), ""),
                     "v": round(float(getattr(r, col) or 0), 1)}
                    for r in gg.itertuples()][-8:]
    return allowed, league


def _home_away(game_id, team):
    """nflverse game_id = SEASON_WEEK_AWAY_HOME."""
    parts = str(game_id).split("_")
    if len(parts) >= 4:
        return "home" if parts[-1] == team else "away"
    return None


def history_vs(season, seasons_back=3):
    """
    Player stat lines from the last few seasons, for 'vs this opponent' trends.
    Returns {(player_id, opponent): [{"season","wk","v": {stat: value}}...]}
    """
    df = nflr.load_player_stats(list(range(season - seasons_back + 1, season + 1))).to_pandas()
    df = df[df["season_type"] == "REG"].sort_values(["season", "week"])
    cols = {k: c for k, (c, _, _) in config.PROP_STATS.items()}
    out = {}
    for r in df.itertuples():
        out.setdefault((r.player_id, r.opponent_team), []).append(
            {"season": int(r.season), "wk": int(r.week),
             "v": {k: round(float(getattr(r, c) or 0), 1) for k, c in cols.items()}})
    return out


def team_logos():
    t = nflr.load_teams().to_pandas()
    return {ABBR_ALIASES.get(a, a): logo for a, logo in zip(t["team_abbr"], t["team_logo_espn"])
            if a not in ("OAK", "SD", "STL", "LAR")}


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
            log = [{"season": int(s), "wk": int(w), "opp": o, "v": round(float(v or 0), 1),
                    "ha": _home_away(gid, t)}
                   for s, w, o, v, gid, t in zip(g["season"], g["week"], g["opponent_team"],
                                                 g[col], g["game_id"], g["team"])][-10:]
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
                "log": log,
            })
    return pd.DataFrame(out)


# ------------------------------------------------------------------ team ratings
def team_ratings(sched_cur, sched_prior, week):
    """
    Points scored / allowed per game for each team, from final scores.
    Weeks 1-3 also include last season so there's enough data.
    Returns ({team: {"pf":, "pa":, "games":}}, league avg points per team-game).
    """
    frames = [sched_cur[(sched_cur["week"] < week) & sched_cur["home_score"].notna()]]
    if week <= 3 and sched_prior is not None:
        frames.append(sched_prior[sched_prior["home_score"].notna()])
    g = pd.concat(frames)
    rows = []
    for r in g.itertuples():
        rows.append((r.home_team, r.home_score, r.away_score))
        rows.append((r.away_team, r.away_score, r.home_score))
    df = pd.DataFrame(rows, columns=["team", "pf", "pa"])
    lg = float(df["pf"].mean()) if len(df) else 22.0
    out = {}
    for t, d in df.groupby("team"):
        out[t] = {"pf": round(float(d["pf"].mean()), 1), "pa": round(float(d["pa"].mean()), 1),
                  "games": int(len(d))}
    return out, round(lg, 2)
