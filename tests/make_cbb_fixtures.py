"""FAKE college basketball odds for offline tests. python tests/make_cbb_fixtures.py OUTDIR 2026-11-25T10:00-05:00"""
import json, random, sys
from datetime import datetime
from pipeline.cbb import stats
from pipeline.matchups import norm_cdf

random.seed(5)
out, now = sys.argv[1], datetime.fromisoformat(sys.argv[2])
s = stats.load_schedule(stats.season_for(now)); sl = stats.slate(s); d = stats.next_day(sl, now)
g = sl[sl.kickoff.dt.date.map(lambda x: x.isoformat()) == d]
am = lambda p: round(1 / p, 3)
sharp, pinn = [], []
for k, r in enumerate(g.itertuples()):
    hs, tot = round(random.uniform(-15, 15) * 2) / 2, round(random.uniform(135, 160) * 2) / 2
    ph = norm_cdf(-hs / 11)
    swap = k % 7 == 3                                   # some feeds list neutral games the other way
    H, A = (r.away_location, r.home_location) if swap else (r.home_location, r.away_location)
    lh = -hs if swap else hs
    pinn.append({"league_name": "NCAA", "home": r.home_display_name if k % 2 else r.home_location,
                 "away": r.away_location, "starts": r.kickoff.isoformat(), "parent_id": None,
                 "periods": {"num_0": {"money_line": {"home": am(ph * 1.02), "away": am((1 - ph) * 1.02)},
                                       "spreads": {"x": {"hdp": hs, "home": 1.95, "away": 1.95}},
                                       "totals": {"y": {"points": tot, "over": 1.95, "under": 1.95}}}}})
    for book in ("draftkings", "fanduel"):
        sh = random.gauss(0, 0.03)
        p_h = (1 - ph) if swap else ph
        for m, st, line, dec in [("moneyline", "home", None, am((p_h + sh) * 1.045)),
                                 ("moneyline", "away", None, am((1 - p_h - sh) * 1.045)),
                                 ("point_spread", "home", lh, am((.5 + sh) * 1.045)),
                                 ("point_spread", "away", -lh, am((.5 - sh) * 1.045)),
                                 ("total_points", "over", tot, am((.5 + sh) * 1.045)),
                                 ("total_points", "under", tot, am((.5 - sh) * 1.045))]:
            sharp.append({"sportsbook": book, "league": "ncaab", "market_type": m, "selection_type": st,
                          "home_team": H, "away_team": A, "line": line, "odds_decimal": dec,
                          "is_player_prop": False, "is_live": False, "is_main_line": True,
                          "market_segment": "full_game", "event_start_time": r.kickoff.isoformat(),
                          "deep_link": f"https://example.com/{book}"})
json.dump({"data": sharp, "pagination": {"has_more": False}}, open(f"{out}/cbb_sharpapi_odds.json", "w"))
json.dump({"events": pinn}, open(f"{out}/cbb_pinnwire_fixtures.json", "w"))
print(len(g), "games", len(sharp), "book rows")
