"""
FAKE NBA odds for offline testing. Not real prices.
  python tests/make_nba_fixtures.py OUTDIR 2026-11-25T10:00-05:00
"""
import json, random, sys
from datetime import datetime
from pipeline import ev
from pipeline.matchups import norm_cdf
from pipeline.nba import build, stats

random.seed(11)
out = sys.argv[1]
now = datetime.fromisoformat(sys.argv[2])
season = stats.season_for(now)
s = stats.slate(stats.load_schedule(season)); day = stats.next_day(s, now)
g = s[s.kickoff.dt.date.map(lambda d: d.isoformat()) == day]
pm = stats.position_map(season)
box, prior = stats.add_positions(stats.load_box(season), pm), stats.add_positions(stats.load_box(season - 1), pm)
al, lg, _ = stats.defense_vs_position(box, prior, now.date())
pr = build.projections(stats.player_baselines(box), al, lg, g, {})
mt = {"pts": "player_points", "reb": "player_rebounds", "ast": "player_assists",
      "fg3": "player_made_threes", "pra": "player_points_+_rebounds_+_assists"}
pl = {"pts": "Points", "reb": "Rebounds", "ast": "Assists", "fg3": "Threes Made", "pra": "Pts & Rebs & Asts"}
am = lambda p: round(1 / p, 3)
sharp, specials, mains = [], [], []
for r in pr:
    if random.random() < 0.3:
        continue
    line = round(r["baseline"] * random.uniform(0.92, 1.05) * 2) / 2
    if line == int(line):
        line += 0.5
    t = min(max(0.5 + random.gauss(0, 0.05), 0.3), 0.7)
    specials.append({"event_id": random.randrange(10**9), "parent_id": 1, "league_name": "NBA",
        "starts": r["kickoff"], "special": f"{r['player']} Total {pl[r['stat']]}",
        "special_category": "Player Props", "special_units": pl[r["stat"]],
        "special_markets": {"num_0": [{"type": "total", "prices": [
            {"name": "Over", "points": line, "price": am(t * 1.025)},
            {"name": "Under", "points": line, "price": am((1 - t) * 1.025)}]}]}})
    for book in ("draftkings", "fanduel"):
        sh = random.gauss(0, 0.035)
        for side, p in (("over", max(t + sh, .2)), ("under", max(1 - t - sh, .2))):
            dec = round(1 / (p * 1.045), 3)
            sharp.append({"sportsbook": book, "league": "nba", "market_type": mt[r["stat"]],
                "player_name": r["player"], "selection_type": side, "line": line, "odds_decimal": dec,
                "is_player_prop": True, "is_live": False, "is_main_line": True, "market_segment": "full_game",
                "event_start_time": r["kickoff"], "deep_link": f"https://example.com/{book}"})
sched = stats.load_schedule(season)
full = dict(zip(sched.home_team, sched.home_display_name))
for _, gm in g.iterrows():
    h, a = gm.home_team, gm.away_team
    hs, tot = -4.5, 226.5
    ph = norm_cdf(-hs / 12.5)
    mains.append({"event_id": random.randrange(10**9), "league_name": "NBA", "home": full[h], "away": full[a],
        "starts": gm.kickoff.isoformat(), "parent_id": None, "periods": {"num_0": {
        "money_line": {"home": am(ph * 1.02), "away": am((1 - ph) * 1.02)},
        "spreads": {"x": {"hdp": hs, "home": 1.95, "away": 1.95}},
        "totals": {"y": {"points": tot, "over": 1.95, "under": 1.95}}}}})
    for book in ("draftkings", "fanduel"):
        sh = random.gauss(0, 0.03)
        for m_, st, line, dec in [("moneyline", "home", None, am((ph + sh) * 1.045)),
                                  ("moneyline", "away", None, am((1 - ph - sh) * 1.045)),
                                  ("point_spread", "home", hs, am((.5 + sh) * 1.045)),
                                  ("point_spread", "away", -hs, am((.5 - sh) * 1.045)),
                                  ("total_points", "over", tot, am((.5 + sh) * 1.045)),
                                  ("total_points", "under", tot, am((.5 - sh) * 1.045))]:
            sharp.append({"sportsbook": book, "league": "nba", "market_type": m_, "selection_type": st,
                "home_team": full[h], "away_team": full[a], "line": line, "odds_decimal": dec,
                "is_player_prop": False, "is_live": False, "is_main_line": True, "market_segment": "full_game",
                "event_start_time": gm.kickoff.isoformat(), "deep_link": f"https://example.com/{book}"})
json.dump({"data": sharp, "pagination": {"has_more": False}}, open(f"{out}/nba_sharpapi_odds.json", "w"))
json.dump({"events": specials}, open(f"{out}/nba_pinnwire_markets.json", "w"))
json.dump({"events": mains}, open(f"{out}/nba_pinnwire_fixtures.json", "w"))
print(len(sharp), "book rows,", len(specials), "pinnacle props,", len(mains), "games")
