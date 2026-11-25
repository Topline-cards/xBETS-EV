"""
College basketball (men's): moneyline, spread, total only. Sizing/EV rules shared with NFL/NBA.
"""
# Mon=0 ... Sun=6. Tuesday, Wednesday, Friday, Saturday.
GAME_WEEKDAYS = {1, 2, 4, 5}
GAME_HOLIDAYS = set()

# ---- team rating model (cheat sheet for game lines)
# points = league avg + offense(team) - defense(opponent) + home court, fit by ridge regression
RIDGE = 6.0                 # shrink ratings toward average (few games early in the season)
PRIOR_WEIGHT = 0.3          # last season's games, weighted down, for the first PRIOR_DAYS
PRIOR_DAYS = 45
GAME_MODEL_WEIGHT = 0.8     # trust this share of the rating gap
HOME_FIELD_PTS = 3.0        # fallback; the fit estimates its own
MARGIN_SD = 11.0
TOTAL_SD = 15.0
SPREAD_MIN_EDGE = 3.0
TOTAL_MIN_EDGE = 5.0
ML_MIN_EDGE_PCT = 5.0
MIN_TEAM_GAMES = 3          # skip games where a team has fewer completed games

# ---- feeds
SHARP_LEAGUE = "ncaab"      # set from the feed probe
SHARP_MARKETS = ["moneyline", "point_spread", "total_points"]
PINN_SPORT_ID = 3
PINN_LEAGUE = "NCAA"
PINN_REFRESH_MIN = 20

# ---- team-name matching between ESPN, DraftKings/FanDuel and Pinnacle
MATCH_MIN = 0.86            # both teams must match at least this well
MATCH_MARGIN = 0.04         # and clearly better than the next-best game
