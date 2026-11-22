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
         "rec_yds": "receiving_yards", "receptions": "receptions"}
pinn_label = {"pass_yds": "Passing Yards", "pass_comp": "Completions", "pass_att": "Pass Attempts",
              "rush_yds": "Rushing Yards", "rush_att": "Rushing Attempts",
              "rec_yds": "Receiving Yards", "receptions": "Receptions"}
def am(p):  # fair prob + vig -> decimal
    return round(1 / p, 3)
sharp, specials = [], []
for r in pr:
    if random.random() < 0.35: continue
    line = round(r["baseline"] * random.uniform(0.92, 1.05) * 2) / 2 + 0.0
    if line == int(line): line += 0.5
    true_over = min(max(0.5 + random.gauss(0, 0.05), 0.3), 0.7)
    # pinnacle: ~2.5% vig
    po, pu = am(true_over * 1.025), am((1 - true_over) * 1.025)
    specials.append({"event_id": abs(hash(r["player"] + r["stat"])) % 10**9, "parent_id": 1,
        "league_name": "NFL", "starts": r["kickoff"], "special": f"{r['player']} ({pinn_label[r['stat']]})",
        "special_category": "Player Props",
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
json.dump({"_note": "FAKE TEST DATA", "data": sharp, "pagination": {"has_more": False}},
          open("tests/fixtures/sharpapi_odds.json", "w"))
json.dump({"_note": "FAKE TEST DATA", "sport_id": 5, "events": specials},
          open("tests/fixtures/pinnwire_markets.json", "w"))
print(len(sharp), "book rows,", len(specials), "pinnacle props")
