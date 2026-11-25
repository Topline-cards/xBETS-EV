"""
Builds FAKE odds files for offline testing (tests/fixtures). Not real prices.
Uses real players from this week's projections so the matching code is exercised.
"""
import json, random, sys
from datetime import datetime
from pipeline import stats, matchups, ev
from pipeline.stats import ET

random.seed(7)
now = datetime.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else datetime.now(ET)
season = stats.season_for(now); s = stats.load_schedule(season)
w = stats.current_week(s, now); g = s[(s.week == w) & (s.kickoff > now)]
d, b, _ = stats.stats_frames(season, w)
al, lg = stats.defense_vs_position(d)
pr = matchups.build_projections(stats.player_baselines(b), al, lg, g)
label = {"pass_yds": "passing_yards", "pass_comp": "pass_completions", "pass_att": "pass_attempts",
         "rush_yds": "rushing_yards", "rush_att": "rushing_attempts",
         "rec_yds": "receiving_yards", "receptions": "receptions", "pass_tds": "passing_touchdowns"}
# PinnWire's real wording (from the Debug feeds sample)
pinn_label = {"pass_yds": "Passing Yards", "pass_comp": "Pass Completions", "pass_att": "Pass Attempts",
              "rush_yds": "Rushing Yards", "rush_att": "Rush Attempts",
              "rec_yds": "Receiving Yards", "receptions": "Receptions", "pass_tds": "Touchdown Passes"}
def am(p):  # fair prob + vig -> decimal
    return round(1 / p, 3)
sharp, specials, mains = [], [], []
import math
for r in pr:
    if random.random() < 0.35: continue
    if r["stat"] == "anytime_td":          # DK/FD "Yes" only, no line, no Pinnacle market
        py = 1 - math.exp(-r["baseline"])
        for book in ("draftkings", "fanduel"):
            dec = round(1 / (min(max(py + random.gauss(0, 0.04), 0.05), 0.8) * 1.08), 3)
            sharp.append({"sportsbook": book, "league": "nfl", "market_type": "anytime_touchdown_scorer",
                "stat_category": "anytime_touchdown_scorer", "player_name": r["player"], "selection": r["player"],
                "selection_type": "other", "line": None, "odds_decimal": dec,
                "is_player_prop": True, "is_live": False, "is_main_line": True, "market_segment": "full_game",
                "event_start_time": r["kickoff"], "deep_link": f"https://example.com/{book}"})
        continue
    line = round(r["baseline"] * random.uniform(0.92, 1.05) * 2) / 2 + 0.0
    if line == int(line): line += 0.5
    true_over = min(max(0.5 + random.gauss(0, 0.05), 0.3), 0.7)
    # pinnacle: ~2.5% vig
    po, pu = am(true_over * 1.025), am((1 - true_over) * 1.025)
    specials.append({"event_id": abs(hash(r["player"] + r["stat"])) % 10**9, "parent_id": 1,
        "league_name": "NFL", "starts": r["kickoff"], "special": f"{r['player']} Total {pinn_label[r['stat']]}",
        "special_category": "Player Props", "special_units": pinn_label[r["stat"]],
        "special_markets": {"num_0": [{"type": "total", "prices": [
            {"name": "Over", "points": line, "price": po}, {"name": "Under", "points": line, "price": pu}]}]}})
    for book in ("draftkings", "fanduel"):
        shade = random.gauss(0, 0.035)       # soft books lag the market
        bo = max(true_over + shade, 0.2); bu = max(1 - true_over - shade, 0.2)
        for side, p in (("over", bo), ("under", bu)):
            dec = round(1 / (p * 1.045), 3)
            sharp.append({"id": f"{book}_{r['player']}_{r['stat']}_{side}", "sportsbook": book,
                "league": "nfl", "market_type": f"player_{label[r['stat']]}", "stat_category": label[r["stat"]],
                "player_name": r["player"], "selection": side.title(), "selection_type": side,
                "line": line, "odds_decimal": dec, "odds_american": ev.decimal_to_american(dec),
                "is_player_prop": True, "is_live": False, "is_main_line": True, "market_segment": "full_game",
                "event_start_time": r["kickoff"], "deep_link": f"https://example.com/{book}"})
# ---- game lines (fake prices around nflverse's consensus lines)
NAMES = {v: k for k, v in stats.TEAM_NICK.items()}
from pipeline.matchups import norm_cdf
for _, gm in g.iterrows():
    h, a = gm["home_team"], gm["away_team"]
    hs = -float(gm["spread_line"] or 0); tot = float(gm["total_line"] or 44.5)
    p_home = norm_cdf(-hs / 13.5)
    ev_id = abs(hash(gm["game_id"])) % 10**9
    mains.append({"event_id": ev_id, "league_name": "NFL", "home": f"X {NAMES[h]}", "away": f"Y {NAMES[a]}",
        "starts": gm["kickoff"].isoformat(), "parent_id": None, "periods": {"num_0": {"number": 0,
        "money_line": {"home": am(p_home * 1.02), "away": am((1 - p_home) * 1.02)},
        "spreads": {str(hs): {"hdp": hs, "home": 1.95, "away": 1.95, "max": 5000}},
        "totals": {str(tot): {"points": tot, "over": 1.95, "under": 1.95, "max": 5000}}}}})
    for book in ("draftkings", "fanduel"):
        sh = random.gauss(0, 0.03)
        rows = [("moneyline", "home", None, am((p_home + sh) * 1.045)),
                ("moneyline", "away", None, am((1 - p_home - sh) * 1.045)),
                ("point_spread", "home", hs, am((0.5 + sh) * 1.045)),
                ("point_spread", "away", -hs, am((0.5 - sh) * 1.045)),
                ("total_points", "over", tot, am((0.5 + sh) * 1.045)),
                ("total_points", "under", tot, am((0.5 - sh) * 1.045))]
        for mt, st, line, dec in rows:
            sharp.append({"sportsbook": book, "league": "nfl", "market_type": mt, "selection_type": st,
                "home_team": f"{h} {NAMES[h]}", "away_team": f"{a} {NAMES[a]}", "line": line,
                "odds_decimal": dec, "odds_american": ev.decimal_to_american(dec), "is_player_prop": False,
                "is_live": False, "is_main_line": True, "market_segment": "full_game",
                "event_start_time": gm["kickoff"].isoformat(), "deep_link": f"https://example.com/{book}"})
json.dump({"_note": "FAKE TEST DATA", "data": sharp, "pagination": {"has_more": False}},
          open("tests/fixtures/sharpapi_odds.json", "w"))
json.dump({"_note": "FAKE TEST DATA", "sport_id": 5, "events": specials},
          open("tests/fixtures/pinnwire_markets.json", "w"))
json.dump({"_note": "FAKE TEST DATA", "sport_id": 5, "events": mains},
          open("tests/fixtures/pinnwire_fixtures.json", "w"))
print(len(sharp), "book rows,", len(specials), "pinnacle props")
