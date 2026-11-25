"""SharpAPI league id + team naming for college basketball; Pinnacle league names (1 PinnWire call)."""
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
    h = {"X-API-Key": os.environ["SHARPAPI_KEY"]}
    try:
        r = requests.get(f"{odds.SHARP_BASE}/leagues", headers=h, timeout=20)
        j = r.json()
        lst = j.get("data", j) if isinstance(j, dict) else j
        out["leagues"] = [x for x in lst if "basket" in json.dumps(x).lower() or "ncaa" in json.dumps(x).lower()][:20] \
            if isinstance(lst, list) else trim(j)
    except Exception as e:
        out["leagues_err"] = str(e)[:200]
    for lg in ("ncaab", "ncaamb", "ncaabb", "cbb", "college_basketball", "ncaa_basketball", "mens_college_basketball"):
        time.sleep(5.5)
        try:
            r = requests.get(f"{odds.SHARP_BASE}/odds", headers=h, timeout=30,
                             params={"league": lg, "sportsbook": "draftkings,fanduel", "is_live": "false",
                                     "limit": 200, "market": "moneyline,point_spread,total_points",
                                     "is_main_line": "true"})
            j = r.json()
            d = j.get("data", []) if isinstance(j, dict) else []
            out[f"odds_{lg}"] = {"status": r.status_code, "rows": len(d),
                                 "err": (j.get("error") if isinstance(j, dict) else None),
                                 "teams": sorted({x.get("home_team") for x in d if x.get("home_team")})[:40],
                                 "sample": trim(d[:1])}
            if d:
                break
        except Exception as e:
            out[f"odds_{lg}"] = str(e)[:200]
    try:
        fx = odds._get(f"{odds.PINN_BASE}/kit/v1/prematch/fixtures", {"sport_id": 3, "key": os.environ["PINNWIRE_KEY"]})
        ev = fx.get("events", []) if isinstance(fx, dict) else fx
        out["pinn_leagues"] = collections.Counter(e.get("league_name") for e in ev).most_common(25)
        ncaa = [e for e in ev if "NCAA" in str(e.get("league_name", "")).upper()]
        out["pinn_ncaa_teams"] = sorted({e.get("home") for e in ncaa})[:60]
        out["pinn_ncaa_sample"] = trim(ncaa[:1])
    except Exception as e:
        out["pinn_err"] = str(e)[:200]
    _save("probe_cbb.json", out)
    print(json.dumps(out)[:2000])


if __name__ == "__main__":
    main()
