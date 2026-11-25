"""
College basketball build: moneyline / spread / total only, Tue/Wed/Fri/Sat.
EV vs Pinnacle (same rules as NFL/NBA) + a team rating model as the cheat sheet.
Writes docs/data/cbb/*.json.

  python -m pipeline.cbb.build [--force]
"""
import argparse
import os
from contextlib import contextmanager
from datetime import datetime

from .. import build as pb
from .. import config as shared
from .. import ev, gamelines, notify, odds
from ..stats import ET
from . import config, stats

RAW = lambda n, h=None: n            # noqa: E731  keep feed team names; matched per game below


@contextmanager
def cbb_data():
    old = pb.DATA
    pb.DATA = os.path.join(old, "cbb")
    try:
        yield
    finally:
        pb.DATA = old


def build(now=None, force=False):
    with cbb_data():
        return _build(now or datetime.now(ET), force)


def _orient(lines, match):
    """Map feed lines onto ESPN's games/home-away. Unmatched games are dropped (never guessed)."""
    out, unmatched = [], set()
    for g in lines:
        row, swapped = match(g["home"], g["away"])
        if row is None:
            unmatched.add(f"{g['away']} @ {g['home']}")
            continue
        g = dict(g, home=row["home_team"], away=row["away_team"], game_id=row["game_id"])
        if swapped:
            if g["kind"] in ("ml", "spread"):
                g["a"], g["b"] = g["b"], g["a"]
            if g["kind"] == "spread" and g["line"] is not None:
                g["line"] = -g["line"]
        out.append(g)
    return out, sorted(unmatched)


