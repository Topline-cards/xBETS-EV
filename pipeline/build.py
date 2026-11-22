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

from . import config, ev, gamelines, matchups, odds, stats
from .stats import ET

DATA = os.environ.get("DATA_DIR") or os.path.join(os.path.dirname(__file__), "..", "docs", "data")
PINN_DAILY_CAP = 90            # PinnWire free plan = 100/day; keep a buffer
KIND_LABEL = {"ml": "Moneyline", "spread": "Spread", "total": "Total"}


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


def _safe(fn, default):
    try:
        return fn()
    except Exception:
        return default


def best_by(rows, keyfn, scorefn):
    best = {}
    for r in rows:
        k = keyfn(r)
        if k not in best or scorefn(r) > scorefn(best[k]):
            best[k] = r
    return list(best.values())


def pick_key(mode, p):
    return f"{mode}|{p['week']}|{p['pkey']}|{p['stat']}|{p['side']}|{p['line']}|{p['book']}"


def _fmt_line(x):
    return f"{x:+g}" if x else "PK"


# ------------------------------------------------------------------ EV for one side
def _price_side(p, dec, fair_pair, side_is_a):
    """Fill EV / units fields on pick p from Pinnacle's two prices."""
    if not fair_pair:
        return False
    fa, fb = ev.devig_two_way(*fair_pair)
    fp = fa if side_is_a else fb
    e = ev.ev_pct(fp, dec)
    units, kelly, trusted, note = ev.kelly_units(fp, dec, p.get("kind", "prop"))
    p.update(fair_prob=round(fp, 4), ev_pct=round(e, 2), kelly_pct=kelly, units=units,
             trusted_prob=trusted, unit_note=note,
             edge_trust=config.EDGE_TRUST.get(p.get("kind", "prop"), 0.5),
             pinn_dec=round(fair_pair[0] if side_is_a else fair_pair[1], 3))
    return (config.MIN_EV_PCT <= e <= config.MAX_EV_PCT and units > 0
            and p["american"] <= config.MAX_ODDS_AMERICAN)


