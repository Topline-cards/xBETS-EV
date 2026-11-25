"""
Odds fetchers. Everything is normalized to one shape:

  {book, player, pkey, stat, line, over, under, start, deep_link}
  (over/under are decimal odds; either can be None)

SharpAPI -> DraftKings / FanDuel props   (secret: SHARPAPI_KEY)
PinnWire -> Pinnacle props, the sharp line (secret: PINNWIRE_KEY)

Set ODDS_FIXTURES=tests/fixtures to run offline against saved responses.
"""
import json
import os
import re
import time
import unicodedata

import requests

from . import config
from .stats import team_abbr

SHARP_BASE = "https://api.sharpapi.io/api/v1"
PINN_BASE = "https://pinnwire.com"
FIXTURES = os.environ.get("ODDS_FIXTURES")


# ------------------------------------------------------------------ helpers
_SUFFIX = re.compile(r"\b(jr|sr|ii|iii|iv|v)\b")


def pkey(name):
    """Normalize a player name so 'Amon-Ra St. Brown Jr.' == 'amon ra st brown'."""
    if not isinstance(name, str):
        return ""
    n = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    n = re.sub(r"[^a-z ]", " ", n.lower().replace("-", " "))
    n = _SUFFIX.sub(" ", n)
    return " ".join(n.split())


def stat_from_text(text):
    """Map any book's market wording to our stat keys. None = not supported."""
    t = (text or "").lower().replace("_", " ")
    if ("touchdown" in t or " td" in f" {t}" or "scorer" in t) and "anytime" in t \
            and not any(b in t for b in ("first", "last", "2+", "two", "3+", "pass", "1st", "half", "quarter")):
        return "anytime_td"
    if t.strip() in ("player touchdowns", "touchdowns", "anytime touchdown scorer"):
        return "anytime_td"
    if "touchdown" in t and "pass" in t and not any(b in t for b in ("1st", "first", "half", "quarter", "longest")):
        return "pass_tds"
    bad = ("longest", "+", "and", "combined", "1st", "first", "half", "quarter",
           "touchdown", " td", "interception", "sack", "kick", "punt", "fantasy",
           "alt ", "milestone")
    if any(b in f" {t} " for b in bad):
        return None
    if "pass" in t and "yard" in t:
        return "pass_yds"
    if "complet" in t:
        return "pass_comp"
    if "pass" in t and "attempt" in t:
        return "pass_att"
    if "rush" in t and "yard" in t:
        return "rush_yds"
    if "rush" in t and ("attempt" in t or "carr" in t):
        return "rush_att"
    if ("receiving" in t or "rec " in f"{t} ") and "yard" in t:
        return "rec_yds"
    if "reception" in t:
        return "receptions"
    return None


def _get(url, params=None, headers=None, tries=3):
    for i in range(tries):
        r = requests.get(url, params=params, headers=headers, timeout=30)
        if r.status_code == 429:
            wait = int(r.headers.get("Retry-After", 10))
            time.sleep(min(wait, 60))
            continue
        if r.status_code >= 500:
            time.sleep(3 * (i + 1))
            continue
        r.raise_for_status()
        return r.json()
    r.raise_for_status()


def _fixture(name):
    with open(os.path.join(FIXTURES, name)) as f:
        return json.load(f)


