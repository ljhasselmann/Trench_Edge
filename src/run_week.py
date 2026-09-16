"""Weekly orchestrator (DESIGN.md Section 7, build plan step 9).

A deterministic script, not a new agentic mode -- ties together every
module built for the "scale to all Top-25-involving games, with four
corners" plan:

1. Discover this week's Top-25-involving matchups (fetch_matchups.py),
   merged with any hand-curated entries in config/teams.yaml for the same
   week (a human's pinned matchup wins on a label collision -- they set
   fields like sp_plus_gap deliberately).
2. Populate every matchup team's config/rosters/{team}.yaml `starters`
   block live from ourlads.com (fetch_ourlads.py), deterministic-first.
   `prior_season_starters` / `continuity_note` are untouched -- those stay
   human-curated once per season, per _template.yaml. A team ourlads
   can't resolve (name miss, parse error) is reported, not guessed at --
   this plain script has no WebSearch/WebFetch access itself; a human or
   the wrapping Routine session does that fallback research for exactly
   the teams this run reports as failed, per DESIGN.md Section 7.
3. Fetch SP+ and talent tables once for the whole run (fetch_sp_plus.py,
   fetch_talent.py already support this), not once per matchup.
4. Render every matchup in both trench directions (render_widget.py's
   build_both_directions); one bad matchup (unresolvable name, CFBD
   error) is caught and reported, never aborts the rest of the run.
5. Render the week's index page and print a deterministic summary.

Roster-diff (DESIGN.md Section 7's staleness/change detection) compares
against the team's own current config/rosters/{team}.yaml right before
overwriting it -- O(1) per team, no search through history/*.json needed
(the plan's simplification over the original "most recent prior
snapshot" design).
"""

from __future__ import annotations

import argparse
import datetime as _dt
import sys
import time
from pathlib import Path
from typing import Optional

import requests
import yaml

import fetch_matchups
import fetch_ourlads
import fetch_sp_plus
import fetch_talent
import render_widget

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = REPO_ROOT / "config"
TEAMS_FILE = CONFIG_DIR / "teams.yaml"
MATCHUPS_DIR = CONFIG_DIR / "matchups"
ROSTERS_DIR = CONFIG_DIR / "rosters"
OUTPUT_DIR = REPO_ROOT / "output"
HISTORY_DIR = REPO_ROOT / "history"

# Only ~2-3 ourlads fetches had been load-tested live before this script;
# a full week is ~45. A small courtesy delay between calls, not a rate
# limit ourlads has confirmed -- just a margin against one.
OURLADS_DELAY_SECONDS = 0.75


def load_hand_curated_matchups(year: int, week: int, path: Optional[Path] = None) -> list[dict]:
    """config/teams.yaml entries for this exact year/week -- a human-pinned
    matchup (e.g. it sets a static sp_plus_gap fallback deliberately)."""
    path = path if path is not None else TEAMS_FILE
    if not path.exists():
        return []
    with open(path) as f:
        config = yaml.safe_load(f) or {}
    if config.get("year") != year:
        return []
    return [m for m in config.get("matchups", []) if m.get("week") == week]


def merge_matchups(discovered: list[dict], hand_curated: list[dict]) -> list[dict]:
    """Dedupe by label; a hand-curated entry wins on a collision."""
    by_label = {m["label"]: m for m in discovered}
    for m in hand_curated:
        by_label[m["label"]] = m
    return sorted(by_label.values(), key=lambda m: m["label"])


def write_matchups_config(matchups: list[dict], year: int, week: int) -> Path:
    MATCHUPS_DIR.mkdir(parents=True, exist_ok=True)
    path = MATCHUPS_DIR / f"{year}-wk{week:02d}.yaml"
    path.write_text(yaml.safe_dump({"year": year, "matchups": matchups}, sort_keys=False))
    return path


def unique_teams(matchups: list[dict]) -> list[str]:
    seen: list[str] = []
    for m in matchups:
        for team in (m["team_a"], m["team_b"]):
            if team not in seen:
                seen.append(team)
    return seen


