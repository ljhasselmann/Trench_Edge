"""Weekly orchestrator (DESIGN.md Section 7, build plan step 9).

A deterministic script, not a new agentic mode -- ties together every
module built for the "scale to all Top-25-involving games, with four
corners" plan:

1. Discover this week's Top-25-involving matchups (fetch_matchups.py),
   merged with any hand-curated entries in config/teams.yaml for the same
   week (a human's pinned matchup wins on a label collision -- they set
   fields like sp_plus_gap deliberately).
2. Populate every matchup team's config/rosters/{team}.yaml `starters`
   block live from puntandrally.com (fetch_puntandrally.py), deterministic-
   first. puntandrally replaced ourlads.com as of this integration --
   confirmed live to resolve all 138 CFBD FBS teams with zero aliases
   needed, and it additionally carries real snap counts ourlads never had
   (see fetch_puntandrally.py's docstring). `prior_season_starters` /
   `continuity_note` are untouched -- those stay human-curated once per
   season, per _template.yaml. A team puntandrally can't resolve (name
   miss, page-structure change) is reported, not guessed at -- this plain
   script has no WebSearch/WebFetch access itself; a human or the wrapping
   Routine session does that fallback research for exactly the teams this
   run reports as failed, per DESIGN.md Section 7.
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
from pathlib import Path
from typing import Optional

import requests
import yaml

import fetch_cfbd
import fetch_matchups
import fetch_puntandrally
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

# Live-fetched starters now come from puntandrally.com (fetch_puntandrally.py),
# not ourlads.com -- puntandrally is a strict superset (starters + real
# snap counts; ourlads only ever had starters), and its team-name index
# resolves all 138 CFBD FBS teams with zero aliases needed (confirmed live,
# scripts/check_team_name_coverage.py). fetch_ourlads.py itself is left in
# the repo unremoved, per that module's own docstring, but is no longer
# called from this orchestrator.
#
# No artificial courtesy delay between teams here (unlike the old ourlads
# path's OURLADS_DELAY_SECONDS): each puntandrally fetch is a real browser
# navigation through Cloudflare's challenge and page hydration, which
# already takes several seconds on its own -- there's no plain-HTTP burst
# to throttle. populate_all_rosters reuses ONE browser process for the
# whole week's teams (fetch_puntandrally.browser_session()) rather than
# launching Chromium per team, which is the actual cost that mattered.
DL_STARTER_COUNT = 4


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


def _dl_starters(players: list, count: int = DL_STARTER_COUNT) -> list[str]:
    """Top `count` DL players by snap count, regardless of tag.
    puntandrally's DL tags (DE/DT/DL) are too coarse to split by slot the
    way fetch_puntandrally.OL_STARTER_COUNTS does for OL -- front size
    genuinely varies by scheme (a 4-3's 4 down linemen vs. a 3-4's 3), and
    that module's own docstring deliberately declines to guess a fixed
    split. Taking a flat top-N by usage is this orchestrator's own
    simplification, not fetch_puntandrally's -- a real per-team front-size
    read is follow-up work, not something to fake here."""
    ranked = sorted(players, key=lambda p: p.snaps if p.snaps is not None else -1, reverse=True)
    return [p.name for p in ranked[:count]]


def populate_roster(
    team: str, today: str, browser_fetch=None
) -> dict:
    """Fetch one team's live roster + snap counts from puntandrally and
    update config/rosters/{team}.yaml's `starters` block in place. Returns
    {"team", "status": "ok"|"failed", "changes": [...], "error": str|None}.
    On failure, the existing file (if any) is left untouched -- never
    overwritten with a guess. `browser_fetch` should be a
    fetch_puntandrally.browser_session() fetch when populating many teams
    (see populate_all_rosters), so they share one browser process."""
    try:
        ol_section, dl_section = fetch_puntandrally.fetch_roster(team, browser_fetch=browser_fetch)
    except fetch_puntandrally.PuntAndRallyFetchError as exc:
        return {"team": team, "status": "failed", "changes": [], "error": str(exc)}

    ol_names = fetch_puntandrally.starters_for_group(ol_section.players, fetch_puntandrally.OL_STARTER_COUNTS)
    dl_names = _dl_starters(dl_section.players)
    new_starters = {"OL": [{"name": n} for n in ol_names], "DL": [{"name": n} for n in dl_names]}

    existing = _load_roster_yaml(team) or {}
    changes = _diff_starters(existing.get("starters"), new_starters)

    updated = dict(existing)
    updated["team"] = team
    updated["starters"] = new_starters
    updated["updated_by_human_at"] = today
    updated["roster_source"] = "puntandrally.com (live fetch via run_week.py)"
    # prior_season_starters / continuity_note (if present) pass through
    # unchanged -- those stay human-curated once per season.

    ROSTERS_DIR.mkdir(parents=True, exist_ok=True)
    (ROSTERS_DIR / f"{team}.yaml").write_text(yaml.safe_dump(updated, sort_keys=False))

    return {"team": team, "status": "ok", "changes": changes, "error": None}


def populate_all_rosters(teams: list[str], today: str) -> list[dict]:
    """One shared browser process for the whole batch (see
    fetch_puntandrally.browser_session()) -- not a fresh Chromium launch
    per team."""
    results = []
    with fetch_puntandrally.browser_session() as fetch:
        for team in teams:
            results.append(populate_roster(team, today, browser_fetch=fetch))
    return results


def _reuse_from_history(label: str, hist_dir: Path, top25: set) -> Optional[dict]:
    """Load an already-written history/{label}.json for a matchup this run
    is skipping (outside --teams/--matchups) and rebuild its index entry
    from it -- zero network calls. Returns None if no prior snapshot
    exists (nothing to reuse), so the caller falls back to a fresh render
    rather than silently dropping the game from the index."""
    path = hist_dir / f"{label}.json"
    if not path.exists():
        return None
    import json

    history = json.loads(path.read_text())
    href = f"{label}.html"
    return render_widget.game_index_entry_from_history(history, href, top25)


def render_all_games(
    matchups: list[dict],
    year: int,
    week: int,
    sp_plus_table: Optional[dict],
    talent_table: Optional[dict],
    top25: set,
    active_labels: Optional[set] = None,
) -> tuple[list[dict], list[dict]]:
    """Returns (index_entries, failures). One bad matchup is caught and
    reported -- never aborts rendering the rest of the week.

    active_labels: when given, only matchups whose label is in this set
    are actually re-rendered (fresh CFBD/puntandrally-derived data); every
    other matchup reuses its existing history/{label}.json snapshot for
    the index instead of being re-fetched -- see _reuse_from_history().
    A matchup with no prior snapshot is always rendered fresh regardless
    of active_labels, since there's nothing to reuse. None (the default)
    renders every matchup fresh, unchanged from the original behavior."""
    week_dir_name = f"{year}-wk{week:02d}"
    out_dir = OUTPUT_DIR / week_dir_name
    hist_dir = HISTORY_DIR / week_dir_name
    out_dir.mkdir(parents=True, exist_ok=True)

    index_entries = []
    failures = []
    for m in matchups:
        label = m["label"]

        if active_labels is not None and label not in active_labels:
            reused = _reuse_from_history(label, hist_dir, top25)
            if reused is not None:
                index_entries.append(reused)
                continue
            # no prior snapshot to reuse -- fall through and render fresh

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


def _active_labels(matchups: list[dict], only_teams: Optional[list[str]], only_matchups: Optional[list[str]]) -> Optional[set]:
    """Which matchup labels a --teams/--matchups filter selects. None
    means "no filter, everything is active" -- callers must treat that
    as full-week behavior, not an empty set (empty would mean nothing
    renders, which is never what an unset filter should mean)."""
    if not only_teams and not only_matchups:
        return None
    team_set = set(only_teams or [])
    label_set = set(only_matchups or [])
    return {
        m["label"] for m in matchups
        if m["label"] in label_set or m["team_a"] in team_set or m["team_b"] in team_set
    }


def run_week(
    year: int,
    week: int,
    session: Optional[requests.Session] = None,
    skip_roster: bool = False,
    today: Optional[str] = None,
    only_teams: Optional[list[str]] = None,
    only_matchups: Optional[list[str]] = None,
) -> dict:
    """only_teams / only_matchups: restrict roster population and
    rendering to matchups touching these teams or matching these labels
    -- a fix-up pass after manual roster research (DESIGN.md Section 7's
    WebSearch fallback) doesn't need to re-populate and re-render all
    ~45 teams / 22 games again, just the handful that changed. Every
    other matchup's index entry is reused from its existing history
    snapshot (see render_all_games). Leave both None (the default) for
    the original full-week behavior, unchanged."""
    today = today or _dt.date.today().isoformat()

    games = fetch_matchups.fetch_fbs_schedule(year, week, session=session)
    top25 = fetch_matchups.fetch_ap_top25(year, week, session=session)
    discovered = fetch_matchups.derive_matchups(games, top25, year, week)
    hand_curated = load_hand_curated_matchups(year, week)
    matchups = merge_matchups(discovered, hand_curated)
    write_matchups_config(matchups, year, week)

    active_labels = _active_labels(matchups, only_teams, only_matchups)
    roster_teams = unique_teams(matchups) if active_labels is None else unique_teams(
        [m for m in matchups if m["label"] in active_labels]
    )

    roster_results: list[dict] = []
    if not skip_roster:
        roster_results = populate_all_rosters(roster_teams, today)

    try:
        sp_plus_table = fetch_sp_plus.fetch_fbs_week_table(week, session=session)
    except fetch_sp_plus.SPPlusFetchError:
        sp_plus_table = None  # each matchup falls back individually (see _resolve_sp_plus_gap)

    try:
        talent_table = fetch_talent.fetch_talent_table(year, session=session)
    except Exception:  # noqa: BLE001 -- each matchup's own talent fetch will warn per-team
        talent_table = None

    index_entries, failures = render_all_games(matchups, year, week, sp_plus_table, talent_table, top25, active_labels=active_labels)

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
        print(f"  {len(summary['roster_failures'])} team(s) need manual roster research (puntandrally lookup failed):")
        for r in summary["roster_failures"]:
            print(f"    - {r['team']}: {r['error']}")
    if summary["roster_changes"]:
        print(f"  {len(summary['roster_changes'])} team(s) had a starter change since last run:")
        for r in summary["roster_changes"]:
            for c in r["changes"]:
                print(f"    - {r['team']} {c}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run a full Trench Edge week: discover matchups, populate rosters, render.")
    parser.add_argument("--year", type=int, default=None, help="Defaults to the current UTC calendar year")
    parser.add_argument("--week", type=int, default=None, help="Defaults to CFBD's current week for --year, via GET /calendar")
    parser.add_argument("--skip-roster", action="store_true", help="Skip live puntandrally roster population (use existing config/rosters/*.yaml as-is)")
    parser.add_argument("--teams", default=None, help="Comma-separated team names -- only populate rosters for and re-render matchups touching these teams; every other matchup reuses its existing history snapshot for the index. For a fix-up pass after manual roster research, not a first run.")
    parser.add_argument("--matchups", default=None, help="Comma-separated matchup labels -- same restriction as --teams, by label instead of team name.")
    args = parser.parse_args()

    year = args.year if args.year is not None else _dt.datetime.now(_dt.timezone.utc).year
    week = args.week if args.week is not None else fetch_cfbd.detect_current_week(year)
    if args.week is None:
        print(f"--week not given -- auto-detected week {week} for {year} via CFBD's /calendar")

    only_teams = [t.strip() for t in args.teams.split(",")] if args.teams else None
    only_matchups = [m.strip() for m in args.matchups.split(",")] if args.matchups else None

    result = run_week(year, week, skip_roster=args.skip_roster, only_teams=only_teams, only_matchups=only_matchups)
    print_summary(result)
    sys.exit(1 if result["rendered_count"] == 0 and result["matchup_count"] > 0 else 0)