# ------------------------------------------------------------------ main build
def build(now=None, force=False):
    now = now or datetime.now(ET)
    season = stats.season_for(now)
    sched = stats.load_schedule(season)
    week = stats.current_week(sched, now)
    week_games = sched[(sched["week"] == week) & (sched["kickoff"] > now)]
    prior_sched = stats.load_schedule(season - 1) if week <= 3 else None

    state = _load("state.json", {})
    today = now.date().isoformat()
    if state.get("pinn_day") != today:
        state.update(pinn_day=today, pinn_calls=0)

    # ---- player cheat sheet ----------------------------------------
    def_df, base_df, mode = stats.stats_frames(season, week)
    allowed, league = stats.defense_vs_position(def_df)
    baselines = stats.player_baselines(base_df)
    proj = matchups.build_projections(baselines, allowed, league, week_games)
    for r in proj:
        r["def_rank"] = matchups.rank_of(allowed, r["opp"], r["position"], r["stat"])
        r["def_log"] = stats.defense_vs_position.logs.get((r["opp"], r["position"], r["stat"]), [])
    try:
        hist = stats.history_vs(season)
    except Exception:
        hist = {}
    for r in proj:
        r["pkey"] = odds.pkey(r["player"])
        games_vs = hist.get((r["player_id"], r["opp"]), [])
        # only games before this week
        r["vs_opp"] = [{"season": h["season"], "wk": h["wk"], "v": h["v"][r["stat"]]}
                       for h in games_vs if (h["season"], h["wk"]) < (season, week)][-6:]
    proj_by = {(odds.pkey(r["player"]), r["stat"]): r for r in proj}

    # ---- game-line model --------------------------------------------
    ratings, lg_pts = stats.team_ratings(sched, prior_sched, week)
    gm = gamelines.project_games(week_games, ratings, lg_pts)
    gm_by_teams = {(m["home"], m["away"]): m for m in gm.values()}

    # ---- odds (only near kickoff, unless forced) --------------------
    soon = stats.games_in_window(sched, config.PREGAME_WINDOW_HOURS, now)
    fixtures = bool(os.environ.get("ODDS_FIXTURES"))
    want_odds = force or not soon.empty or fixtures
    status = {"odds_checked": False,
              "reason": f"no game within {config.PREGAME_WINDOW_HOURS}h"}
    b_props, b_games, p_props, p_games = [], [], [], []
    if want_odds:
        status = {"odds_checked": True}
        try:
            b_props, b_games = odds.fetch_sharpapi()
            status.update(book_props=len(b_props), book_game_lines=len(b_games))
        except Exception as e:                     # keep the app alive
            status["book_error"] = str(e)[:200]
        if fixtures or state["pinn_calls"] < PINN_DAILY_CAP:
            try:
                p_props, p_games = odds.fetch_pinnacle()
                state["pinn_calls"] += 0 if fixtures else 1
                status.update(pinnacle_props=len(p_props), pinnacle_game_lines=len(p_games))
            except Exception as e:
                status["pinnacle_error"] = str(e)[:200]
        else:
            status["pinnacle_error"] = "daily PinnWire budget used"

    pinn_prop = {(p["pkey"], p["stat"], p["line"]): p for p in p_props
                 if p["over"] and p["under"]}
    pinn_game = {(g["kind"], g["home"], g["away"], g["line"]): g for g in p_games}

    # every book's price on the same bet, for the detail view
    prop_books, game_books = {}, {}
    for b in b_props + p_props:
        prop_books.setdefault((b["pkey"], b["stat"], b["line"]), []).append(
            {"book": b["book"], "over": b["over"], "under": b["under"]})
    for g in b_games + p_games:
        game_books.setdefault((g["kind"], g["home"], g["away"], g["line"]), []).append(
            {"book": g["book"], "a": g["a"], "b": g["b"]})

    ev_picks, cheat_picks = [], []

    # ---- player props -------------------------------------------------
    for b in b_props:
        pr = proj_by.get((b["pkey"], b["stat"]))
        info = {
            "kind": "prop", "week": week, "player": pr["player"] if pr else b["player"],
            "pkey": b["pkey"], "stat": b["stat"],
            "stat_label": config.PROP_STATS[b["stat"]][2],
            "line": b["line"], "book": b["book"], "deep_link": b.get("deep_link"),
            "team": pr["team"] if pr else None, "opp": pr["opp"] if pr else None,
            "position": pr["position"] if pr else None,
            "game_id": pr["game_id"] if pr else None,
            "kickoff": pr["kickoff"] if pr else b.get("start"),
            "headshot": pr["headshot"] if pr else None,
            "books": prop_books.get((b["pkey"], b["stat"], b["line"]), []),
        }
        if not info["game_id"] or not _after(info["kickoff"], now):
            continue                       # only players in upcoming games we model
        if pr:
            info.update(matchups.evaluate_line(pr, b["line"]))
            info.update({k: pr[k] for k in ("projection", "baseline", "matchup_pct",
                                             "matchup_factor", "def_rank", "last10", "log", "def_log",
                                             "vs_opp", "home_away", "player_id",
                                             "def_allowed", "league_avg", "games")})
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
            if _price_side(p, dec, (fair["over"], fair["under"]) if fair else None,
                           side == "over"):
                ev_picks.append(dict(p))
            if pr and p.get("cheat_side") == side:
                cheat_picks.append(dict(p))

    # ---- moneyline / spread / total -----------------------------------
    for g in b_games:
        m = gm_by_teams.get((g["home"], g["away"]))
        if not m or not _after(m["kickoff"], now):
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
                title = f"{team} {_fmt_line(g['line'] if side == 'home' else -g['line'])}"
            else:
                title = f"{'Over' if side == 'over' else 'Under'} {g['line']}"
            p = {
                "kind": g["kind"], "week": week, "player": f"{m['away']} @ {m['home']}",
                "pkey": m["game_id"], "stat": g["kind"], "stat_label": KIND_LABEL[g["kind"]],
                "title": title, "line": g["line"], "side": side, "team": team, "opp": opp,
                "book": g["book"], "deep_link": g.get("deep_link"),
                "game_id": m["game_id"], "kickoff": m["kickoff"], "home": m["home"], "away": m["away"],
                "dec": round(dec, 3), "american": ev.decimal_to_american(dec),
                "game_model": m,
                "books": game_books.get((g["kind"], g["home"], g["away"], g["line"]), []),
            }
            implied = (own[0] if i == 0 else own[1]) if own else 1 / dec
            p.update(gamelines.evaluate(m, g["kind"], side, g["line"], implied))
            if fair:
                fa_, fb_ = ev.decimal_to_american(fair["a"]), ev.decimal_to_american(fair["b"])
                if g["kind"] == "total":
                    p["pinnacle_line"] = f"{fair['line']} o{fa_}/u{fb_}"
                elif g["kind"] == "spread":
                    p["pinnacle_line"] = (f"{m['home']} {_fmt_line(fair['line'])} {fa_} / "
                                          f"{m['away']} {_fmt_line(-fair['line'])} {fb_}")
                else:
                    p["pinnacle_line"] = f"{m['home']} {fa_:+d} / {m['away']} {fb_:+d}"
            if _price_side(p, dec, (fair["a"], fair["b"]) if fair else None, i == 0):
                ev_picks.append(dict(p))
            if p.get("cheat_side") == side:
                cheat_picks.append(dict(p))

    # one row per bet: the better of DK / FD
    ev_picks = best_by(ev_picks, lambda p: (p["pkey"], p["stat"], p["side"], p["line"]),
                       lambda p: p["ev_pct"])
    cheat_picks = best_by(cheat_picks, lambda p: (p["pkey"], p["stat"], p["side"]),
                          lambda p: (abs(p.get("proj_edge_pct") or p.get("model_edge") or 0), p["dec"]))
    combined = [dict(p) for p in ev_picks if p.get("cheat_side") == p["side"]]

    ev.cap_per_game(ev_picks)
    ev.cap_per_game(combined)
    ev_picks.sort(key=lambda p: -p["ev_pct"])
    combined.sort(key=lambda p: -p["ev_pct"])
    cheat_picks.sort(key=lambda p: (p["kind"] != "prop",
                                    -abs(p.get("proj_edge_pct") or p.get("model_edge") or 0)))

    # top matchups even when no lines are posted yet
    top = sorted(proj, key=lambda r: -abs(r["matchup_pct"]))
    top = [r for r in top if r["games"] >= config.MIN_GAMES][:80]

    games_list = [{"game_id": g["game_id"], "label": f"{g['away_team']} @ {g['home_team']}",
                   "kickoff": g["kickoff"].isoformat()}
                  for _, g in week_games.sort_values("kickoff").iterrows()]

    nxt = sched[sched["kickoff"] > now].sort_values("kickoff").head(1)
    out = {
        "generated_at": _iso(now), "season": season, "week": week, "stats_mode": mode,
        "next_kickoff": _iso(nxt.iloc[0]["kickoff"]) if len(nxt) else None,
        "status": status, "settings": {
            "unit_pct": config.UNIT_PCT_OF_BANKROLL, "kelly": config.KELLY_FRACTION,
            "max_units": config.MAX_UNITS_PER_BET, "max_game_units": config.MAX_UNITS_PER_GAME,
            "max_units_prop": config.MAX_UNITS["prop"], "max_units_line": config.MAX_UNITS["line"],
            "suspect_ev": config.SUSPECT_EV_PCT, "suspect_units": config.SUSPECT_MAX_UNITS,
            "min_ev": config.MIN_EV_PCT, "matchup_weight": config.MATCHUP_WEIGHT,
            "game_weight": config.GAME_MODEL_WEIGHT, "baseline_games": config.BASELINE_GAMES,
            "home_field": config.HOME_FIELD_PTS},
        "logos": _safe(stats.team_logos, {}),
        "games": games_list, "game_model": sorted(gm.values(), key=lambda m: m["kickoff"]),
        "combined": combined, "ev": ev_picks, "cheat": cheat_picks, "matchups": top,
    }
    # Keep the last odds board if this run didn't pull odds
    if not want_odds:
        prev = _load("picks.json", {})
        if prev.get("week") == week:
            for k in ("combined", "ev", "cheat"):
                out[k] = [p for p in prev.get(k, []) if _after(p.get("kickoff"), now)]
            out["odds_as_of"] = prev.get("odds_as_of")
    else:
        out["odds_as_of"] = _iso(now)

    _save("picks.json", out)
    # every active player, for the My Players search page
    players = {}
    for r in baselines.itertuples():
        k = odds.pkey(r.player)
        if k not in players:
            players[k] = {"pkey": k, "name": r.player, "team": r.team, "pos": r.position,
                          "headshot": r.headshot}
    _save("players.json", sorted(players.values(), key=lambda x: x["name"]))
    if want_odds:
        update_log(out, pinn_prop, pinn_game, now)
    _save("state.json", state)
    return out


