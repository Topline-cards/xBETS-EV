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


def kelly_units(fair_p, dec, kind="prop"):
    """
    Recommended units. Steps:
      1. Trust only part of the gap between Pinnacle's fair price and the
         book's own price (EDGE_TRUST): p = book_p + trust * (fair_p - book_p)
      2. Full Kelly on that trusted p, then KELLY_FRACTION of it
      3. Cap per bet type, cap suspiciously big EV, round to 0.25u steps
    Returns (units, full_kelly_pct_raw, trusted_p, note)
    """
    b = dec - 1
    full_raw = (b * fair_p - (1 - fair_p)) / b
    book_p = 1 / dec
    trust = config.EDGE_TRUST.get(kind, 0.5)
    p = book_p + trust * (fair_p - book_p)
    full = (b * p - (1 - p)) / b
    if full <= 0:
        return 0.0, round(full_raw * 100, 2), round(p, 4), None
    units = full * config.KELLY_FRACTION * 100 / config.UNIT_PCT_OF_BANKROLL
    cap = config.MAX_UNITS["prop" if kind == "prop" else "line"]
    note = None
    if ev_pct(fair_p, dec) >= config.SUSPECT_EV_PCT:
        cap = min(cap, config.SUSPECT_MAX_UNITS)
        note = "big edge: double-check the line before betting"
    units = min(units, cap)
    step = config.UNIT_STEP
    units = round(units / step) * step
    if units < config.MIN_UNITS:
        units = config.MIN_UNITS if full > 0.002 else 0.0
    return round(units, 2), round(full_raw * 100, 2), round(p, 4), note


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
            step = config.UNIT_STEP
            p[key] = (int(left / step) * step) if left >= config.MIN_UNITS else 0.0
            p["capped"] = True
        spent[p["game_id"]] = spent.get(p["game_id"], 0) + p[key]
    return picks
