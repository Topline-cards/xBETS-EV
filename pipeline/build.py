"""
Main job. Run by GitHub Actions every ~10 minutes.

  python -m pipeline.build            # normal run (odds only near kickoff)
  python -m pipeline.build --force    # pull odds now, whatever the time

Writes:
  docs/data/picks.json   what the app shows right now
  docs/data/log.json     paper-trading log (every pick, graded later)
  docs/data/state.json   API call counters
"""
import argparse
import json
import os
from datetime import datetime

from . import config, ev, matchups, odds, stats
from .stats import ET

DATA = os.environ.get("DATA_DIR") or os.path.join(os.path.dirname(__file__), "..", "docs", "data")
PINN_DAILY_CAP = 90            # PinnWire free plan = 100/day; keep a buffer


def _load(name, default):
    p = os.path.join(DATA, name)
    if os.path.exists(p):
        with open(p) as f:
            return json.load(f)
    return default


def _save(name, obj):
    os.makedirs(DATA, exist_ok=True)
    with open(os.path.join(DATA, name), "w") as f:
        json.dump(obj, f, indent=1, default=str)


def _iso(dt):
    return dt.isoformat(timespec="seconds")


def _after(iso, now):
    """True if ISO time string is later than now."""
    return bool(iso) and datetime.fromisoformat(str(iso).replace("Z", "+00:00")) > now


# ------------------------------------------------------------------ helpers
def best_by(rows, keyfn, scorefn):
    best = {}
    for r in rows:
        k = keyfn(r)
        if k not in best or scorefn(r) > scorefn(best[k]):
            best[k] = r
    return list(best.values())


def pick_key(mode, p):
    return f"{mode}|{p['week']}|{p['pkey']}|{p['stat']}|{p['side']}|{p['line']}|{p['book']}"


# ------------------------------------------------------------------ main build
def build(now=None, force=False):
    now = now or datetime.now(ET)
    season = stats.season_for(now)
    sched = stats.load_schedule(season)
    week = stats.current_week(sched, now)
    week_games = sched[(sched["week"] == week) & (sched["kickoff"] > now)]

    state = _load("state.json", {})
    today = now.date().isoformat()
    if state.get("pinn_day") != today:
        state.update(pinn_day=today, pinn_calls=0)

    # ---- cheat sheet (always) ----------------------------------------
    def_df, base_df, mode = stats.stats_frames(season, week)
    allowed, league = stats.defense_vs_position(def_df)
    baselines = stats.player_baselines(base_df)
    proj = matchups.build_projections(baselines, allowed, league, week_games)
    for r in proj:
        r["def_rank"] = matchups.rank_of(allowed, r["opp"], r["position"], r["stat"])
    proj_by = {(odds.pkey(r["player"]), r["stat"]): r for r in proj}

    # ---- odds (only near kickoff, unless forced) --------------------
    soon = stats.games_in_window(sched, config.PREGAME_WINDOW_HOURS, now)
    want_odds = force or not soon.empty
    status = {"odds_checked": False, "reason": "no game within "
              f"{config.PREGAME_WINDOW_HOURS}h"}
    books, pinn = [], []
    if want_odds:
        status = {"odds_checked": True}
        try:
            books = odds.fetch_sharpapi_props()
            status["book_props"] = len(books)
        except Exception as e:                     # keep the app alive
            status["book_error"] = str(e)[:200]
        if state["pinn_calls"] < PINN_DAILY_CAP:
            try:
                pinn = odds.fetch_pinnacle_props()
                state["pinn_calls"] += 1
                status["pinnacle_props"] = len(pinn)
            except Exception as e:
                status["pinnacle_error"] = str(e)[:200]
        else:
            status["pinnacle_error"] = "daily PinnWire budget used"
    elif os.environ.get("ODDS_FIXTURES"):
        books, pinn = odds.fetch_sharpapi_props(), odds.fetch_pinnacle_props()

    pinn_by = {(p["pkey"], p["stat"], p["line"]): p for p in pinn
               if p["over"] and p["under"]}

    ev_picks, cheat_picks = [], []
    for b in books:
        pr = proj_by.get((b["pkey"], b["stat"]))
        info = {
            "week": week, "player": pr["player"] if pr else b["player"],
            "pkey": b["pkey"], "stat": b["stat"],
            "stat_label": config.PROP_STATS[b["stat"]][2],
            "line": b["line"], "book": b["book"], "deep_link": b.get("deep_link"),
            "team": pr["team"] if pr else None, "opp": pr["opp"] if pr else None,
            "position": pr["position"] if pr else None,
            "game_id": pr["game_id"] if pr else b.get("start"),
            "kickoff": pr["kickoff"] if pr else b.get("start"),
            "headshot": pr["headshot"] if pr else None,
        }
        if pr:
            read = matchups.evaluate_line(pr, b["line"])
            info.update(read)
            info.update({k: pr[k] for k in ("projection", "baseline", "matchup_pct",
                                             "def_rank", "last10", "def_allowed",
                                             "league_avg", "games")})

        fair = pinn_by.get((b["pkey"], b["stat"], b["line"]))
        for side in ("over", "under"):
            dec = b[side]
            if not dec:
                continue
            p = dict(info, side=side, dec=round(dec, 3),
                     american=ev.decimal_to_american(dec))
            if fair:
                fo, fu = ev.devig_two_way(fair["over"], fair["under"])
                fp = fo if side == "over" else fu
                e = ev.ev_pct(fp, dec)
                units, kelly = ev.kelly_units(fp, dec)
                p.update(fair_prob=round(fp, 4), ev_pct=round(e, 2),
                         kelly_pct=kelly, units=units,
                         pinnacle_line=f"{fair['line']} "
                         f"o{ev.decimal_to_american(fair['over'])}"
                         f"/u{ev.decimal_to_american(fair['under'])}")
                if (config.MIN_EV_PCT <= e <= config.MAX_EV_PCT and units > 0
                        and p["american"] <= config.MAX_ODDS_AMERICAN):
                    ev_picks.append(dict(p))
            if pr and p.get("cheat_side") == side:
                cheat_picks.append(dict(p))

    # one row per player/stat/side/line: the better of DK / FD
    ev_picks = best_by(ev_picks, lambda p: (p["pkey"], p["stat"], p["side"], p["line"]),
                       lambda p: p["ev_pct"])
    cheat_picks = best_by(cheat_picks, lambda p: (p["pkey"], p["stat"], p["side"]),
                          lambda p: (abs(p["proj_edge_pct"]), p["dec"]))
    combined = [dict(p) for p in ev_picks if p.get("cheat_side") == p["side"]]

    ev.cap_per_game(ev_picks)
    ev.cap_per_game(combined)
    ev_picks.sort(key=lambda p: -p["ev_pct"])
    combined.sort(key=lambda p: -p["ev_pct"])
    cheat_picks.sort(key=lambda p: -abs(p["proj_edge_pct"]))

    # top matchups even when no lines are posted yet
    top = sorted(proj, key=lambda r: -abs(r["matchup_pct"]))
    top = [r for r in top if r["games"] >= config.MIN_GAMES][:60]

    nxt = sched[sched["kickoff"] > now].sort_values("kickoff").head(1)
    out = {
        "generated_at": _iso(now), "season": season, "week": week, "stats_mode": mode,
        "next_kickoff": _iso(nxt.iloc[0]["kickoff"]) if len(nxt) else None,
        "status": status, "settings": {
            "unit_pct": config.UNIT_PCT_OF_BANKROLL, "kelly": config.KELLY_FRACTION,
            "max_units": config.MAX_UNITS_PER_BET, "min_ev": config.MIN_EV_PCT},
        "combined": combined, "ev": ev_picks, "cheat": cheat_picks,
        "matchups": top,
    }
    # Keep the last good odds board if this run didn't pull odds
    if not want_odds and not os.environ.get("ODDS_FIXTURES"):
        prev = _load("picks.json", {})
        if prev.get("week") == week:
            for k in ("combined", "ev", "cheat"):
                out[k] = [p for p in prev.get(k, [])
                          if _after(p.get("kickoff"), now)]
            out["odds_as_of"] = prev.get("odds_as_of")
    else:
        out["odds_as_of"] = _iso(now)

    _save("picks.json", out)
    update_log(out, pinn_by, now)
    _save("state.json", state)
    return out