def _load_roster_yaml(team: str) -> Optional[dict]:
    path = ROSTERS_DIR / f"{team}.yaml"
    if not path.exists():
        return None
    with open(path) as f:
        return yaml.safe_load(f)


def _diff_starters(old_starters: Optional[dict], new_starters: dict) -> list[str]:
    changes = []
    for group in ("OL", "DL"):
        old_names = [e["name"] for e in (old_starters or {}).get(group, [])]
        new_names = [e["name"] for e in new_starters.get(group, [])]
        if old_names != new_names:
            changes.append(f"{group}: {old_names!r} -> {new_names!r}")
    return changes


def populate_roster(
    team: str, index: dict, today: str, session: Optional[requests.Session] = None
) -> dict:
    """Fetch one team's live depth chart from ourlads and update
    config/rosters/{team}.yaml's `starters` block in place. Returns
    {"team", "status": "ok"|"failed", "changes": [...], "error": str|None}.
    On failure, the existing file (if any) is left untouched -- never
    overwritten with a guess."""
    try:
        chart = fetch_ourlads.fetch_depth_chart(team, index=index, session=session)
    except fetch_ourlads.OurladsFetchError as exc:
        return {"team": team, "status": "failed", "changes": [], "error": str(exc)}

    ol_names = fetch_ourlads.starters_for_group(chart, fetch_ourlads.OL_ROW_LABELS)
    dl_names = fetch_ourlads.starters_for_group(chart, fetch_ourlads.DL_ROW_LABELS)
    new_starters = {"OL": [{"name": n} for n in ol_names], "DL": [{"name": n} for n in dl_names]}

    existing = _load_roster_yaml(team) or {}
    changes = _diff_starters(existing.get("starters"), new_starters)

    updated = dict(existing)
    updated["team"] = team
    updated["starters"] = new_starters
    updated["updated_by_human_at"] = today
    updated["roster_source"] = "ourlads.com (live fetch via run_week.py)"
    # prior_season_starters / continuity_note (if present) pass through
    # unchanged -- those stay human-curated once per season.

    ROSTERS_DIR.mkdir(parents=True, exist_ok=True)
    (ROSTERS_DIR / f"{team}.yaml").write_text(yaml.safe_dump(updated, sort_keys=False))

    return {"team": team, "status": "ok", "changes": changes, "error": None}


def populate_all_rosters(
    teams: list[str], today: str, session: Optional[requests.Session] = None
) -> list[dict]:
    try:
        index = fetch_ourlads.fetch_team_index(session=session)
    except fetch_ourlads.OurladsFetchError as exc:
        return [{"team": t, "status": "failed", "changes": [], "error": f"ourlads team index fetch failed: {exc}"} for t in teams]

    results = []
    for i, team in enumerate(teams):
        results.append(populate_roster(team, index, today, session=session))
        if i < len(teams) - 1:
            time.sleep(OURLADS_DELAY_SECONDS)
    return results


def render_all_games(
    matchups: list[dict],
    year: int,
    week: int,
    sp_plus_table: Optional[dict],
    talent_table: Optional[dict],
    top25: set,
) -> tuple[list[dict], list[dict]]:
    """Returns (index_entries, failures). One bad matchup is caught and
    reported -- never aborts rendering the rest of the week."""
    week_dir_name = f"{year}-wk{week:02d}"
    out_dir = OUTPUT_DIR / week_dir_name
    hist_dir = HISTORY_DIR / week_dir_name
    out_dir.mkdir(parents=True, exist_ok=True)

    index_entries = []
    failures = []
    for m in matchups:
        label = m["label"]
        try:
            ctx_a, ctx_b = render_widget.build_both_directions(
                m, year, sp_plus_table=sp_plus_table, talent_table=talent_table
            )
            html = render_widget.render_game(label, m["team_a"], m["team_b"], ctx_a, ctx_b)
            (out_dir / f"{label}.html").write_text(html)
            render_widget.write_game_history_snapshot(label, m["team_a"], m["team_b"], year, ctx_a, ctx_b, hist_dir)
            href = f"{label}.html"
            index_entries.append(render_widget.game_index_entry(label, m["team_a"], m["team_b"], href, top25, ctx_a, ctx_b))
        except Exception as exc:  # noqa: BLE001 -- one bad matchup must not abort the run
            failures.append({"label": label, "error": str(exc)})

    return index_entries, failures


