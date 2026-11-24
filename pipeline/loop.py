"""
Game-day loop. GitHub's schedule is unreliable, so one run keeps itself going:

  * refreshes every REFRESH_MIN minutes while a game kicks off within the
    pregame window (or is being played)
  * sleeps until the window opens if the next kickoff today is further out
  * commits picks after every refresh so the app updates live
  * before GitHub's 6-hour job limit, starts a fresh run of itself and exits
  * stops when no game kicks off in the next LOOKAHEAD_H hours
  * grades finished picks at the start of each run

  python -m pipeline.loop
"""
import os
import subprocess
import time
from datetime import datetime, timedelta

from . import build, config, stats
from .stats import ET

REFRESH_MIN = 10
MAX_RUNTIME_MIN = 330          # GitHub kills jobs at 360 min
GAME_LENGTH_H = 3.5
LOOKAHEAD_H = 36               # keep going overnight Sun -> Mon night


def sh(*cmd):
    return subprocess.run(cmd, capture_output=True, text=True)


def commit(msg):
    sh("git", "add", "docs/data")
    if sh("git", "diff", "--cached", "--quiet").returncode == 0:
        return
    sh("git", "commit", "-m", msg)
    for _ in range(3):
        if sh("git", "pull", "--rebase", "-X", "theirs").returncode == 0 and \
                sh("git", "push").returncode == 0:
            return
        time.sleep(5)


def relaunch():
    """Start a new run of this workflow (allowed with GITHUB_TOKEN)."""
    repo, token = os.environ.get("GITHUB_REPOSITORY"), os.environ.get("GITHUB_TOKEN")
    if not repo or not token:
        return
    import requests
    r = requests.post(f"https://api.github.com/repos/{repo}/actions/workflows/update.yml/dispatches",
                      headers={"Authorization": f"Bearer {token}",
                               "Accept": "application/vnd.github+json"},
                      json={"ref": "main"}, timeout=30)
    print("relaunch:", r.status_code)


def today_games(sched, now):
    live_from = now - timedelta(hours=GAME_LENGTH_H)
    return sched[(sched["kickoff"] > live_from) &
                 (sched["kickoff"] < now + timedelta(hours=LOOKAHEAD_H))]


def wait_and_grade(start):
    """
    After the last game, box scores post overnight. Check hourly and grade,
    so My Results / Results update the next morning without GitHub's cron.
    """
    from . import grade
    deadline = time.time() + 16 * 3600
    while time.time() < deadline:
        try:
            pending = grade.grade()
            commit(f"Grade results {datetime.now(ET):%Y-%m-%d %H:%M} ET")
            print(f"graded; {pending} still waiting on box scores", flush=True)
            if not pending:
                return
        except Exception as e:
            print("grading error:", e)
        if (time.time() - start) / 60 + 60 > MAX_RUNTIME_MIN:
            print("Handing off grading to a new run.")
            relaunch()
            return
        time.sleep(3600)


def main(force_first=False):
    start = time.time()
    first = True
    try:
        from . import grade
        grade.grade()
    except Exception as e:
        print("grading skipped:", e)
    while True:
        now = datetime.now(ET)
        o = build.build(now=now, force=force_first and first)
        first = False
        print(f"{now:%H:%M} week {o['week']}: combined={len(o['combined'])} ev={len(o['ev'])} "
              f"cheat={len(o['cheat'])} {o['status']}", flush=True)
        commit(f"Update picks {now:%Y-%m-%d %H:%M} ET")

        sched = stats.slate(stats.load_schedule(stats.season_for(now)))
        games = today_games(sched, datetime.now(ET))
        upcoming = games[games["kickoff"] > datetime.now(ET)]
        if upcoming.empty:
            wait_and_grade(start)
            return
        nxt = upcoming["kickoff"].min()
        window_opens = nxt - timedelta(hours=config.PREGAME_WINDOW_HOURS)
        now = datetime.now(ET)
        wait = REFRESH_MIN * 60 if now >= window_opens else (window_opens - now).total_seconds()

        elapsed_min = (time.time() - start) / 60
        if elapsed_min + wait / 60 > MAX_RUNTIME_MIN:
            print("Near GitHub's time limit; handing off to a new run.")
            relaunch()
            return
        print(f"Next refresh in {wait / 60:.0f} min (next kickoff {nxt:%a %H:%M} ET)", flush=True)
        time.sleep(max(wait, 60))


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    main(force_first=ap.parse_args().force)