# ------------------------------------------------------------------ paper log
def update_log(out, pinn_prop, pinn_game, now):
    log = _load("log.json", {"picks": {}})
    picks = log["picks"]
    for mode in ("combined", "ev", "cheat"):
        for p in out[mode]:
            k = pick_key(mode, p)
            units = p.get("units") if mode != "cheat" else 1.0
            if not units:
                continue                    # trimmed to 0 by the game cap
            if k not in picks:
                picks[k] = {
                    "mode": mode, "kind": p["kind"], "first_seen": _iso(now), "week": p["week"],
                    "player": p["player"], "title": p["title"], "pkey": p["pkey"],
                    "stat": p["stat"], "stat_label": p["stat_label"], "side": p["side"],
                    "line": p["line"], "book": p["book"], "dec": p["dec"],
                    "american": p["american"], "units": units,
                    "ev_pct": p.get("ev_pct"), "fair_prob": p.get("fair_prob"),
                    "kickoff": p["kickoff"], "team": p.get("team"), "opp": p.get("opp"),
                    "game_id": p.get("game_id"), "home": p.get("home"), "away": p.get("away"),
                    "result": None,
                }
    # closing line: latest Pinnacle fair price before kickoff
    for rec in picks.values():
        if rec["result"] is not None or not _after(rec["kickoff"], now):
            continue
        if rec.get("kind", "prop") == "prop":
            f = pinn_prop.get((rec["pkey"], rec["stat"], rec["line"]))
            pair = (f["over"], f["under"]) if f else None
            is_a = rec["side"] == "over"
        else:
            f = pinn_game.get((rec["kind"], rec["home"], rec["away"], rec["line"]))
            pair = (f["a"], f["b"]) if f else None
            is_a = rec["side"] in ("home", "over")
        if pair:
            fa, fb = ev.devig_two_way(*pair)
            rec["close_fair_prob"] = round(fa if is_a else fb, 4)
            rec["clv_pct"] = round(ev.ev_pct(rec["close_fair_prob"], rec["dec"]), 2)
    _save("log.json", log)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    o = build(force=a.force)
    print(f"Week {o['week']}: combined={len(o['combined'])} ev={len(o['ev'])} "
          f"cheat={len(o['cheat'])} status={o['status']}")
