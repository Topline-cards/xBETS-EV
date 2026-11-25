"""Check Basketball-Reference positions load on GitHub Actions and save the cache."""
import collections
import json

from .. import build as pb
from . import stats


def main():
    with __import__("pipeline.nba.build", fromlist=["nba_data"]).nba_data():
        season = stats.season_for()
        m = stats.position_map(season)
        out = {"players": len(m), "by_pos": collections.Counter(m.values()).most_common(),
               "sample": dict(list(m.items())[:8])}
        pb._save("probe_pos.json", out)
    print(json.dumps(out))


if __name__ == "__main__":
    main()
