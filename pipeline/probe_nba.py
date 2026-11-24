"""Probe what the odds/injury feeds return for the NBA (2 PinnWire calls, ~4 SharpAPI calls)."""
import collections
import json
import os
import time

import requests

from . import odds
from .build import _save
from .debug_feeds import trim


def main():
    out = {}
    try:
        if os.environ.get("SKIP_SHARP"):
            raise RuntimeError("skipped")
        key = os.environ["SHARPAPI_KEY"]
        h = {"X-API-Key": key}
        base = {"league": "nba", "sportsbook": "draftkings,fanduel", "is_live": "false",
                "limit": 200, "is_main_line": "true"}
        mk, cursor, rows = collections.Counter(), None, []
        for _ in range(4):
            p = dict(base, **({"cursor": cursor} if cursor else {}))
            page = odds._get(f"{odds.SHARP_BASE}/odds", p, h)
            d = page.get("data", [])
            rows += d
            mk.update(r.get("market_type") for r in d)
            pg = page.get("pagination") or {}
            if not pg.get("has_more"):
                break
            cursor = pg.get("next_cursor")
            time.sleep(5.5)
        out["sharp_markets"] = mk.most_common(60)
        out["sharp_line_sample"] = [trim(r) for r in rows if not r.get("is_player_prop")][:3]
        out["sharp_prop_sample"] = [trim(r) for r in rows if r.get("is_player_prop")][:3]
        out["sharp_pages_seen"] = len(rows)
    except Exception as e:
        out["sharp_error"] = str(e)[:300]
    try:
        key = os.environ["PINNWIRE_KEY"]
        sid = None
        for path in ("/kit/v1/sports", "/kit/v1/meta/sports", "/v1/sports"):
            try:
                sp = odds._get(f"{odds.PINN_BASE}{path}", {"key": key})
                lst = sp.get("sports", sp.get("data", sp)) if isinstance(sp, dict) else sp
                out["pinn_sports"] = trim(lst)
                for x in (lst if isinstance(lst, list) else []):
                    if "basket" in str(x).lower():
                        sid = x.get("id") or x.get("sport_id")
                        break
                break
            except Exception as e:
                out.setdefault("pinn_sports_err", []).append(f"{path}: {str(e)[:80]}")
        out["pinn_basketball_id"] = sid
        sid = sid or 3
        pay = odds._get(f"{odds.PINN_BASE}/kit/v1/markets", {"sport_id": sid, "include_specials": 1, "key": key})
        evs = [e for e in pay.get("events", []) if str(e.get("league_name", "")).upper() == "NBA"]
        props = [e for e in evs if e.get("special_category") == "Player Props"]
        out["pinn_leagues"] = collections.Counter(e.get("league_name") for e in pay.get("events", [])).most_common(8)
        out["pinn_nba"] = {"n": len(evs), "cats": collections.Counter(e.get("special_category") for e in evs).most_common(),
                           "prop_names": sorted({e.get("special", "") for e in props})[:80],
                           "units": collections.Counter(e.get("special_units") for e in props).most_common(),
                           "prop_sample": [trim(e) for e in props[:2]]}
        fx = odds._get(f"{odds.PINN_BASE}/kit/v1/prematch/fixtures", {"sport_id": sid, "key": key})
        fev = fx.get("events", []) if isinstance(fx, dict) else fx
        nba = [e for e in fev if str(e.get("league_name", "")).upper() == "NBA"]
        out["pinn_fixtures"] = {"n": len(nba), "sample": trim(nba[0] if nba else None)}
    except Exception as e:
        out["pinn_error"] = str(e)[:300]
    try:
        r = requests.get("https://site.api.espn.com/apis/site/v2/sports/basketball/nba/injuries", timeout=20)
        j = r.json()
        out["espn_injuries"] = {"status": r.status_code, "keys": list(j.keys()), "sample": trim(j.get("injuries", [])[:2])}
    except Exception as e:
        out["espn_error"] = str(e)[:300]
    _save("probe_nba.json", out)
    print(json.dumps(out)[:4000])


if __name__ == "__main__":
    main()