# ------------------------------------------------------------------ paper log
def update_log(out, pinn_by, now):
    log = _load("log.json", {"picks": {}})
    picks = log["picks"]
    if out.get("odds_as_of") != _iso(now):
        return
    for mode in ("combined", "ev", "cheat"):
        for p in out[mode]:
            k = pick_key(mode, p)
            units = p.get("units") if mode != "cheat" else 1.0
            if not units:
                continue                    # trimmed to 0 by the game cap
            if k not in picks:
                picks[k] = {
                    "mode": mode, "first_seen": _iso(now), "week": p["week"],
                    "player": p["player"], "pkey": p["pkey"], "stat": p["stat"],
                    "stat_label": p["stat_label"], "side": p["side"],
                    "line": p["line"], "book": p["book"], "dec": p["dec"],
                    "american": p["american"], "units": units,
                    "ev_pct": p.get("ev_pct"), "fair_prob": p.get("fair_prob"),
                    "kickoff": p["kickoff"], "team": p.get("team"), "opp": p.get("opp"),
                    "result": None,
                }
    # closing line: latest Pinnacle fair price before kickoff
    for k, rec in picks.items():
        if rec["result"] is not None or not _after(rec["kickoff"], now):
            continue
        f = pinn_by.get((rec["pkey"], rec["stat"], rec["line"]))
        if f:
            fo, fu = ev.devig_two_way(f["over"], f["under"])
            rec["close_fair_prob"] = round(fo if rec["side"] == "over" else fu, 4)
            rec["clv_pct"] = round(ev.ev_pct(rec["close_fair_prob"], rec["dec"]), 2)
    _save("log.json", log)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    o = build(force=a.force)
    print(f"Week {o['week']}: combined={len(o['combined'])} ev={len(o['ev'])} "
          f"cheat={len(o['cheat'])} status={o['status']}")
