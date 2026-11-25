# xBETS · NFL + NBA

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
| Cheat sheet (props) | What each defense allows per position vs league average, then player average × matchup (50% weight). QBs: only the named starter. | `pipeline/matchups.py` |
| Cheat sheet (game lines) | Team points scored/allowed vs league average → projected score, margin, total; moneyline/spread/total edges | `pipeline/gamelines.py` |
| Odds | DK/FD props + ML/spread/total from SharpAPI; Pinnacle from PinnWire (same calls) | `pipeline/odds.py` |
| EV + units | No-vig Pinnacle fair odds, EV %, quarter-Kelly units | `pipeline/ev.py` |
| Job | Runs every 10 min; pulls odds only within 4h of a kickoff | `pipeline/build.py`, `.github/workflows/update.yml` |
| Grading | Grades picks each morning once box scores are posted | `pipeline/grade.py`, `.github/workflows/grade.yml` |
| App | Static page on GitHub Pages: filters by game / bet type / book; tap any bet for the full breakdown (game logs, defense log, EV and unit math, every book's price) | `docs/index.html` |

**Recommended units:** 1u = $10 by default (change it under ⚙️). No budget cap, so no +EV play is ever voided. Each stake is:
1. Base: quarter-Kelly on a trusted chance (only 50% of Pinnacle's prop edge counts, 80% on game lines).
2. × confidence: 1.25 if the cheat sheet agrees, 0.6 if the matchup points the other way, 0.5 if Questionable.
3. × correlation: 1/√(bets in the same game) and 1/(bets on the same player).
4. Capped at 1.5u props / 2u lines (0.5u if EV > 12%), rounded to 0.25u, never below 0.25u.

Last Sunday replayed: 23 plays, 8u total.

**My Players:** `docs/players.html`. Thumbs-up players get pinned to the top of every list; thumbs-down players are hidden. Saved in the browser on each device.

**API budget:** PinnWire's free plan allows 100 calls/day. The job makes one call per run, only inside the 4-hour pregame window, and stops at 90/day.

**Sundays + Thanksgiving:** the app covers Sunday games and every Thanksgiving Day game (`GAME_WEEKDAYS` in `pipeline/config.py`). Games on other days still feed the stats.

**NBA:** Monday through Friday, plus Christmas Day (`pipeline/nba/config.py`). Switch sports with the ☰ menu in the app.
- Stats: ESPN box scores and schedule (sportsdataverse releases, updated daily); injuries from ESPN's injury report (Out / Doubtful hidden, Day-To-Day sized down).
- Props: points, rebounds, assists, threes made, points + rebounds + assists. Also moneyline, spread, total.
- Cheat sheet: player's last 10 games x what the opponent allows to guards / forwards / centers.
- Same EV math and unit sizing as the NFL. PinnWire's 100 calls a day are shared by both sports.
- Output: `docs/data/nba/`. Grading: `python -m pipeline.nba.grade`.

**Phone alerts:** install the free **ntfy** app, subscribe to a private topic name, and save that name as the repo secret `NTFY_TOPIC`. Each new EV play is announced once (⭐ = also a cheat-sheet play); tapping the alert opens the app.

## Pricing rules (from the +EV Best Practices Playbook)
- **Fair line:** Pinnacle (PinnWire). Devigged four ways (multiplicative, additive, power, Shin; Shin skipped on main lines); we bet off the **worst** result for our side.
- **Sliding EV threshold:** ~1% at -150, 3% at +130, 5% at +175, 6.5% at +250, 8% at +400, 10% at +600 and longer. No odds cap. Over 15% EV = "too good to be true": flagged and held to 0.5u; over 30% dropped as bad data.
- **Second opinion:** the other US book's own no-vig price. If it agrees, the play is tagged **2 SOURCES**; otherwise stakes x0.75.
- **Freshness:** Pinnacle prices older than 25 min are not used.
- **Sizing:** quarter-Kelly on a shrunk edge (0.10 Kelly at +300 and longer), confidence, correlation, caps. Sorted by expected profit (EV x stake).
- **Lanes + promotion gate:** each sport x market is a lane. Full stakes only after 30+ graded bets over 15+ days, EV vs sharp close >= +1%, ROI > 0. Until then stakes are halved ("TESTING"). Record tab shows every lane plus expected vs actual profit.
- **NFL touchdowns:** passing TDs priced vs Pinnacle. Anytime TD: Pinnacle has no market, so DraftKings is priced vs FanDuel (assumed 8% vig on FanDuel's "Yes", +2% extra EV required, single-source, ROI-only lane gate). Cheat sheet uses a Poisson model on matchup-adjusted TDs per game.
- Not built (needs more books/feeds than DK + FD + Pinnacle): extra sharp books (SharpAPI's $399 tier), exchanges, DFS pick'em, live betting, arbs, promos.

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
