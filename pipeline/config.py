"""
All the knobs for the app live here. Change these, push to GitHub, done.
"""

# ---------------------------------------------------------------- books
# Books you bet on (must be on your SharpAPI plan; free plan = these two).
BET_BOOKS = ["draftkings", "fanduel"]

# ---------------------------------------------------------------- sizing
UNIT_PCT_OF_BANKROLL = 1.0   # 1 unit = 1% of bankroll
KELLY_FRACTION = 0.25        # quarter-Kelly
MAX_UNITS_PER_BET = 3.0
MAX_UNITS_PER_GAME = 5.0     # props in one game are correlated
MIN_UNITS = 0.1              # anything smaller is dropped

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

# ---------------------------------------------------------------- timing
# Odds checks only run when a game kicks off within this many hours.
PREGAME_WINDOW_HOURS = 4

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
