"""
NBA knobs. Sizing, EV filters and books are shared with the NFL (pipeline/config.py).
"""

# ---------------------------------------------------------------- slate
# Mon=0 ... Sun=6. Monday through Friday, plus Christmas Day whatever the weekday.
GAME_WEEKDAYS = {0, 1, 2, 3, 4}
GAME_HOLIDAYS = {"christmas"}

# ---------------------------------------------------------------- props
# stat key -> (box score column, label). "pra" is points + rebounds + assists.
PROP_STATS = {
    "pts": ("points", "Points"),
    "reb": ("rebounds", "Rebounds"),
    "ast": ("assists", "Assists"),
    "fg3": ("three_point_field_goals_made", "Threes Made"),
    "pra": ("pra", "Pts + Reb + Ast"),
}
# Your NBA cheat sheet: 5 positions from Basketball-Reference (first listed position),
# ESPN's listing as a fallback.
POSITIONS = ("PG", "SG", "SF", "PF", "C")
ESPN_POS_FALLBACK = {"PG": "PG", "SG": "SG", "SF": "SF", "PF": "PF", "C": "C",
                     "G": "SG", "F": "SF", "G-F": "SG", "F-G": "SF", "F-C": "PF", "C-F": "C"}
BLEND_DAYS = 14              # first 14 days of the season: blend in last season
CURRENT_WEIGHT = 2.0         # ...with each current-season game counting double

# ---------------------------------------------------------------- cheat sheet
# Matchup = what the opponent allows per game to the player's position vs the league.
MATCHUP_GOOD = 1.08
MATCHUP_BAD = 0.92
CHEAT_MIN_EDGE_PCT = 8.0
MATCHUP_WEIGHT = 0.5
MATCHUP_FACTOR_RANGE = (0.70, 1.40)
BASELINE_GAMES = 10          # player baseline = last 10 games played
MIN_GAMES = 4
MIN_MINUTES = 14             # ignore deep-bench roles (avg minutes, last 5 games)
MIN_BASELINE = {"pts": 6, "reb": 2.5, "ast": 1.5, "fg3": 0.8, "pra": 10}
DEF_MIN_GAMES = 8            # early season: add last season's games until each team has this many

# ---------------------------------------------------------------- game lines
GAME_MODEL_WEIGHT = 0.6
HOME_FIELD_PTS = 2.5
MARGIN_SD = 12.5
TOTAL_SD = 18.0
SPREAD_MIN_EDGE = 3.0
TOTAL_MIN_EDGE = 5.0
ML_MIN_EDGE_PCT = 5.0

# ---------------------------------------------------------------- injuries
HIDE_INJURY_STATUS = {"Out", "Doubtful", "Suspension"}

# ---------------------------------------------------------------- feeds
SHARP_MARKETS = [
    "moneyline", "point_spread", "total_points",
    "player_points", "player_rebounds", "player_assists", "player_made_threes",
    "player_points_+_rebounds_+_assists",
]
PINN_SPORT_ID = 3            # PinnWire: basketball
PINN_REFRESH_MIN = 20
