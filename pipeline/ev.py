"""
EV math + unit sizing.

  fair prob   : Pinnacle over/under with the vig removed
  EV %        : fair_prob * decimal_odds - 1
  full Kelly  : (b*p - q) / b   where b = decimal - 1, q = 1 - p
  units       : KELLY_FRACTION * full Kelly, as % of bankroll / UNIT_PCT
"""
from . import config


def american_to_decimal(a):
    a = float(a)
    return 1 + (a / 100 if a > 0 else 100 / -a)


def decimal_to_american(d):
    d = float(d)
    if d >= 2:
        return round((d - 1) * 100)
    return round(-100 / (d - 1))


def devig_two_way(dec_a, dec_b):
    """Multiplicative devig. Returns fair probs (p_a, p_b)."""
    ia, ib = 1 / dec_a, 1 / dec_b
    s = ia + ib
    return ia / s, ib / s


def ev_pct(fair_p, dec):
    return (fair_p * dec - 1) * 100


def kelly_units(fair_p, dec):
    b = dec - 1
    full = (b * fair_p - (1 - fair_p)) / b
    if full <= 0:
        return 0.0, 0.0
    stake_pct = full * config.KELLY_FRACTION * 100      # % of bankroll
    units = stake_pct / config.UNIT_PCT_OF_BANKROLL
    units = min(units, config.MAX_UNITS_PER_BET)
    units = round(units, 1)
    return (units if units >= config.MIN_UNITS else 0.0), round(full * 100, 2)


def cap_per_game(picks, key="units"):
    """
    Keep each game's total at or under MAX_UNITS_PER_GAME. Best EV bets get
    their full size first; once a game's budget is used, later bets in that
    game are trimmed, then marked 'game limit reached' (0 units).
    """
    spent = {}
    for p in sorted(picks, key=lambda x: -x.get("ev_pct", 0)):
        left = config.MAX_UNITS_PER_GAME - spent.get(p["game_id"], 0)
        want = p[key]
        if want > left:
            p["full_units"] = want
            p[key] = round(left, 1) if left >= config.MIN_UNITS else 0.0
            p["capped"] = True
        spent[p["game_id"]] = spent.get(p["game_id"], 0) + p[key]
    return picks