def _build(now, force):
    season = stats.season_for(now)
    sched = stats.load_schedule(season)
    sl = stats.slate(sched)
    day = stats.next_day(sl, now)
    day_games = sl[(sl["kickoff"].dt.date.map(lambda d: d.isoformat()) == day) & (sl["kickoff"] > now)] \
        if day else sl.iloc[0:0]

    state = pb._load("../state.json", {})          # PinnWire budget shared by all sports
    today = now.date().isoformat()
    if state.get("pinn_day") != today:
        state.update(pinn_day=today, pinn_calls=0)

    prior = stats.load_schedule(season - 1)
    ratings, mu, hca = stats.team_ratings(sched, prior, now)
    gm = stats.project_games(day_games, ratings, mu, hca)
    gm_by_id = {m["game_id"]: m for m in gm.values()}

    soon = stats.games_in_window(sl, shared.PREGAME_WINDOW_HOURS, now)
    fixtures = bool(os.environ.get("ODDS_FIXTURES"))
    want_odds = force or not soon.empty or (fixtures and bool(day))
    status = {"odds_checked": False, "reason": f"no game within {shared.PREGAME_WINDOW_HOURS}h"}
    b_games, p_games = [], []
    match = stats.matcher(day_games)
    if want_odds:
        status = {"odds_checked": True}
        try:
            _, raw_b = odds.fetch_sharpapi(config.SHARP_LEAGUE, config.SHARP_MARKETS, lambda t: None, RAW,
                                           "cbb_sharpapi_odds.json")
            b_games, miss_b = _orient(raw_b, match)
            status.update(book_game_lines=len(b_games), book_unmatched=len(miss_b), book_unmatched_games=miss_b[:20])
        except Exception as e:
            status["book_error"] = str(e)[:200]
        # Pinnacle: reuse the NBA run's basketball call when fresh, else one fixtures call
        cache = pb._load("pinn_cache.json", {})
        age = ((now - datetime.fromisoformat(cache["at"])).total_seconds() / 60) if cache.get("at") else 1e9
        raw_p = None
        try:
            if fixtures:
                raw_p = odds._pinn_game_lines(odds._fixture("cbb_pinnwire_fixtures.json"), config.PINN_LEAGUE, RAW)
            elif age < config.PINN_REFRESH_MIN and not force:
                raw_p = cache.get("games", [])
            elif state["pinn_calls"] + 1 <= pb.PINN_DAILY_CAP:
                main = odds.fetch_pinnacle_fixtures(config.PINN_SPORT_ID)
                state["pinn_calls"] += 1
                raw_p = odds._pinn_game_lines(main, config.PINN_LEAGUE, RAW)
                pb._save("pinn_cache.json", {"at": pb._iso(now), "games": raw_p})
                age = 0
            elif age <= shared.PINN_MAX_AGE_MIN:
                raw_p = cache.get("games", [])
            else:
                status["pinnacle_stale"] = "daily PinnWire budget used and last prices are too old"
        except Exception as e:
            status["pinnacle_error"] = str(e)[:200]
        if raw_p:
            p_games, miss_p = _orient(raw_p, match)
            status.update(pinnacle_game_lines=len(p_games), pinnacle_unmatched=len(miss_p),
                          pinnacle_as_of=pb._iso(now) if fixtures or age == 0 else cache.get("at"))

    pinn_game = {(g["kind"], g["home"], g["away"], g["line"]): g for g in p_games}
    game_books = {}
    for g in b_games + p_games:
        game_books.setdefault((g["kind"], g["home"], g["away"], g["line"]), []).append(
            {"book": g["book"], "a": g["a"], "b": g["b"]})
    book_game_pairs = {(g["book"], g["kind"], g["home"], g["away"], g["line"]): (g["a"], g["b"]) for g in b_games}

    ev_picks, cheat_picks = [], []
    for g in b_games:
        m = gm_by_id.get(g["game_id"])
        row_kick = next((r["kickoff"] for _, r in day_games.iterrows() if r["game_id"] == g["game_id"]), None)
        kickoff = m["kickoff"] if m else (row_kick.isoformat() if row_kick is not None else None)
        if not kickoff or not pb._after(kickoff, now):
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
            p = {"kind": g["kind"], "week": day, "player": f"{g['away']} @ {g['home']}",
                 "pkey": g["game_id"], "stat": g["kind"], "stat_label": pb.KIND_LABEL[g["kind"]],
                 "title": title, "line": g["line"], "side": side, "team": team, "opp": opp,
                 "book": g["book"], "deep_link": g.get("deep_link"), "game_id": g["game_id"],
                 "kickoff": kickoff, "home": g["home"], "away": g["away"],
                 "dec": round(dec, 3), "american": ev.decimal_to_american(dec),
                 "game_model": m or {"home": g["home"], "away": g["away"]},
                 "books": game_books.get((g["kind"], g["home"], g["away"], g["line"]), [])}
            if m:
                implied = (own[0] if i == 0 else own[1]) if own else 1 / dec
                p.update(gamelines.evaluate(m, g["kind"], side, g["line"], implied, config))
            if fair:
                fa_, fb_ = ev.decimal_to_american(fair["a"]), ev.decimal_to_american(fair["b"])
                if g["kind"] == "total":
                    p["pinnacle_line"] = f"{fair['line']} o{fa_}/u{fb_}"
                elif g["kind"] == "spread":
                    p["pinnacle_line"] = (f"{g['home']} {pb._fmt_line(fair['line'])} {fa_} / "
                                          f"{g['away']} {pb._fmt_line(-fair['line'])} {fb_}")
                else:
                    p["pinnacle_line"] = f"{g['home']} {fa_:+d} / {g['away']} {fb_:+d}"
            second = pb.other_book(book_game_pairs, g["book"], (g["kind"], g["home"], g["away"], g["line"]))
            if pb._price_side(p, dec, (fair["a"], fair["b"]) if fair else None, i == 0,
                              second_pair=second, shin=False):
                ev_picks.append(dict(p))
            if p.get("cheat_side") == side:
                cheat_picks.append(dict(p))

    ev_picks = pb.best_by(ev_picks, lambda p: (p["pkey"], p["stat"], p["side"], p["line"]), lambda p: p["ev_pct"])
    cheat_picks = pb.best_by(cheat_picks, lambda p: (p["pkey"], p["stat"], p["side"]),
                             lambda p: (abs(p.get("model_edge") or 0), p["dec"]))
    combined = [dict(p) for p in ev_picks if p.get("cheat_side") == p["side"]]
    slate_ev, _ = pb.finish_ev(ev_picks, "cbb", pb._load("log.json", {"picks": {}}))
    sized = {(p["pkey"], p["stat"], p["side"], p["line"]): p for p in ev_picks}
    for c in combined:
        src = sized[(c["pkey"], c["stat"], c["side"], c["line"])]
        c.update({k: src.get(k) for k in ("units", "sizing", "lane", "lane_status", "lane_info", "exp_profit_units")})
    combined.sort(key=lambda p: (-p["exp_profit_units"], -p["ev_pct"]))
    cheat_picks.sort(key=lambda p: -abs(p.get("model_edge") or 0))

    games_list = [{"game_id": g["game_id"], "label": f"{g['away_team']} @ {g['home_team']}",
                   "kickoff": g["kickoff"].isoformat()} for _, g in day_games.iterrows()]
    nxt = sl[sl["kickoff"] > now].head(1)
    out = {
        "sport": "cbb", "generated_at": pb._iso(now), "season": season, "week": day, "day": day,
        "next_kickoff": pb._iso(nxt.iloc[0]["kickoff"]) if len(nxt) else None, "status": status,
        "settings": {"unit_pct": shared.UNIT_PCT_OF_BANKROLL, "kelly": shared.KELLY_FRACTION,
                     "max_units": shared.MAX_UNITS_PER_BET, "unit_usd": shared.UNIT_USD,
                     "max_units_prop": shared.MAX_UNITS["prop"], "max_units_line": shared.MAX_UNITS["line"],
                     "suspect_ev": shared.SUSPECT_EV_PCT, "suspect_units": shared.SUSPECT_MAX_UNITS,
                     "min_ev": shared.MIN_EV_PCT, "game_weight": config.GAME_MODEL_WEIGHT,
                     "home_field": config.HOME_FIELD_PTS, "lines_only": True},
        "logos": pb._safe(lambda: stats.team_logos(sched), {}),
        "games": games_list, "game_model": sorted(gm.values(), key=lambda m: m["kickoff"]),
        "combined": combined, "ev": ev_picks, "cheat": cheat_picks, "matchups": [],
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
    if want_odds:
        pb.update_log(out, {}, pinn_game, now)
        try:
            n = notify.notify_new(out, pb._load, pb._save, sport="CBB")
            if n:
                print(f"CBB: alerted {n} new EV plays")
        except Exception as e:
            print("notify error:", e)
    pb._save("../state.json", state)
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    o = build(force=ap.parse_args().force)
    print(f"CBB {o['day']}: combined={len(o['combined'])} ev={len(o['ev'])} cheat={len(o['cheat'])} "
          f"status={o['status']}")