def run_week(
    year: int,
    week: int,
    session: Optional[requests.Session] = None,
    skip_roster: bool = False,
    today: Optional[str] = None,
) -> dict:
    today = today or _dt.date.today().isoformat()

    games = fetch_matchups.fetch_fbs_schedule(year, week, session=session)
    top25 = fetch_matchups.fetch_ap_top25(year, week, session=session)
    discovered = fetch_matchups.derive_matchups(games, top25, year, week)
    hand_curated = load_hand_curated_matchups(year, week)
    matchups = merge_matchups(discovered, hand_curated)
    write_matchups_config(matchups, year, week)

    roster_results: list[dict] = []
    if not skip_roster:
        roster_results = populate_all_rosters(unique_teams(matchups), today, session=session)

    try:
        sp_plus_table = fetch_sp_plus.fetch_fbs_week_table(week, session=session)
    except fetch_sp_plus.SPPlusFetchError:
        sp_plus_table = None  # each matchup falls back individually (see _resolve_sp_plus_gap)

    try:
        talent_table = fetch_talent.fetch_talent_table(year, session=session)
    except Exception:  # noqa: BLE001 -- each matchup's own talent fetch will warn per-team
        talent_table = None

    index_entries, failures = render_all_games(matchups, year, week, sp_plus_table, talent_table, top25)

    week_label = f"{year}, Week {week}"
    index_html = render_widget.render_index(week_label, index_entries)
    week_dir_name = f"{year}-wk{week:02d}"
    (OUTPUT_DIR / week_dir_name / "index.html").write_text(index_html)

    return {
        "year": year,
        "week": week,
        "matchup_count": len(matchups),
        "rendered_count": len(index_entries),
        "failed_count": len(failures),
        "failures": failures,
        "roster_results": roster_results,
        "roster_failures": [r for r in roster_results if r["status"] == "failed"],
        "roster_changes": [r for r in roster_results if r["status"] == "ok" and r["changes"]],
    }


def print_summary(summary: dict) -> None:
    print(f"Trench Edge -- {summary['year']} Week {summary['week']}")
    print(f"  {summary['matchup_count']} matchup(s) discovered/merged")
    print(f"  {summary['rendered_count']} rendered, {summary['failed_count']} failed")
    if summary["failures"]:
        print("  Render failures:")
        for f in summary["failures"]:
            print(f"    - {f['label']}: {f['error']}")
    if summary["roster_failures"]:
        print(f"  {len(summary['roster_failures'])} team(s) need manual roster research (ourlads lookup failed):")
        for r in summary["roster_failures"]:
            print(f"    - {r['team']}: {r['error']}")
    if summary["roster_changes"]:
        print(f"  {len(summary['roster_changes'])} team(s) had a starter change since last run:")
        for r in summary["roster_changes"]:
            for c in r["changes"]:
                print(f"    - {r['team']} {c}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run a full Trench Edge week: discover matchups, populate rosters, render.")
    parser.add_argument("--year", type=int, default=2026)
    parser.add_argument("--week", type=int, required=True)
    parser.add_argument("--skip-roster", action="store_true", help="Skip live ourlads roster population (use existing config/rosters/*.yaml as-is)")
    args = parser.parse_args()

    result = run_week(args.year, args.week, skip_roster=args.skip_roster)
    print_summary(result)
    sys.exit(1 if result["rendered_count"] == 0 and result["matchup_count"] > 0 else 0)
