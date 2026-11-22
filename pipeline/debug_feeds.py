"""
Diagnostic: saves a trimmed sample of what each odds API really returns to
docs/data/debug.json (no API keys are written). Run from Actions > Debug feeds.
"""
import collections
import json
import os

from . import odds
from .build import _save


def trim(o, depth=0):
    """Shorten big nested objects so the sample stays readable."""
    if depth > 6:
        return "…"
    if isinstance(o, dict):
        return {k: trim(v, depth + 1) for k, v in list(o.items())[:30]}
    if isinstance(o, list):
        return [trim(v, depth + 1) for v in o[:3]] + ([f"… {len(o) - 3} more"] if len(o) > 3 else [])
    return o


def main():
    out = {}
    # ---- PinnWire
    try:
        key = os.environ["PINNWIRE_KEY"]
        payload = odds._get(f"{odds.PINN_BASE}/kit/v1/markets",
                            {"sport_id": 5, "include_specials": 1, "key": key})
        evs = payload.get("events", [])
        nfl = [e for e in evs if "nfl" in str(e.get("league_name", "")).lower()]
        props = [e for e in nfl if e.get("special_category") == "Player Props"]
        main = odds._get(f"{odds.PINN_BASE}/kit/v1/markets", {"sport_id": 5, "key": key})
        mev = main.get("events", [])
        out["pinnwire_no_specials"] = {
            "n_events": len(mev),
            "league_counts": collections.Counter(e.get("league_name") for e in mev).most_common(10),
            "with_periods": sum(1 for e in mev if e.get("periods")),
            "nfl_sample": trim(next((e for e in mev if "nfl" in str(e.get("league_name", "")).lower()), None)),
        }
        fx = odds._get(f"{odds.PINN_BASE}/kit/v1/prematch/fixtures", {"sport_id": 5, "key": key})
        fev = fx.get("events", fx.get("fixtures", [])) if isinstance(fx, dict) else fx
        out["pinnwire_fixtures"] = {"top_keys": list(fx.keys()) if isinstance(fx, dict) else None,
                                    "n": len(fev or []), "sample": trim((fev or [None])[0])}
        out["pinnwire"] = {
            "n_events": len(evs), "n_nfl": len(nfl),
            "category_counts": collections.Counter(e.get("special_category") for e in nfl).most_common(20),
            "with_periods": sum(1 for e in nfl if e.get("periods")),
            "main_event_sample": trim(next((e for e in nfl if e.get("periods")), None)),
            "player_prop_names": sorted({" ".join(e.get("special", "").split()[-2:]) for e in props})[:60],
            "player_prop_samples": [trim(e) for e in props[:3]],
        }
    except Exception as e:
        out["pinnwire_error"] = str(e)[:300]

    # ---- SharpAPI (first page only)
    try:
        key = os.environ["SHARPAPI_KEY"]
        page = odds._get(f"{odds.SHARP_BASE}/odds",
                         {"league": "nfl", "sportsbook": "draftkings,fanduel", "is_live": "false",
                          "limit": 200}, {"X-API-Key": key})
        rows = page.get("data", [])
        out["sharpapi"] = {
            "pagination": page.get("pagination"),
            "market_type_counts": collections.Counter(r.get("market_type") for r in rows).most_common(40),
            "stat_category_counts": collections.Counter(r.get("stat_category") for r in rows).most_common(40),
            "game_line_sample": [trim(r) for r in rows if not r.get("is_player_prop")][:4],
            "prop_sample": [trim(r) for r in rows if r.get("is_player_prop")][:2],
        }
        mk = odds._get(f"{odds.SHARP_BASE}/markets", {"sport": "football", "limit": 500}, {"X-API-Key": key})
        ids = [m.get("id") for m in mk.get("data", [])]
        out["sharpapi_market_ids"] = [i for i in ids if i and ("player" in i or i in
                                      ("moneyline", "point_spread", "total_points", "spread", "total"))]
        out["sharpapi_n_markets"] = len(ids)
        pp = odds._get(f"{odds.SHARP_BASE}/odds",
                       {"league": "nfl", "sportsbook": "draftkings", "is_live": "false", "limit": 5,
                        "market": "player_receiving_yards,moneyline,point_spread,total_points"},
                       {"X-API-Key": key})
        out["sharpapi_filtered_sample"] = [trim(r) for r in pp.get("data", [])][:5]
    except Exception as e:
        out["sharpapi_error"] = str(e)[:300]

    _save("debug.json", out)
    print(json.dumps(out)[:3000])


if __name__ == "__main__":
    main()
