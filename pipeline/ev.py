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
    Base stake for one bet, before confidence/correlation (see size_slate).
      1. Trust only part of the gap between Pinnacle and the book (EDGE_TRUST)
      2. Quarter-Kelly on that trusted chance
    Returns (units_float, full_kelly_pct_raw, trusted_p, note)
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
    note = None
    if ev_pct(fair_p, dec) >= config.SUSPECT_EV_PCT:
        note = "big edge: double-check the line before betting"
    return units, round(full_raw * 100, 2), round(p, 4), note


def _round_units(u):
    step = config.UNIT_STEP
    return max(round(u / step) * step, config.MIN_UNITS)


def size_slate(picks):
    """
    Final recommended units for every EV play. Nothing is dropped:
      base (trusted quarter-Kelly)
      x confidence (cheat sheet agrees / disagrees / questionable)
      x correlation (1/sqrt(n) per game, 1/n per player)
      capped per bet type and for suspicious EV, rounded to 0.25u (min 0.25u)
    Stores the steps on each pick so the app can show the math.
    """
    by_game, by_player = {}, {}
    for p in picks:
        by_game[p.get("game_id")] = by_game.get(p.get("game_id"), 0) + 1
        if p.get("kind", "prop") == "prop":
            by_player[p["pkey"]] = by_player.get(p["pkey"], 0) + 1
    for p in picks:
        base = p.get("base_units") or 0.0
        conf, why = 1.0, []
        if p.get("cheat_side") == p["side"]:
            conf *= config.CONF_AGREES
            why.append("cheat sheet agrees")
        elif _cheat_disagrees(p):
            conf *= config.CONF_DISAGREES
            why.append("matchup points the other way")
        if p.get("injury_status") in ("Questionable", "Day-To-Day"):
            conf *= config.CONF_QUESTIONABLE
            why.append("questionable")
        n_g = by_game.get(p.get("game_id"), 1)
        n_p = by_player.get(p["pkey"], 1) if p.get("kind", "prop") == "prop" else 1
        corr = (1 / n_g ** 0.5) * (1 / n_p)
        cap = config.MAX_UNITS["prop" if p.get("kind", "prop") == "prop" else "line"]
        if p.get("unit_note"):
            cap = min(cap, config.SUSPECT_MAX_UNITS)
        raw = min(base * conf * corr, cap)
        p.update(units=_round_units(raw) if base > 0 else 0.0,
                 sizing={"base": round(base, 2), "conf": round(conf, 2), "conf_why": why,
                         "game_bets": n_g, "player_bets": n_p, "corr": round(corr, 2),
                         "cap": cap, "raw": round(raw, 2)})
    return round(sum(p["units"] for p in picks), 2)


def _cheat_disagrees(p):
    """Projection and matchup both lean the opposite way of this bet."""
    if p.get("kind", "prop") == "prop":
        e, m = p.get("proj_edge_pct"), p.get("matchup_pct")
        if e is None or m is None:
            return False
        sign = 1 if p["side"] == "over" else -1
        return e * sign < 0 and m * sign < 0
    return (p.get("model_edge") or 0) < -1.0


