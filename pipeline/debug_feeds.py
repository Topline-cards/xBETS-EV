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
        for name, params in {
            "football_specials_flat": {"sport_id": 5, "include_specials": 1},
            "football_specials_nested": {"sport_id": 5, "include_specials": "nested"},
        }.items():
            payload = odds._get(f"{odds.PINN_BASE}/kit/v1/markets", dict(params, key=key))
            evs = payload.get("events", []) if isinstance(payload, dict) else []
            out[f"pinnwire_{name}"] = {
                "top_level_keys": list(payload.keys()) if isinstance(payload, dict) else str(type(payload)),
                "n_events": len(evs),
                "league_counts": collections.Counter(e.get("league_name") for e in evs).most_common(15),
                "special_names_sample": [e.get("special") or e.get("name") for e in evs
                                         if e.get("special") or e.get("special_markets")][:25],
                "nfl_main_event_sample": trim(next((e for e in evs if "nfl" in str(e.get("league_name", "")).lower()
                                                    and not e.get("parent_id")), None)),
                "nfl_special_sample": trim(next((e for e in evs if e.get("special_markets")
                                                 or e.get("specials")), None)),
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
        mk = odds._get(f"{odds.SHARP_BASE}/markets", {"sport": "football"}, {"X-API-Key": key})
        out["sharpapi_markets"] = trim(mk)
    except Exception as e:
        out["sharpapi_error"] = str(e)[:300]

    _save("debug.json", out)
    print(json.dumps(out)[:3000])


if __name__ == "__main__":
    main()
