"""Live check: does the NFL odds parser pick up TD markets? (SharpAPI + 2 PinnWire calls)"""
import collections
import json

from . import odds
from .build import _save


def main():
    props, games = odds.fetch_sharpapi()
    pp, pg = odds.fetch_pinnacle()
    out = {"book_stats": collections.Counter(f"{p['book']}:{p['stat']}" for p in props).most_common(),
           "pinn_stats": collections.Counter(p["stat"] for p in pp).most_common(),
           "td_sample": [p for p in props if p["stat"] == "anytime_td"][:4],
           "pass_td_sample": [p for p in props if p["stat"] == "pass_tds"][:2],
           "pinn_pass_td": [p for p in pp if p["stat"] == "pass_tds"][:2]}
    _save("probe_td.json", out)
    print(json.dumps(out)[:3000])


if __name__ == "__main__":
    main()