# ------------------------------------------------------------------ SharpAPI (DK / FD)
# Only ask for markets the app uses (the full feed is mostly 1st-half junk).
SHARP_MARKETS = [
    "moneyline", "point_spread", "total_points",
    "player_passing_yards", "player_passing_completions", "player_passing_attempts",
    "player_rushing_yards", "player_rushing_attempts", "player_total_rush_attempts",
    "player_receiving_yards", "player_receptions", "player_total_receptions",
    "anytime_touchdown_scorer", "player_touchdowns", "player_passing_touchdowns",
]
def fetch_sharpapi(league="nfl", markets=None, stat_fn=None, abbr=None, fixture="sharpapi_odds.json"):
    """Returns (props, game_lines) for DK/FD. Defaults = NFL."""
    markets = markets or SHARP_MARKETS
    stat_fn = stat_fn or stat_from_text
    abbr = abbr or team_abbr
    if FIXTURES:
        pages = [_fixture(fixture)]
    else:
        key = os.environ["SHARPAPI_KEY"]
        params = {"league": league, "sportsbook": ",".join(config.BET_BOOKS),
                  "is_live": "false", "limit": 200, "market": ",".join(markets),
                  "is_main_line": "true"}       # drops alt ladders (~85% of rows)
        pages, cursor = [], None
        for _ in range(40):                      # hard stop (main lines ≈ 11 pages)
            if cursor:
                params["cursor"] = cursor
            page = _get(f"{SHARP_BASE}/odds", params, {"X-API-Key": key})
            pages.append(page)
            pg = page.get("pagination") or {}
            if not pg.get("has_more") or not pg.get("next_cursor"):
                break
            cursor = pg["next_cursor"]
            time.sleep(5.5)                      # free plan: 12 req/min

    grouped, games = {}, {}
    for page in pages:
        for o in page.get("data", []):
            if o.get("is_live"):
                continue
            if not o.get("is_player_prop"):
                _add_game_line(games, o, abbr)
                continue
            if o.get("is_active") is False or o.get("is_stale_pregame_price"):
                continue
            if (o.get("market_segment") or "full_game") != "full_game":
                continue
            if o.get("is_alternate_line"):
                continue                     # main line only (skip alt ladders)
            stat = stat_fn(o.get("market_type") or o.get("stat_category"))
            side = (o.get("selection_type") or o.get("selection") or "").lower()
            if stat == "anytime_td":             # scorer markets: "Yes" (often typed "other"), no line
                side = {"other": "over", "yes": "over", "no": "under"}.get(side, side)
                if o.get("line") is None:
                    o = dict(o, line=0.5)
            if not stat or o.get("line") is None or not o.get("player_name"):
                continue
            if side not in ("over", "under"):
                continue
            k = (o["sportsbook"], pkey(o["player_name"]), stat, float(o["line"]))
            g = grouped.setdefault(k, {
                "book": o["sportsbook"], "player": o["player_name"], "pkey": k[1],
                "stat": stat, "line": k[3], "over": None, "under": None,
                "start": o.get("event_start_time"), "deep_link": o.get("deep_link"),
                "main": bool(o.get("is_main_line", True)),
            })
            g[side] = float(o.get("odds_decimal") or 0) or None
    return list(grouped.values()), list(games.values())


GAME_MARKETS = {"moneyline": "ml", "point_spread": "spread", "spread": "spread",
                "total_points": "total", "total": "total"}


def _add_game_line(games, o, abbr=None):
    """
    Game lines, normalized to {book, kind, home, away, line, a, b}:
      ml:     a = home price, b = away price, line None
      spread: line = HOME spread (e.g. -3.5), a = home price, b = away price
      total:  line = total, a = over, b = under
    """
    kind = GAME_MARKETS.get(o.get("market_type"))
    if not kind or (o.get("market_segment") or "full_game") != "full_game":
        return
    if o.get("is_active") is False or o.get("is_stale_pregame_price"):
        return
    if o.get("is_alternate_line"):
        return
    abbr = abbr or team_abbr
    home = abbr(o.get("home_team"), (o.get("home") or {}).get("abbreviation"))
    away = abbr(o.get("away_team"), (o.get("away") or {}).get("abbreviation"))
    if not home or not away:
        return
    st = (o.get("selection_type") or "").lower()
    dec = float(o.get("odds_decimal") or 0) or None
    line = o.get("line")
    if kind == "ml":
        if st not in ("home", "away"):
            return
        key_line = None
    elif kind == "spread":
        if st not in ("home", "away") or line is None:
            return
        key_line = float(line) if st == "home" else -float(line)
    else:
        if st not in ("over", "under") or line is None:
            return
        key_line = float(line)
    k = (o["sportsbook"], kind, home, away, key_line)
    g = games.setdefault(k, {"book": o["sportsbook"], "kind": kind, "home": home, "away": away,
                             "line": key_line, "a": None, "b": None,
                             "start": o.get("event_start_time"), "deep_link": o.get("deep_link")})
    g["a" if st in ("home", "over") else "b"] = dec


# ------------------------------------------------------------------ PinnWire (Pinnacle)
_PAREN = re.compile(r"^(.*?)\s*\((.*?)\)\s*$")


