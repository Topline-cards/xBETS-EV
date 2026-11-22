# xBETS NFL Props

Phone app for NFL player props with three views:

- **EV + Cheat Sheet:** cheat-sheet plays that are also +EV. The strictest list.
- **EV:** DraftKings/FanDuel prices better than Pinnacle's no-vig fair line, with unit sizing.
- **Cheat Sheet:** defense-vs-position matchups turned into player projections vs the prop line.
- **Results:** paper-trading record for each view (win/loss, units, ROI, and how often the price beat the closing line).

Everything runs on GitHub. Nothing runs on your computer.

## How it works

| Piece | What it does | Where |
|---|---|---|
| Stats + schedule | nflverse box scores, free, no key | `pipeline/stats.py` |
| Cheat sheet | What each defense allows per position vs league average, then player average × matchup (50% weight) | `pipeline/matchups.py` |
| Odds | DK/FD props from SharpAPI; Pinnacle props from PinnWire | `pipeline/odds.py` |
| EV + units | No-vig Pinnacle fair odds, EV %, quarter-Kelly units | `pipeline/ev.py` |
| Job | Runs every 10 min; pulls odds only within 4h of a kickoff | `pipeline/build.py`, `.github/workflows/update.yml` |
| Grading | Grades picks each morning once box scores are posted | `pipeline/grade.py`, `.github/workflows/grade.yml` |
| App | Static page served by GitHub Pages | `docs/index.html` |

**Units:** 1u = 1% of bankroll. Size = ¼ Kelly: `(b·p − (1−p)) / b × 0.25`, where `p` is Pinnacle's fair win probability and `b` is decimal odds − 1. Max 3u per bet and 5u per game (best EV bets get filled first). All settings are in `pipeline/config.py`.

**API budget:** PinnWire's free plan allows 100 calls/day. The job makes one call per run, only inside the 4-hour pregame window, and stops at 90/day.

## Setup (one time)

1. **Secrets:** Settings → Secrets and variables → Actions → New repository secret
   - `SHARPAPI_KEY`: your SharpAPI key
   - `PINNWIRE_KEY`: your PinnWire key
2. **Pages:** Settings → Pages → Source: *Deploy from a branch* → Branch `main`, folder `/docs`.
3. **First run:** Actions → *Update picks* → Run workflow (tick *force* to pull odds immediately).
4. On your phone, open `https://topline-cards.github.io/xbets-ev-app/`, then Share → *Add to Home Screen*.

## Testing offline

```bash
pip install -r requirements.txt playwright
python -m tests.make_fixtures 2026-11-22T10:00:00-05:00     # FAKE odds for testing
DATA_DIR=/tmp/site/data ODDS_FIXTURES=tests/fixtures python -c \
  "from pipeline import build; build.build(force=True)"
```

Paper-trade for a few weeks before betting real money. *Beat close %* in Results is the best early sign the edges are real.
