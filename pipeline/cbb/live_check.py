"""Forced CBB build into a scratch folder: shows live team matching (1 PinnWire call). Not committed."""
import json
import tempfile

from .. import build as pb


def main():
    real = pb.DATA
    tmp = tempfile.mkdtemp()
    pb.DATA = tmp
    from . import build
    o = build.build(force=True)
    pb.DATA = real
    st = o["status"]
    out = {"day": o["day"], "status": st, "games": len(o["games"]), "model_games": len(o["game_model"]),
           "ev": [(p["title"], p["book"], p["american"], p["ev_pct"], p["tier"], p["units"]) for p in o["ev"][:12]],
           "combined": len(o["combined"]), "cheat": len(o["cheat"])}
    pb._save("cbb_check.json", out)
    print(json.dumps(out)[:3000])


if __name__ == "__main__":
    main()
