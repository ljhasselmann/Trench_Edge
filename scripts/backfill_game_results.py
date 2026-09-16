#!/usr/bin/env python3
"""Backfill real game outcomes + ATS-cover results into history/*.json
snapshots (DESIGN.md Section 8's backtesting plan).

`run_week.py` runs pre-kickoff -- rosters and matchups need to be ready
ahead of the game, so it can never know the real score at generation
time. This is a SEPARATE, re-runnable pass meant to run after a week's
games are final: it reads each history/{year}-wk{week:02d}/*.json file
already on disk and attaches a `result` block to it, in place.

Two real data sources, both already fetched elsewhere in this repo but
never used for backtesting until now:
- fetch_matchups.fetch_game_results(): CFBD's /games response already
  carries homePoints/awayPoints/completed on every row -- confirmed live.
- fetch_sp_plus.fetch_week_lines(): the SAME "FBS Week N" Google Sheet
  tab this repo already reads for Push also carries a real per-game
  schedule with actual betting spreads and Connelly's own ATS picks, in
  columns to the left of the ratings table this repo used to stop
  reading at -- confirmed live against the real Week 3 Miami/Wake Forest
  line ("Miami-FL -22.5"). If that lines fetch fails (sheet down, or
  this week not published), scores are still backfilled -- ATS fields
  are just omitted from `result`, never guessed at.

`cover_margin_for_team_a` is the number a backtest should actually
correlate against a composite score, not raw margin: it's how many
points BETTER than the closing line's own expectation team_a performed
(positive = team_a beat the market's expectation; negative = team_b
did). Raw margin conflates "this team is good" (already priced into the
spread) with "this team beat what was already expected" -- only the
second is a fair test of whether OUR trench-specific composite adds
anything beyond what oddsmakers/SP+ already knew.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

import requests

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

import fetch_matchups
import fetch_sp_plus

HISTORY_DIR = REPO_ROOT / "history"


def compute_result_block(team_a: str, team_b: str, game_result: dict, game_line) -> dict:
    """team_a/team_b are the game's own away/home identity (fetch_matchups
    convention). `game_line` is an Optional[fetch_sp_plus.GameLine] --
    None means no lines fetch succeeded or no line was posted for this
    game (e.g. an FCS opponent), in which case only the real score is
    recorded, no ATS fields."""
    home_points, away_points = game_result["home_points"], game_result["away_points"]
    team_a_points, team_b_points = away_points, home_points  # team_a=away, team_b=home
    margin_for_team_a = team_a_points - team_b_points

    result = {
        "home_points": home_points,
        "away_points": away_points,
        "team_a_points": team_a_points,
        "team_b_points": team_b_points,
        "margin_for_team_a": margin_for_team_a,
    }

    if game_line is None or game_line.favorite is None or game_line.spread is None:
        return result

    sheet_a = fetch_sp_plus._to_sheet_name(team_a)
    sheet_b = fetch_sp_plus._to_sheet_name(team_b)
    if game_line.favorite == sheet_a:
        favorite_is_team_a = True
    elif game_line.favorite == sheet_b:
        favorite_is_team_a = False
    else:
        return result  # favorite name didn't resolve to either team -- don't guess

    expected_margin_for_team_a = game_line.spread if favorite_is_team_a else -game_line.spread
    result["spread"] = game_line.spread
    result["favorite"] = team_a if favorite_is_team_a else team_b
    result["expected_margin_for_team_a"] = expected_margin_for_team_a
    result["cover_margin_for_team_a"] = margin_for_team_a - expected_margin_for_team_a
    if game_line.ats_pick is not None:
        result["ats_pick"] = fetch_sp_plus._from_sheet_name(game_line.ats_pick)

    return result


def backfill_week(
    year: int,
    week: int,
    history_dir: Path = HISTORY_DIR,
    session: Optional[requests.Session] = None,
    force: bool = False,
) -> dict:
    week_dir = history_dir / f"{year}-wk{week:02d}"
    if not week_dir.exists():
        return {"year": year, "week": week, "updated": [], "pending": [], "already_up_to_date": [], "lines_warning": f"no history directory: {week_dir}"}

    game_results = fetch_matchups.fetch_game_results(year, week, session=session)
    try:
        game_lines = fetch_sp_plus.fetch_week_lines(week, session=session)
        lines_warning = None
    except fetch_sp_plus.SPPlusFetchError as exc:
        game_lines = []
        lines_warning = f"real spreads unavailable this backfill -- scores only, no ATS fields: {exc}"

    updated, pending, already_up_to_date = [], [], []
    for path in sorted(week_dir.glob("*.json")):
        data = json.loads(path.read_text())
        if "direction_a" not in data:
            continue  # legacy pre-four-corners snapshot -- not this schema, skip

        team_a, team_b = data["team_a"], data["team_b"]
        if "result" in data and not force:
            already_up_to_date.append(path.name)
            continue

        game_result = game_results.get((team_a, team_b))
        if game_result is None:
            pending.append(path.name)  # not yet final, or not found -- never fabricate a result
            continue

        game_line = fetch_sp_plus.find_game_line(game_lines, team_a, team_b)
        data["result"] = compute_result_block(team_a, team_b, game_result, game_line)
        path.write_text(json.dumps(data, indent=2))
        updated.append(path.name)

    return {"year": year, "week": week, "updated": updated, "pending": pending, "already_up_to_date": already_up_to_date, "lines_warning": lines_warning}


def print_summary(summary: dict) -> None:
    print(f"Backfill -- {summary['year']} Week {summary['week']}")
    print(f"  {len(summary['updated'])} updated, {len(summary['already_up_to_date'])} already up to date, {len(summary['pending'])} pending (not yet final)")
    if summary["lines_warning"]:
        print(f"  {summary['lines_warning']}")
    if summary["pending"]:
        print("  Pending:")
        for name in summary["pending"]:
            print(f"    - {name}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backfill real game outcomes + ATS results into history/*.json.")
    parser.add_argument("--year", type=int, required=True)
    parser.add_argument("--week", type=int, required=True)
    parser.add_argument("--force", action="store_true", help="Recompute result for games that already have one")
    args = parser.parse_args()

    summary = backfill_week(args.year, args.week, force=args.force)
    print_summary(summary)
    sys.exit(0)
