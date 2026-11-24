"""
Phone alerts for NEW EV / EV + Cheat Sheet plays, via ntfy (free app, no account).

Setup: install "ntfy" on your phone, subscribe to your topic, and save the same
topic as the repo secret NTFY_TOPIC. Without the secret, this does nothing.

Each play is only announced once per week (docs/data/notified.json).
"""
import os

import requests

from . import config

APP_URL = "https://topline-cards.github.io/xbets-ev-app/"
MAX_LINES = 8


def _key(p):
    return f"{p['pkey']}|{p['stat']}|{p['side']}|{p['line']}"


def _line(p, star):
    book = {"draftkings": "DK", "fanduel": "FD"}.get(p["book"], p["book"])
    odds = f"+{p['american']}" if p["american"] > 0 else str(p["american"])
    bet = f"{p['player']} {p['title']}" if p.get("kind", "prop") == "prop" else p["title"]
    return f"{'⭐ ' if star else ''}{bet} {odds} {book} · EV +{p['ev_pct']:.1f}% · {p['units']}u"


def notify_new(out, load, save):
    topic = os.environ.get("NTFY_TOPIC", "").strip()
    seen = load("notified.json", {})
    if seen.get("week") != out["week"]:
        seen = {"week": out["week"], "keys": []}
    keys = set(seen["keys"])

    combined_keys = {_key(p) for p in out["combined"]}
    new = [p for p in out["ev"] if p.get("units") and _key(p) not in keys
           and p["ev_pct"] >= config.NOTIFY_MIN_EV_PCT]
    if not new:
        return 0
    new.sort(key=lambda p: (_key(p) not in combined_keys, -p["ev_pct"]))
    n_star = sum(_key(p) in combined_keys for p in new)

    if topic:
        lines = [_line(p, _key(p) in combined_keys) for p in new[:MAX_LINES]]
        if len(new) > MAX_LINES:
            lines.append(f"+{len(new) - MAX_LINES} more in the app")
        title = (f"{len(new)} new EV play{'s' if len(new) > 1 else ''}"
                 + (f" ({n_star} also on the cheat sheet ⭐)" if n_star else ""))
        try:
            requests.post(f"https://ntfy.sh/{topic}", data="\n".join(lines).encode(),
                          headers={"Title": title.encode(), "Click": APP_URL,
                                   "Priority": "high" if n_star else "default",
                                   "Tags": "football"}, timeout=15)
        except Exception as e:
            print("notify failed:", e)

    seen["keys"] = sorted(keys | {_key(p) for p in new})
    save("notified.json", seen)
    return len(new)
