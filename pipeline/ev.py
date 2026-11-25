"""
EV math + unit sizing (see the +EV Best Practices Playbook).

  fair prob   : sharp two-way price devigged four ways (multiplicative, additive,
                power, Shin); we bet off the WORST of them for our side
  EV %        : fair_prob * decimal_odds - 1
  threshold   : sliding by odds (1% at -150 ... 8-10% at +400 and longer)
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


def _bisect(f, lo, hi, n=60):
    for _ in range(n):
        mid = (lo + hi) / 2
        if f(mid) > 0:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def devig_methods(dec_a, dec_b, shin=True):
    """
    {method: (p_a, p_b)} for every method whose result is a valid probability.
    Additive can push a longshot below 0: such results are dropped.
    Shin is skipped for efficient two-way main lines (shin=False).
    """
    ia, ib = 1 / dec_a, 1 / dec_b
    s = ia + ib
    out = {"multiplicative": (ia / s, ib / s)}
    over = s - 1
    pa, pb = ia - over / 2, ib - over / 2
    if 0 < pa < 1 and 0 < pb < 1:
        out["additive"] = (pa, pb)
    if s > 1:
        k = _bisect(lambda k: ia ** k + ib ** k - 1, 1.0, 10.0)   # sum falls as k rises
        out["power"] = (ia ** k, ib ** k)
        if shin:
            def shin_p(z, q):
                return ((z * z + 4 * (1 - z) * q * q / s) ** 0.5 - z) / (2 * (1 - z))
            z = _bisect(lambda z: shin_p(z, ia) + shin_p(z, ib) - 1, 0.0, 0.4)
            out["shin"] = (shin_p(z, ia), shin_p(z, ib))
    return out


def worst_case(dec_a, dec_b, side_a, shin=True):
    """Least favorable fair probability for our side, plus the range across methods."""
    m = devig_methods(dec_a, dec_b, shin)
    ps = {k: (v[0] if side_a else v[1]) for k, v in m.items()}
    worst = min(ps, key=ps.get)
    return ps[worst], worst, (min(ps.values()), max(ps.values())), {k: round(v, 4) for k, v in ps.items()}


def min_ev_for(dec):
    """Sliding EV threshold: longer odds need more EV (Betscope curve + the 8-10% rule)."""
    pts = config.EV_THRESHOLD_CURVE
    if dec <= pts[0][0]:
        return pts[0][1]
    for (d0, e0), (d1, e1) in zip(pts, pts[1:]):
        if dec <= d1:
            return e0 + (e1 - e0) * (dec - d0) / (d1 - d0)
    return pts[-1][1]


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
    k = config.KELLY_LONGSHOT if dec >= config.LONGSHOT_DEC else config.KELLY_FRACTION
    units = full * k * 100 / config.UNIT_PCT_OF_BANKROLL
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
        if p.get("tier") == "single":
            conf *= config.CONF_SINGLE_SOURCE
            why.append("one sharp source only")
        if p.get("lane_status") == "testing":
            conf *= config.LANE_TESTING_MULT
            why.append("lane still testing")
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


def lane_of(p, sport="nfl"):
    """A 'lane' = sport x market (e.g. nfl:rec_yds, nba:pts, nfl:spread)."""
    return f"{sport}:{p.get('stat') if p.get('kind', 'prop') == 'prop' else p.get('kind')}"


def lane_stats(log, sport="nfl"):
    """
    Promotion gate per lane, from logged EV plays (playbook: Measuring the edge).
    A lane is 'proven' once: mean EV vs sharp close >= +1%, ROI > 0,
    >= 30 graded bets over >= 15 distinct days. Otherwise 'testing'.
    """
    lanes = {}
    for r in log.get("picks", {}).values():
        if r.get("mode") != "ev":
            continue
        L = lanes.setdefault(lane_of(r, sport), {"bets": 0, "graded": 0, "days": set(), "clv": [],
                                                  "risked": 0.0, "profit": 0.0, "expected": 0.0})
        L["bets"] += 1
        if r.get("clv_pct") is not None:
            L["clv"].append(r["clv_pct"])
        if r.get("result") in ("win", "loss", "push"):
            L["graded"] += 1
            L["days"].add(str(r.get("kickoff", ""))[:10])
            L["profit"] += r.get("profit") or 0
            if r["result"] != "push":
                L["risked"] += r.get("units") or 0
            L["expected"] += (r.get("ev_pct") or 0) / 100 * (r.get("units") or 0)
    out = {}
    g = config.LANE_GATE
    for k, L in lanes.items():
        clv = sum(L["clv"]) / len(L["clv"]) if L["clv"] else None
        roi = L["profit"] / L["risked"] * 100 if L["risked"] else None
        n = len(L["clv"])
        sd = (sum((c - clv) ** 2 for c in L["clv"]) / (n - 1)) ** 0.5 if n > 1 else None
        roi_only = k.endswith(":anytime_td")   # no sharp close for scorer props: judge on ROI (playbook)
        checks = {"ev_vs_close": roi_only or (clv is not None and clv >= g["min_clv"]),
                  "roi": roi is not None and roi > 0,
                  "bets": L["graded"] >= g["min_bets"], "days": len(L["days"]) >= g["min_days"]}
        out[k] = {"bets": L["bets"], "graded": L["graded"], "days": len(L["days"]),
                  "ev_vs_close": round(clv, 2) if clv is not None else None,
                  "t_stat": round(clv / (sd / n ** 0.5), 2) if sd else None,
                  "roi_pct": round(roi, 1) if roi is not None else None,
                  "profit_units": round(L["profit"], 2), "expected_units": round(L["expected"], 2),
                  "checks": checks, "status": "proven" if all(checks.values()) else "testing"}
    return out


def _cheat_disagrees(p):
    """Projection and matchup both lean the opposite way of this bet."""
    if p.get("kind", "prop") == "prop":
        e, m = p.get("proj_edge_pct"), p.get("matchup_pct")
        if e is None or m is None:
            return False
        sign = 1 if p["side"] == "over" else -1
        return e * sign < 0 and m * sign < 0
    return (p.get("model_edge") or 0) < -1.0