def _split_pinn_name(sp):
    """
    'Tony Pollard Total Receiving Yards' (special_units 'Receiving Yards')
      -> ('Tony Pollard', 'Receiving Yards')
    Also handles the older 'Josh Allen (Passing Yards)' style.
    """
    name = (sp.get("special") or sp.get("name") or "").strip()
    units = (sp.get("special_units") or "").strip()
    m = _PAREN.match(name)
    if m:
        return m.group(1), m.group(2)
    if units:
        m = re.match(r"^(.*?)\s+(?:Total\s+)?" + re.escape(units) + r"$", name, re.I)
        if m:
            return m.group(1), units
    m = re.match(r"^(.*?)\s+Total\s+(.*)$", name)
    if m:
        return m.group(1), m.group(2)
    return None, name


def _parse_pinn_special(sp, stat_fn=None):
    """Pinnacle player props -> normalized rows (Over/Under with a line)."""
    if sp.get("special_category") and sp.get("special_category") != "Player Props":
        return []
    player, stat_txt = _split_pinn_name(sp)
    stat = (stat_fn or stat_from_text)(stat_txt)
    if not stat or not player:
        return []
    rows = {}
    for mk in (sp.get("special_markets") or {}).get("num_0") or []:
        for pr in mk.get("prices", []):
            side = (pr.get("name") or "").strip().lower()
            if side not in ("over", "under") or pr.get("points") is None or not pr.get("price"):
                continue
            line = float(pr["points"])
            r = rows.setdefault((pkey(player), line), {
                "book": "pinnacle", "player": player, "pkey": pkey(player), "stat": stat,
                "line": line, "over": None, "under": None,
                "start": sp.get("starts"), "deep_link": None, "main": True,
            })
            r[side] = float(pr["price"])
    return list(rows.values())


def _iter_specials(payload):
    for ev in payload.get("events", []):
        if ev.get("special") or ev.get("special_markets"):
            yield ev                                    # flat mode
        for sp in ev.get("specials", []) or []:          # nested mode
            sp.setdefault("starts", ev.get("starts"))
            yield sp


def _pinn_game_lines(payload, league="NFL", abbr=None):
    abbr = abbr or team_abbr
    out = []
    for ev in payload.get("events", []):
        if ev.get("special") or ev.get("parent_id"):
            continue
        if (ev.get("league_name") or "").strip().upper() != league.upper():
            continue
        home, away = abbr(ev.get("home")), abbr(ev.get("away"))
        p0 = (ev.get("periods") or {}).get("num_0") or {}
        if not home or not away or not p0:
            continue
        base = {"book": "pinnacle", "home": home, "away": away, "start": ev.get("starts")}
        ml = p0.get("money_line") or {}
        if ml.get("home") and ml.get("away"):
            out.append(dict(base, kind="ml", line=None, a=float(ml["home"]), b=float(ml["away"])))
        for sp in (p0.get("spreads") or {}).values():
            if sp.get("home") and sp.get("away") and sp.get("hdp") is not None:
                out.append(dict(base, kind="spread", line=float(sp["hdp"]),
                                a=float(sp["home"]), b=float(sp["away"])))
        for t in (p0.get("totals") or {}).values():
            if t.get("over") and t.get("under") and t.get("points") is not None:
                out.append(dict(base, kind="total", line=float(t["points"]),
                                a=float(t["over"]), b=float(t["under"])))
    return out


def fetch_pinnacle(sport_id=5, league="NFL", stat_fn=None, abbr=None, prefix=""):
    """
    Returns (props, game_lines). Two PinnWire calls:
      /kit/v1/markets?include_specials=1   -> player props
      /kit/v1/prematch/fixtures            -> moneyline / spreads / totals
    PinnWire sport ids: 5 = football, 3 = basketball.
    """
    if FIXTURES:
        payload = _fixture(prefix + "pinnwire_markets.json")
        main = _fixture(prefix + "pinnwire_fixtures.json") if os.path.exists(
            os.path.join(FIXTURES, prefix + "pinnwire_fixtures.json")) else payload
    else:
        key = os.environ["PINNWIRE_KEY"]
        payload = _get(f"{PINN_BASE}/kit/v1/markets",
                       {"sport_id": sport_id, "include_specials": 1, "key": key})
        main = _get(f"{PINN_BASE}/kit/v1/prematch/fixtures", {"sport_id": sport_id, "key": key})
    props = []
    for sp in _iter_specials(payload):
        lg = (sp.get("league_name") or "").strip().upper()
        if lg and lg != league.upper():
            continue                                    # skip college etc.
        props.extend(_parse_pinn_special(sp, stat_fn))
    return props, _pinn_game_lines(main, league, abbr)
