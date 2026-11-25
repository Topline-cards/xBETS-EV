"""Which extra sharp books/exchanges SharpAPI gives us, and what NFL touchdown markets look like.
~10 SharpAPI calls, 1 PinnWire call."""
import collections
import json
import os
import time

import requests

from . import odds
from .build import _save
from .debug_feeds import trim

CANDIDATES = ["pinnacle", "circa", "bookmaker", "betonline", "kalshi", "novig", "prophetx", "polymarket",
              "caesars", "betmgm", "espnbet", "fanatics", "bet365", "betrivers", "hardrock", "sporttrade"]


def main():
    out = {}
    key = os.environ["SHARPAPI_KEY"]
    h = {"X-API-Key": key}
    for path in ("/sportsbooks", "/books", "/meta/sportsbooks"):
        try:
            r = requests.get(f"{odds.SHARP_BASE}{path}", headers=h, timeout=20)
            out[f"list{path}"] = {"status": r.status_code, "body": trim(r.json()) if r.ok else r.text[:300]}
        except Exception as e:
            out[f"list{path}"] = str(e)[:200]
        time.sleep(5.5)
    books = {}
    for b in CANDIDATES:
        try:
            r = requests.get(f"{odds.SHARP_BASE}/odds", headers=h, timeout=30,
                             params={"league": "nfl", "sportsbook": b, "is_live": "false", "limit": 50})
            j = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
            d = j.get("data", []) if isinstance(j, dict) else []
            books[b] = {"status": r.status_code, "rows": len(d),
                        "markets": collections.Counter(x.get("market_type") for x in d).most_common(5),
                        "msg": (j.get("error") or j.get("message")) if isinstance(j, dict) else None}
        except Exception as e:
            books[b] = str(e)[:200]
        time.sleep(5.5)
    out["books"] = books
    try:
        r = requests.get(f"{odds.SHARP_BASE}/odds", headers=h, timeout=30,
                         params={"league": "nfl", "sportsbook": "draftkings,fanduel", "is_live": "false",
                                 "limit": 200, "market": "player_anytime_touchdown,player_anytime_td,anytime_touchdown_scorer,player_touchdowns,player_first_touchdown"})
        d = r.json().get("data", [])
        out["td_markets"] = collections.Counter((x.get("market_type"), x.get("selection_type"), x.get("line")) for x in d).most_common(15)
        out["td_sample"] = [trim(x) for x in d[:2]]
    except Exception as e:
        out["td_error"] = str(e)[:300]
    try:
        pay = odds._get(f"{odds.PINN_BASE}/kit/v1/markets",
                        {"sport_id": 5, "include_specials": 1, "key": os.environ["PINNWIRE_KEY"]})
        sp = [e for e in pay.get("events", []) if e.get("special") and "NFL" == str(e.get("league_name", "")).upper()]
        names = collections.Counter(e.get("special_units") for e in sp)
        tds = [e for e in sp if "touch" in json.dumps(e).lower() or " td" in json.dumps(e).lower()]
        out["pinn_units"] = names.most_common(40)
        out["pinn_td_names"] = sorted({e.get("special") for e in tds})[:30]
        out["pinn_td_sample"] = [trim(e) for e in tds[:2]]
    except Exception as e:
        out["pinn_error"] = str(e)[:300]
    _save("probe_books.json", out)
    print(json.dumps(out)[:3000])


if __name__ == "__main__":
    main()
