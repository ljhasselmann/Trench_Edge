#!/usr/bin/env python3
"""One-time (or periodic) backfill: populates config/rosters/{team}.yaml
for every FBS team, not just the ones already touched by a matchup a
particular week has cared about. run_week.py only ever populates the
teams involved in that week's discovered/hand-curated matchups
(`unique_teams(matchups)`), so most of the league never gets a
config/rosters/{team}.yaml until they happen to play a Top-25 team --
confirmed only 43 of ~138 FBS teams have one as of this backfill's first
run. A league-wide TrenchEdge OL Rank needs every team's roster staged,
regardless of who they're playing this week.

Reuses run_week.populate_all_rosters() unchanged (it already takes an
explicit team list and shares one ourlads team-index fetch + one
puntandrally browser session across every team, same as a normal
run_week() call) -- this script's only job is to compute the team list
(every FBS team, optionally minus ones already populated) and print a
summary, not to reimplement roster population.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import fetch_cfbd
import fetch_puntandrally
import run_week

ROSTERS_DIR = run_week.ROSTERS_DIR


def teams_missing_roster(all_teams: list[str]) -> list[str]:
    return [t for t in all_teams if not (ROSTERS_DIR / f"{t}.yaml").exists()]


def main() -> None:
    parser = argparse.ArgumentParser(description="Populate config/rosters/{team}.yaml for every FBS team.")
    parser.add_argument("--year", type=int, default=2026)
    parser.add_argument("--force", action="store_true", help="Re-populate teams that already have a roster file too")
    args = parser.parse_args()

    print(f"Fetching CFBD FBS teams (year={args.year})...")
    fbs_teams = [t["school"] for t in fetch_cfbd.fetch_fbs_teams(args.year)]
    print(f"  {len(fbs_teams)} FBS teams")

    targets = fbs_teams if args.force else teams_missing_roster(fbs_teams)
    already_done = len(fbs_teams) - len(targets)
    print(f"  {already_done} already have config/rosters/{{team}}.yaml, {len(targets)} to populate")

    if not targets:
        print("Nothing to do.")
        return

    today = _dt.date.today().isoformat()
    with fetch_puntandrally.browser_session() as browser_fetch:
        results = run_week.populate_all_rosters(targets, args.year, today, browser_fetch=browser_fetch)

    ok = [r for r in results if r["status"] == "ok"]
    failed = [r for r in results if r["status"] == "failed"]

    print()
    print(f"Populated {len(ok)}/{len(targets)} team(s).")
    if failed:
        print(f"{len(failed)} team(s) failed -- need manual roster research:")
        for r in failed:
            print(f"  - {r['team']}: {r['error']}")

    warned = [r for r in ok if r.get("warnings")]
    if warned:
        print(f"{len(warned)} team(s) populated with warnings:")
        for r in warned:
            for w in r["warnings"]:
                print(f"  - {r['team']}: {w}")


if __name__ == "__main__":
    main()
