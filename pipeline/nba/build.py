"""
NBA build: same idea as the NFL build (EV vs Pinnacle, cheat sheet, combined),
for Wednesday / Friday / Christmas games. Writes docs/data/nba/*.json.

  python -m pipeline.nba.build [--force]
"""
import argparse
import os
from contextlib import contextmanager
from datetime import datetime

from .. import build as pb
from .. import config as shared
from .. import ev, gamelines, matchups, notify, odds
from ..stats import ET
from . import config, stats

KIND_LABEL = pb.KIND_LABEL
PINN_DAILY_CAP = pb.PINN_DAILY_CAP


@contextmanager
def nba_data():
    """Point the shared load/save helpers at docs/data/nba while we work."""
    old = pb.DATA
    pb.DATA = os.path.join(old, "nba")
    try:
        yield
    finally:
        pb.DATA = old


def projections(baselines, allowed, league, games, avail):
    opp = {}
    for _, g in games.iterrows():
        k = g["kickoff"].isoformat()
        opp[g["home_team"]] = (g["away_team"], g["game_id"], k, "home")
        opp[g["away_team"]] = (g["home_team"], g["game_id"], k, "away")
    rows = []
    if baselines.empty:
        return rows
    lo, hi = config.MATCHUP_FACTOR_RANGE
    for r in baselines.itertuples():
        a = avail.get(r.pkey, {})
        if a.get("hide"):
            continue
        if r.team not in opp:
            continue
        opp_team, game_id, kickoff, side = opp[r.team]
        lg = league.get((r.position, r.stat))
        dv = allowed.get((opp_team, r.position, r.stat))
        if not lg or dv is None:
            continue
        factor = min(max(dv / lg, lo), hi)
        proj = r.baseline * (1 + config.MATCHUP_WEIGHT * (factor - 1))
        rows.append({
            "player": r.player, "player_id": r.player_id, "pkey": r.pkey, "team": r.team,
            "injury_status": a.get("status"), "injury": a.get("injury"),
            "opp": opp_team, "position": r.position, "stat": r.stat,
            "stat_label": config.PROP_STATS[r.stat][1],
            "game_id": game_id, "kickoff": kickoff, "home_away": side, "headshot": r.headshot,
            "baseline": r.baseline, "sd": r.sd, "games": r.games, "last10": r.last10, "log": r.log,
            "minutes": r.minutes,
            "def_allowed": round(dv, 1), "league_avg": round(lg, 1),
            "matchup_factor": round(factor, 3), "matchup_pct": round((factor - 1) * 100, 1),
            "projection": round(proj, 1),
        })
    return rows


def build(now=None, force=False):
    with nba_data():
        return _build(now or datetime.now(ET), force)


