"""Probe which SharpAPI query options drop alternate lines (SharpAPI calls only)."""
import json
import os
import time

from . import odds
from .build import _save


def main():
    key = os.environ["SHARPAPI_KEY"]
    base = {"league": "nfl", "sportsbook": "draftkings,fanduel", "is_live": "false",
            "limit": 200, "market": ",".join(odds.SHARP_MARKETS)}
    out = {}
    for name, extra in {
        "none": {}, "is_main_line=true": {"is_main_line": "true"},
        "main_line=true": {"main_line": "true"}, "is_alternate_line=false": {"is_alternate_line": "false"},
        "alternates=false": {"alternates": "false"}, "include_alternates=false": {"include_alternates": "false"},
        "main_only=true": {"main_only": "true"}, "alt_lines=false": {"alt_lines": "false"},
    }.items():
        try:
            page = odds._get(f"{odds.SHARP_BASE}/odds", dict(base, **extra), {"X-API-Key": key})
            rows = page.get("data", [])
            out[name] = {"rows": len(rows), "alt": sum(1 for r in rows if r.get("is_alternate_line")),
                         "has_more": (page.get("pagination") or {}).get("has_more")}
        except Exception as e:
            out[name] = {"error": str(e)[:150]}
        time.sleep(5.5)
    # how many pages does the current filter take in total?
    params, pages, rows, alt, cursor = dict(base), 0, 0, 0, None
    while pages < 90:
        if cursor:
            params["cursor"] = cursor
        page = odds._get(f"{odds.SHARP_BASE}/odds", params, {"X-API-Key": key})
        pages += 1
        d = page.get("data", [])
        rows += len(d)
        alt += sum(1 for r in d if r.get("is_alternate_line"))
        pg = page.get("pagination") or {}
        if not pg.get("has_more") or not pg.get("next_cursor"):
            break
        cursor = pg["next_cursor"]
        time.sleep(5.2)
    out["full_scan_current"] = {"pages": pages, "rows": rows, "alt_rows": alt}
    _save("probe.json", out)
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
