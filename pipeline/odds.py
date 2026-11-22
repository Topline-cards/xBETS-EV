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
def fetch_sharpapi():
    """Returns (props, game_lines) for DK/FD."""
    if FIXTURES:
        pages = [_fixture("sharpapi_odds.json")]
    else:
        key = os.environ["SHARPAPI_KEY"]
        params = {"league": "nfl", "sportsbook": ",".join(config.BET_BOOKS),
                  "is_live": "false", "limit": 200}
        pages, cursor = [], None
        for _ in range(60):                      # hard stop
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
                _add_game_line(games, o)
                continue
            if o.get("is_active") is False or o.get("is_stale_pregame_price"):
                continue
            if (o.get("market_segment") or "full_game") != "full_game":
                continue
            stat = stat_from_text(o.get("stat_category") or o.get("market_type"))
            if not stat or o.get("line") is None or not o.get("player_name"):
                continue
            side = (o.get("selection_type") or o.get("selection") or "").lower()
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


def _add_game_line(games, o):
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
    home = team_abbr(o.get("home_team"), (o.get("home") or {}).get("abbreviation"))
    away = team_abbr(o.get("away_team"), (o.get("away") or {}).get("abbreviation"))
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


def _parse_pinn_special(sp):
    """
    Pinnacle names props like 'Josh Allen (Passing Yards)' with Over/Under
    outcomes. Returns list of normalized rows.
    """
    name = sp.get("special") or sp.get("name") or ""
    m = _PAREN.match(name)
    player, stat_txt = (m.group(1), m.group(2)) if m else (None, name)
    stat = stat_from_text(stat_txt)
    if not stat:
        return []
    rows = {}
    markets = sp.get("special_markets") or {}
    period = markets.get("num_0") or []
    for mk in period:
        for pr in mk.get("prices", []):
            side = (pr.get("name") or "").strip().lower()
            if side not in ("over", "under"):
                continue
            if pr.get("points") is None or not pr.get("price"):
                continue
            pl = player or pr.get("participant_name")
            if not pl:
                continue
            line = float(pr["points"])
            r = rows.setdefault((pkey(pl), line), {
                "book": "pinnacle", "player": pl, "pkey": pkey(pl), "stat": stat,
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


def _pinn_game_lines(payload):
    out = []
    for ev in payload.get("events", []):
        if ev.get("special") or ev.get("parent_id"):
            continue
        if "nfl" not in (ev.get("league_name") or "").lower():
            continue
        home, away = team_abbr(ev.get("home")), team_abbr(ev.get("away"))
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


def fetch_pinnacle():
    """Returns (props, game_lines) from one PinnWire call."""
    if FIXTURES:
        payload = _fixture("pinnwire_markets.json")
    else:
        key = os.environ["PINNWIRE_KEY"]
        payload = _get(f"{PINN_BASE}/kit/v1/markets",
                       {"sport_id": 5, "include_specials": 1, "key": key})
    props = []
    for sp in _iter_specials(payload):
        league = (sp.get("league_name") or "").lower()
        if league and "nfl" not in league:
            continue                                    # skip college etc.
        props.extend(_parse_pinn_special(sp))
    return props, _pinn_game_lines(payload)