def _build(now, force):
    season = stats.season_for(now)
    sched = stats.load_schedule(season)
    sl = stats.slate(sched)
    day = stats.next_day(sl, now)
    day_games = sl[(sl["kickoff"].dt.date.map(lambda d: d.isoformat()) == day) & (sl["kickoff"] > now)] \
        if day else sl.iloc[0:0]

    state = pb._load("../state.json", {})          # PinnWire budget shared with the NFL
    today = now.date().isoformat()
    if state.get("pinn_day") != today:
        state.update(pinn_day=today, pinn_calls=0)

    box = stats.load_box(season)
    prior = stats.load_box(season - 1)
    allowed, league, dlogs = stats.defense_vs_position(box, prior)
    early = box.empty or box.groupby("team_abbreviation")["game_id"].nunique().median() < config.MIN_GAMES
    baselines = stats.player_baselines(box, prior if early else None)
    avail = stats.availability()
    proj = projections(baselines, allowed, league, day_games, avail)
    try:
        hist = stats.history_vs([stats.load_box(season - 2), prior, box])
    except Exception:
        hist = {}
    for r in proj:
        r["def_rank"] = matchups.rank_of(allowed, r["opp"], r["position"], r["stat"])
        r["def_n"] = 30
        r["def_log"] = dlogs.get((r["opp"], r["position"], r["stat"]), [])
        r["vs_opp"] = [{"season": h["season"], "wk": h["wk"], "lbl": h["lbl"], "v": h["v"][r["stat"]]}
                       for h in hist.get((r["player_id"], r["opp"]), []) if h["wk"] < (day or "9")][-6:]
    proj_by = {(r["pkey"], r["stat"]): r for r in proj}

    ratings, lg_pts = stats.team_ratings(box, prior)
    gm = gamelines.project_games(day_games, ratings, lg_pts, config)
    gm_by_teams = {(m["home"], m["away"]): m for m in gm.values()}

    # ---- odds ----------------------------------------------------------
    soon = stats.games_in_window(sl, shared.PREGAME_WINDOW_HOURS, now)
    fixtures = bool(os.environ.get("ODDS_FIXTURES"))
    want_odds = force or not soon.empty or (fixtures and bool(day))
    status = {"odds_checked": False, "reason": f"no game within {shared.PREGAME_WINDOW_HOURS}h"}
    b_props, b_games, p_props, p_games = [], [], [], []
    if want_odds:
        status = {"odds_checked": True}
        try:
            b_props, b_games = odds.fetch_sharpapi("nba", config.SHARP_MARKETS, stats.stat_from_text,
                                                   stats.team_abbr, "nba_sharpapi_odds.json")
            status.update(book_props=len(b_props), book_game_lines=len(b_games))
        except Exception as e:
            status["book_error"] = str(e)[:200]
        cache = pb._load("pinn_cache.json", {})
        age_min = ((now - datetime.fromisoformat(cache["at"])).total_seconds() / 60
                   if cache.get("at") else 1e9)
        fresh_enough = age_min < config.PINN_REFRESH_MIN and not force
        if fixtures or (not fresh_enough and state["pinn_calls"] + 2 <= PINN_DAILY_CAP):
            try:
                p_props, p_games = odds.fetch_pinnacle(config.PINN_SPORT_ID, "NBA", stats.stat_from_text,
                                                       stats.team_abbr, "nba_")
                state["pinn_calls"] += 0 if fixtures else 2
                if not fixtures:
                    pb._save("pinn_cache.json", {"at": pb._iso(now), "props": p_props, "games": p_games})
                status.update(pinnacle_props=len(p_props), pinnacle_game_lines=len(p_games))
            except Exception as e:
                status["pinnacle_error"] = str(e)[:200]
        if not p_props and not p_games and cache.get("at") and age_min < 90:
            p_props, p_games = cache.get("props", []), cache.get("games", [])
            status.update(pinnacle_props=len(p_props), pinnacle_game_lines=len(p_games),
                          pinnacle_as_of=cache["at"])

    pinn_prop = {(p["pkey"], p["stat"], p["line"]): p for p in p_props if p["over"] and p["under"]}
    pinn_game = {(g["kind"], g["home"], g["away"], g["line"]): g for g in p_games}
    prop_books, game_books = {}, {}
    for b in b_props + p_props:
        prop_books.setdefault((b["pkey"], b["stat"], b["line"]), []).append(
            {"book": b["book"], "over": b["over"], "under": b["under"]})
    for g in b_games + p_games:
        game_books.setdefault((g["kind"], g["home"], g["away"], g["line"]), []).append(
            {"book": g["book"], "a": g["a"], "b": g["b"]})

    ev_picks, cheat_picks = [], []
    for b in b_props:
        pr = proj_by.get((b["pkey"], b["stat"]))
        if not pr or not pb._after(pr["kickoff"], now):
            continue
        info = {"kind": "prop", "week": day, "player": pr["player"], "pkey": b["pkey"], "stat": b["stat"],
                "stat_label": config.PROP_STATS[b["stat"]][1], "line": b["line"], "book": b["book"],
                "deep_link": b.get("deep_link"), "books": prop_books.get((b["pkey"], b["stat"], b["line"]), [])}
        info.update({k: pr[k] for k in ("team", "opp", "position", "game_id", "kickoff", "headshot",
                                         "projection", "baseline", "matchup_pct", "matchup_factor",
                                         "def_rank", "def_n", "last10", "log", "def_log", "vs_opp",
                                         "home_away", "player_id", "injury_status", "injury",
                                         "def_allowed", "league_avg", "games", "minutes")})
        info.update(matchups.evaluate_line(pr, b["line"], config))
        fair = pinn_prop.get((b["pkey"], b["stat"], b["line"]))
        for side in ("over", "under"):
            dec = b[side]
            if not dec:
                continue
            p = dict(info, side=side, dec=round(dec, 3), american=ev.decimal_to_american(dec),
                     title=f"{'Over' if side == 'over' else 'Under'} {b['line']} {info['stat_label']}")
            if fair:
                p["pinnacle_line"] = (f"{fair['line']} o{ev.decimal_to_american(fair['over'])}"
                                      f"/u{ev.decimal_to_american(fair['under'])}")
            if pb._price_side(p, dec, (fair["over"], fair["under"]) if fair else None, side == "over"):
                ev_picks.append(dict(p))
            if p.get("cheat_side") == side:
                cheat_picks.append(dict(p))

    for g in b_games:
        m = gm_by_teams.get((g["home"], g["away"]))
        if not m or not pb._after(m["kickoff"], now):
            continue
        sides = ("over", "under") if g["kind"] == "total" else ("home", "away")
        own = ev.devig_two_way(g["a"], g["b"]) if g["a"] and g["b"] else None
        fair = pinn_game.get((g["kind"], g["home"], g["away"], g["line"]))
        for i, side in enumerate(sides):
            dec = g["a"] if i == 0 else g["b"]
            if not dec:
                continue
            team = g["home"] if side == "home" else g["away"] if side == "away" else None
            opp = g["away"] if side == "home" else g["home"] if side == "away" else None
            if g["kind"] == "ml":
                title = f"{team} Moneyline"
            elif g["kind"] == "spread":
                title = f"{team} {pb._fmt_line(g['line'] if side == 'home' else -g['line'])}"
            else:
                title = f"{'Over' if side == 'over' else 'Under'} {g['line']}"
            p = {"kind": g["kind"], "week": day, "player": f"{m['away']} @ {m['home']}",
                 "pkey": m["game_id"], "stat": g["kind"], "stat_label": KIND_LABEL[g["kind"]],
                 "title": title, "line": g["line"], "side": side, "team": team, "opp": opp,
                 "book": g["book"], "deep_link": g.get("deep_link"),
                 "game_id": m["game_id"], "kickoff": m["kickoff"], "home": m["home"], "away": m["away"],
                 "dec": round(dec, 3), "american": ev.decimal_to_american(dec), "game_model": m,
                 "books": game_books.get((g["kind"], g["home"], g["away"], g["line"]), [])}
            implied = (own[0] if i == 0 else own[1]) if own else 1 / dec
            p.update(gamelines.evaluate(m, g["kind"], side, g["line"], implied, config))
            if fair:
                fa_, fb_ = ev.decimal_to_american(fair["a"]), ev.decimal_to_american(fair["b"])
                if g["kind"] == "total":
                    p["pinnacle_line"] = f"{fair['line']} o{fa_}/u{fb_}"
                elif g["kind"] == "spread":
                    p["pinnacle_line"] = (f"{m['home']} {pb._fmt_line(fair['line'])} {fa_} / "
                                          f"{m['away']} {pb._fmt_line(-fair['line'])} {fb_}")
                else:
                    p["pinnacle_line"] = f"{m['home']} {fa_:+d} / {m['away']} {fb_:+d}"
            if pb._price_side(p, dec, (fair["a"], fair["b"]) if fair else None, i == 0):
                ev_picks.append(dict(p))
            if p.get("cheat_side") == side:
                cheat_picks.append(dict(p))

    ev_picks = pb.best_by(ev_picks, lambda p: (p["pkey"], p["stat"], p["side"], p["line"]), lambda p: p["ev_pct"])
    cheat_picks = pb.best_by(cheat_picks, lambda p: (p["pkey"], p["stat"], p["side"]),
                             lambda p: (abs(p.get("proj_edge_pct") or p.get("model_edge") or 0), p["dec"]))
    combined = [dict(p) for p in ev_picks if p.get("cheat_side") == p["side"]]
    slate_ev = ev.size_slate(ev_picks)
    sized = {(p["pkey"], p["stat"], p["side"], p["line"]): p for p in ev_picks}
    for c in combined:
        src = sized[(c["pkey"], c["stat"], c["side"], c["line"])]
        c.update(units=src["units"], sizing=src["sizing"])
    ev_picks.sort(key=lambda p: -p["ev_pct"])
    combined.sort(key=lambda p: -p["ev_pct"])
    cheat_picks.sort(key=lambda p: (p["kind"] != "prop", -abs(p.get("proj_edge_pct") or p.get("model_edge") or 0)))

    top = sorted(proj, key=lambda r: -abs(r["matchup_pct"]))
    top = [r for r in top if r["games"] >= config.MIN_GAMES][:80]
    games_list = [{"game_id": g["game_id"], "label": f"{g['away_team']} @ {g['home_team']}",
                   "kickoff": g["kickoff"].isoformat()} for _, g in day_games.iterrows()]
    nxt = sl[sl["kickoff"] > now].head(1)
    out = {
        "sport": "nba", "generated_at": pb._iso(now), "season": season, "week": day, "day": day,
        "next_kickoff": pb._iso(nxt.iloc[0]["kickoff"]) if len(nxt) else None,
        "status": status, "settings": {
            "unit_pct": shared.UNIT_PCT_OF_BANKROLL, "kelly": shared.KELLY_FRACTION,
            "max_units": shared.MAX_UNITS_PER_BET, "unit_usd": shared.UNIT_USD,
            "max_units_prop": shared.MAX_UNITS["prop"], "max_units_line": shared.MAX_UNITS["line"],
            "suspect_ev": shared.SUSPECT_EV_PCT, "suspect_units": shared.SUSPECT_MAX_UNITS,
            "min_ev": shared.MIN_EV_PCT, "matchup_weight": config.MATCHUP_WEIGHT,
            "game_weight": config.GAME_MODEL_WEIGHT, "baseline_games": config.BASELINE_GAMES,
            "home_field": config.HOME_FIELD_PTS},
        "logos": pb._safe(lambda: stats.team_logos(sched), {}),
        "games": games_list, "game_model": sorted(gm.values(), key=lambda m: m["kickoff"]),
        "combined": combined, "ev": ev_picks, "cheat": cheat_picks, "matchups": top,
        "slate_units": {"ev": slate_ev, "combined": round(sum(c["units"] for c in combined), 2)},
    }
    if not want_odds:
        prev = pb._load("picks.json", {})
        if prev.get("week") == day:
            for k in ("combined", "ev", "cheat"):
                out[k] = [p for p in prev.get(k, []) if pb._after(p.get("kickoff"), now)]
            out["odds_as_of"] = prev.get("odds_as_of")
    else:
        out["odds_as_of"] = pb._iso(now)
    pb._save("picks.json", out)

    players = {}
    if not baselines.empty:
        for r in baselines.itertuples():
            players.setdefault(r.pkey, {"pkey": r.pkey, "name": r.player, "team": r.team,
                                        "pos": r.position, "headshot": r.headshot})
    pb._save("players.json", sorted(players.values(), key=lambda x: x["name"]))
    if want_odds:
        pb.update_log(out, pinn_prop, pinn_game, now)
        try:
            n = notify.notify_new(out, pb._load, pb._save, sport="NBA")
            if n:
                print(f"NBA: alerted {n} new EV plays")
        except Exception as e:
            print("notify error:", e)
    pb._save("../state.json", state)
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    o = build(force=ap.parse_args().force)
    print(f"NBA {o['day']}: combined={len(o['combined'])} ev={len(o['ev'])} cheat={len(o['cheat'])} "
          f"status={o['status']}")
