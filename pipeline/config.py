"""
All the knobs for the app live here. Change these, push to GitHub, done.
"""

# ---------------------------------------------------------------- books
# Books you bet on (must be on your SharpAPI plan; free plan = these two).
BET_BOOKS = ["draftkings", "fanduel"]

# ---------------------------------------------------------------- sizing
UNIT_PCT_OF_BANKROLL = 1.0   # 1 unit = 1% of bankroll
KELLY_FRACTION = 0.25        # quarter-Kelly, applied to the *trusted* edge below
# How much of Pinnacle's edge we trust. Pinnacle's main lines are very sharp;
# its props are softer (lower limits), so only half the gap counts there.
EDGE_TRUST = {"prop": 0.5, "ml": 0.8, "spread": 0.8, "total": 0.8}
MAX_UNITS = {"prop": 1.5, "line": 2.0}   # per bet
MAX_UNITS_PER_BET = 2.0      # absolute ceiling (shown in the app)
MAX_UNITS_PER_GAME = 3.0     # bets in one game are correlated
UNIT_STEP = 0.25             # recommend 0.25u, 0.5u, 0.75u, ...
MIN_UNITS = 0.25             # anything smaller is dropped
# EV this big usually means a stale or mismatched line, so size it small.
SUSPECT_EV_PCT = 12.0
SUSPECT_MAX_UNITS = 0.5

# ---------------------------------------------------------------- EV filter
MIN_EV_PCT = 2.0             # only show bets at least 2% +EV
MAX_EV_PCT = 25.0            # bigger than this is almost always a bad match / stale line
MAX_ODDS_AMERICAN = 300      # ignore long shots; fair-line estimates get shaky

# ---------------------------------------------------------------- cheat sheet
# A matchup counts as "good" when the defense allows this much more than
# league average to the position (1.10 = 10% more). Mirror for unders.
MATCHUP_GOOD = 1.10
MATCHUP_BAD = 0.90
# Projection must clear the prop line by this % to be a cheat-sheet play.
CHEAT_MIN_EDGE_PCT = 8.0
# How much of the defense effect to trust (0 = ignore defense, 1 = full).
# Defense-vs-position numbers are noisy, so we only trust half.
MATCHUP_WEIGHT = 0.5
# Player baseline = average of their last N games (min games required).
BASELINE_GAMES = 6
MIN_GAMES = 3
# Ignore tiny roles (a WR averaging 0.5 rush yds isn't a real matchup).
MIN_BASELINE = {"pass_yds": 120, "pass_comp": 10, "pass_att": 15, "rush_yds": 12,
                "rush_att": 4, "rec_yds": 15, "receptions": 1.5}
# Defense effect is capped so one weird game can't dominate.
MATCHUP_FACTOR_RANGE = (0.6, 1.6)

# ---------------------------------------------------------------- game lines
GAME_MODEL_WEIGHT = 0.6      # how much of team scoring form to trust
HOME_FIELD_PTS = 1.5
MARGIN_SD = 13.5             # NFL final margins vary ~13-14 pts around expectation
TOTAL_SD = 13.0
SPREAD_MIN_EDGE = 2.5        # model must beat the spread by 2.5+ pts
TOTAL_MIN_EDGE = 3.0         # ...or the total by 3+ pts
ML_MIN_EDGE_PCT = 5.0        # ...or the book's win % by 5+ points

# ---------------------------------------------------------------- injuries
# Hide players with these injury-report statuses for the game.
HIDE_INJURY_STATUS = {"Out"}
# Doubtful players almost never play; set True to hide them as well.
HIDE_DOUBTFUL = True
# Roster statuses that mean "not playing": reserve/IR, cut, retired, traded away
# (re-checked against their new team), practice squad, inactive.
INACTIVE_ROSTER_STATUS = {"RES", "CUT", "RET", "DEV", "INA", "EXE", "SUS", "PUP", "NON"}

# ---------------------------------------------------------------- timing
# Odds checks only run when a game kicks off within this many hours.
PREGAME_WINDOW_HOURS = 4
# Pinnacle refresh costs 2 PinnWire calls; refresh at most this often
# (DK/FD still refresh every run). 20 min keeps a full Sunday under 100 calls.
PINN_REFRESH_MIN = 20

# ---------------------------------------------------------------- stats
# Prop stat -> (nflverse column, positions it applies to, label)
PROP_STATS = {
    "pass_yds":    ("passing_yards",   ["QB"],             "Passing Yards"),
    "pass_comp":   ("completions",     ["QB"],             "Completions"),
    "pass_att":    ("attempts",        ["QB"],             "Pass Attempts"),
    "rush_yds":    ("rushing_yards",   ["RB", "QB"],       "Rushing Yards"),
    "rush_att":    ("carries",         ["RB", "QB"],       "Rush Attempts"),
    "rec_yds":     ("receiving_yards", ["WR", "TE", "RB"], "Receiving Yards"),
    "receptions":  ("receptions",      ["WR", "TE", "RB"], "Receptions"),
}
