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
   season, per _template.yaml. Each starter is also enriched with a real
   247Sports.com recruiting rating/star count (fetch_247sports.py) for
   the Recruiting Talent Differential score -- a degraded-but-recoverable
   fetch (a starter is just staged without a rating if it fails, never
   fatal to roster population). A team puntandrally can't resolve (name
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

import compute_ol_rank
import fetch_247sports
import fetch_cfbd
import fetch_matchups
import fetch_ourlads
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

# REVERTED back to ourlads.com as the authoritative depth-chart source
# (2026-09-16): puntandrally's "top-N-by-snaps" heuristic is a usage
# PROXY for who starts, not an actual depth chart -- confirmed wrong live
# (Miami's Jackson Cantwell is puntandrally's highest-snap "T", but
# Miami's real ourlads chart lists him at LG, not tackle, this week).
# ourlads publishes each team's real, human-maintained depth chart with
# genuine left/right slots (LT/LG/C/RG/RT, LDE/RDE/DT/NT/...) and each
# side's actual scheme name (e.g. "Air Raid" / "4-2-5") -- see
# fetch_ourlads.py. puntandrally is kept as a pure BY-NAME enrichment
# source (jersey, class_year, multi-year snaps, and the Experience
# metric's year-over-year snap-share lookup), matched against ourlads's
# starter names via fetch_puntandrally.resolve_any_name_match since the
# two sites don't always spell a name identically.
#
# No artificial courtesy delay between teams here (unlike the old ourlads
# path's original OURLADS_DELAY_SECONDS): puntandrally enrichment is a
# real browser navigation through Cloudflare's challenge and page
# hydration, which already takes several seconds on its own -- there's no
# plain-HTTP burst to throttle there, and ourlads itself is a plain
# `requests` GET with no rate-limit confirmed live. run_week() opens ONE
# fetch_puntandrally.browser_session() for the whole run and shares it
# across BOTH roster population (enrichment) and rendering (fetch_talent's
# Experience metric needs a live year-1 snap-count lookup per team too)
# -- not a fresh Chromium
# launch per team or per phase.


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
    team: str, year: int, today: str, browser_fetch=None, ourlads_index=None
) -> dict:
    """Fetch one team's real depth chart from ourlads.com and update
    config/rosters/{team}.yaml's `starters` block in place -- ourlads is
    authoritative for WHO starts and at which real left/right slot (see
    module-level comment above for why this reverted from puntandrally's
    usage-derived guess). puntandrally and 247Sports are then consulted
    purely BY NAME to enrich each ourlads-sourced starter with jersey,
    class_year, snaps_multi_year, and a recruiting rating -- neither gets
    a vote on who's actually starting. Each starter entry carries `name`,
    `position_tag` (ourlads' own real slot label, e.g. "LT"/"RG"/"LDE"/
    "NT") plus, where matched, `jersey`, `class_year` (FR/SO/JR/SR/GR),
    `snaps_multi_year`, `recruit_rating`, `recruit_stars`. The team-wide
    `offense_scheme`/`defense_scheme` (ourlads' own labels, e.g. "Air
    Raid"/"4-2-5") are staged at the top level. Returns {"team",
    "status": "ok"|"failed", "changes": [...], "error": str|None,
    "warnings": [...]}. On failure (ourlads itself unreachable/unparsed),
    the existing file is left untouched -- never overwritten with a
    guess. `browser_fetch` should be a fetch_puntandrally.browser_session()
    fetch when populating many teams (see populate_all_rosters), so the
    puntandrally/247Sports enrichment fetches share one browser process.
    `ourlads_index` should be fetch_ourlads.fetch_team_index()'s result,
    fetched once and reused across teams (see populate_all_rosters)."""
    try:
        chart = fetch_ourlads.fetch_depth_chart(team, index=ourlads_index)
    except fetch_ourlads.OurladsFetchError as exc:
        return {"team": team, "status": "failed", "changes": [], "error": str(exc), "warnings": []}

    ol_starters = fetch_ourlads.starters_with_labels(chart, fetch_ourlads.OL_ROW_LABELS, fetch_ourlads.OL_ROW_ORDER)
    dl_starters = fetch_ourlads.starters_with_labels(chart, fetch_ourlads.DL_ROW_LABELS, fetch_ourlads.DL_ROW_ORDER)

    enrichment_warnings: list[str] = []
    try:
        pa_ol_section, pa_dl_section = fetch_puntandrally.fetch_roster(team, year, browser_fetch=browser_fetch)
        pa_players = pa_ol_section.players + pa_dl_section.players
    except fetch_puntandrally.PuntAndRallyFetchError as exc:
        pa_players = []
        enrichment_warnings.append(
            f"puntandrally enrichment fetch failed for {team}, starters staged without jersey/class_year/snaps: {exc}"
        )

    # Multi-year snaps: reuse this year's already-fetched players (no
    # extra live fetch for `year` itself) and only fetch the PRIOR years
    # live, merging into one multi-year total per player -- see
    # fetch_puntandrally.fetch_snap_history's own docstring for why this
    # isn't a true career total. Skipped entirely if the enrichment fetch
    # above already failed -- nothing to merge prior years onto.
    multi_year_snaps: dict = {}
    if pa_players:
        prior_years = fetch_puntandrally.DEFAULT_SNAP_HISTORY_YEARS - 1
        snap_history = fetch_puntandrally.fetch_snap_history(team, year - 1, years=prior_years, browser_fetch=browser_fetch)
        multi_year_snaps = dict(snap_history.totals)
        enrichment_warnings += snap_history.warnings
        for player in pa_players:
            if player.snaps is not None:
                multi_year_snaps[player.name] = multi_year_snaps.get(player.name, 0) + player.snaps

    pa_by_name = {p.name: p for p in pa_players}
    pa_full_names = [p.name for p in pa_players]

    def _match_puntandrally(name: str):
        player = pa_by_name.get(name)
        if player is not None:
            return player
        resolved = fetch_puntandrally.resolve_any_name_match(name, pa_full_names)
        return pa_by_name.get(resolved) if resolved is not None else None

    # Recruiting Talent Differential: a separate site (247Sports), so its
    # failure is degraded-but-recoverable, not fatal to this whole call --
    # ourlads' depth chart (already fetched above) is the authoritative
    # source of who's starting; 247Sports only enriches those
    # already-determined starters with a rating, never decides who starts.
    existing = _load_roster_yaml(team) or {}
    # A whole-team 247Sports failure (site down, timeout) shouldn't erase
    # ratings this file already had from a prior, successful run -- only
    # a real per-player "not found on 247Sports" miss (handled below,
    # site fetch itself succeeded) should ever clear a previously-staged
    # rating, since that's a genuine name-mismatch worth re-flagging.
    _existing_recruit_by_name = {
        entry["name"]: entry
        for entry in existing.get("starters", {}).get("OL", []) + existing.get("starters", {}).get("DL", [])
        if entry.get("recruit_rating") is not None
    }

    talent_warnings: list[str] = []
    try:
        recruiting_by_name = fetch_247sports.fetch_roster(team, year, browser_fetch=browser_fetch)
    except fetch_247sports.TwoFortySevenFetchError as exc:
        recruiting_by_name = {}
        talent_warnings.append(f"247Sports recruiting-rating fetch failed for {team}, starters staged without a rating: {exc}")

    def _starter_entry(label: str, name: str) -> dict:
        entry = {"name": name, "position_tag": label}  # ourlads' own real slot -- see fetch_ourlads.py
        player = _match_puntandrally(name)
        if player is not None:
            if player.jersey is not None:
                entry["jersey"] = player.jersey
            if player.class_year is not None:
                entry["class_year"] = player.class_year
        if name in multi_year_snaps:
            entry["snaps_multi_year"] = multi_year_snaps[name]
        elif player is not None and player.name in multi_year_snaps:
            entry["snaps_multi_year"] = multi_year_snaps[player.name]
        recruit = recruiting_by_name.get(name.lower())
        if recruit is None and recruiting_by_name:
            # ourlads and 247Sports don't always spell a name identically
            # (truncated initial, dropped suffix, stripped accent) -- an
            # exact match then silently fails. See
            # fetch_puntandrally.resolve_any_name_match.
            resolved = fetch_puntandrally.resolve_any_name_match(name, [p.name for p in recruiting_by_name.values()])
            if resolved is not None:
                recruit = recruiting_by_name[resolved.lower()]
        if recruit is not None:
            if recruit.rating is not None:
                entry["recruit_rating"] = recruit.rating
            if recruit.stars is not None:
                entry["recruit_stars"] = recruit.stars
        elif recruiting_by_name:
            # 247Sports fetch succeeded but this exact name wasn't found
            # there -- a real name-spelling mismatch worth flagging, not
            # the same as the whole fetch failing.
            talent_warnings.append(f"{name} ({team}) not found on 247Sports's roster page -- staged without a recruit_rating")
        elif name in _existing_recruit_by_name:
            # The whole 247Sports fetch failed this run (see above) --
            # carry forward the rating this file already had rather than
            # silently erasing it over a transient site/network issue.
            stale = _existing_recruit_by_name[name]
            entry["recruit_rating"] = stale["recruit_rating"]
            if stale.get("recruit_stars") is not None:
                entry["recruit_stars"] = stale["recruit_stars"]
            talent_warnings.append(f"{name} ({team}) kept its previously-staged recruit_rating -- this run's 247Sports fetch failed")
        return entry

    new_starters = {
        "OL": [_starter_entry(label, name) for label, name in ol_starters],
        "DL": [_starter_entry(label, name) for label, name in dl_starters],
    }

    changes = _diff_starters(existing.get("starters"), new_starters)

    updated = dict(existing)
    updated["team"] = team
    updated["starters"] = new_starters
    updated["offense_scheme"] = chart.offense_scheme
    updated["defense_scheme"] = chart.defense_scheme
    updated["updated_by_human_at"] = today
    updated["roster_source"] = "ourlads.com (depth chart) + puntandrally.com (jersey/class/snaps enrichment), via run_week.py"
    # prior_season_starters / continuity_note (if present) pass through
    # unchanged -- those stay human-curated once per season.

    ROSTERS_DIR.mkdir(parents=True, exist_ok=True)
    (ROSTERS_DIR / f"{team}.yaml").write_text(yaml.safe_dump(updated, sort_keys=False))

    return {"team": team, "status": "ok", "changes": changes, "error": None, "warnings": enrichment_warnings + talent_warnings}


def populate_all_rosters(teams: list[str], year: int, today: str, browser_fetch=None) -> list[dict]:
    """`browser_fetch` should be a fetch_puntandrally.browser_session()
    fetch shared across the whole run (roster population AND rendering,
    see run_week()) -- not a fresh Chromium launch per team or per phase.
    ourlads' team index is fetched once here and reused across every
    team, same pattern as the pre-puntandrally implementation."""
    try:
        ourlads_index = fetch_ourlads.fetch_team_index()
    except fetch_ourlads.OurladsFetchError as exc:
        return [{"team": t, "status": "failed", "changes": [], "error": f"ourlads team index fetch failed: {exc}", "warnings": []} for t in teams]
    return [populate_roster(team, year, today, browser_fetch=browser_fetch, ourlads_index=ourlads_index) for team in teams]


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
    browser_fetch=None,
    team_colors: Optional[dict] = None,
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
    all_contexts: list = []
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
                m, year, sp_plus_table=sp_plus_table, talent_table=talent_table, browser_fetch=browser_fetch,
                team_colors=team_colors,
            )
            html = render_widget.render_game(label, m["team_a"], m["team_b"], ctx_a, ctx_b)
            (out_dir / f"{label}.html").write_text(html)
            render_widget.write_game_history_snapshot(label, m["team_a"], m["team_b"], year, ctx_a, ctx_b, hist_dir)
            href = f"{label}.html"
            index_entries.append(render_widget.game_index_entry(label, m["team_a"], m["team_b"], href, top25, ctx_a, ctx_b))
            all_contexts.extend([ctx_a, ctx_b])
        except Exception as exc:  # noqa: BLE001 -- one bad matchup must not abort the run
            failures.append({"label": label, "error": str(exc)})

    if all_contexts:
        render_widget.export_matchup_scores_csv(all_contexts, hist_dir / "matchup_scores.csv", week, year)
        _copy_csv_to_frontend(hist_dir / "matchup_scores.csv")

    return index_entries, failures


def _copy_csv_to_frontend(src: Path) -> None:
    """Copy a CSV to frontend/public/data/ so the Vite dev server can serve it.
    Silent no-op when the frontend directory doesn't exist (e.g. CI, first-time
    setup before `npm install` has been run)."""
    import shutil
    dest_dir = REPO_ROOT / "frontend" / "public" / "data"
    if dest_dir.exists():
        shutil.copy2(src, dest_dir / src.name)


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
    compute_ol_rank_table: bool = False,
) -> dict:
    """only_teams / only_matchups: restrict roster population and
    rendering to matchups touching these teams or matching these labels
    -- a fix-up pass after manual roster research (DESIGN.md Section 7's
    WebSearch fallback) doesn't need to re-populate and re-render all
    ~45 teams / 22 games again, just the handful that changed. Every
    other matchup's index entry is reused from its existing history
    snapshot (see render_all_games). Leave both None (the default) for
    the original full-week behavior, unchanged.

    compute_ol_rank_table: opt-in (default False, unlike everything else
    in this function) because it's genuinely a different order of cost --
    it fetches Mass/Experience/Recruiting/Performance for EVERY FBS team
    (~138), not just the ~40-90 teams touching this week's matchups, so
    turning it on multiplies this run's network/browser cost several
    times over. When True, writes history/{week}/ol_rank_table.csv/.json
    and lineman_stats.csv/.json (see compute_ol_rank.py) -- these are
    standalone data products (a league-wide ranking table and a per-
    lineman player-card export), not currently surfaced in the rendered
    widget itself (the widget's output format is expected to change once
    a front end reads these files directly instead of Jinja-rendered
    HTML)."""
    today = today or _dt.date.today().isoformat()

    games = fetch_matchups.fetch_fbs_schedule(year, week, session=session)
    top25 = fetch_matchups.fetch_ap_top25(year, week, session=session)
    fbs_teams_raw = fetch_cfbd.fetch_fbs_teams(year, session=session)
    fbs_teams = {t["school"] for t in fbs_teams_raw}
    team_colors = fetch_cfbd.team_colors_from_fbs_teams(fbs_teams_raw)
    discovered = fetch_matchups.derive_matchups(games, top25, year, week, fbs_teams=fbs_teams)
    hand_curated = load_hand_curated_matchups(year, week)
    matchups = merge_matchups(discovered, hand_curated)
    write_matchups_config(matchups, year, week)

    active_labels = _active_labels(matchups, only_teams, only_matchups)
    roster_teams = unique_teams(matchups) if active_labels is None else unique_teams(
        [m for m in matchups if m["label"] in active_labels]
    )

    try:
        sp_plus_table = fetch_sp_plus.fetch_fbs_week_table(week, session=session)
    except fetch_sp_plus.SPPlusFetchError:
        sp_plus_table = None  # each matchup falls back individually (see _resolve_sp_plus_gap)

    try:
        talent_table = fetch_talent.fetch_talent_table(year, session=session)
    except Exception:  # noqa: BLE001 -- each matchup's own talent fetch will warn per-team
        talent_table = None

    # One browser process for the whole run -- roster population (this
    # year's starters), rendering (Experience's live year-1 snap-count
    # lookup, see fetch_talent.compute_experience_inputs), AND the
    # league-wide OL Rank pass (same Experience lookup, once per FBS
    # team) all drive puntandrally.com and share this same fetch, rather
    # than each launching its own Chromium instance -- confirmed live
    # this was a real bottleneck when compute_ol_rank_table's league-wide
    # pass didn't share this session.
    with fetch_puntandrally.browser_session() as browser_fetch:
        if compute_ol_rank_table:
            # Deliberately its own fetch pass, not folded into the
            # fetch_team_data() calls below -- this needs EVERY FBS team's
            # attributes (see compute_ol_rank_table's docstring above), not
            # just the teams in this week's matchups. This data product is
            # independent of matchup rendering below (no widget threading).
            with open(CONFIG_DIR / "weights.yaml") as f:
                ol_rank_weights = yaml.safe_load(f)
            attrs_by_team = compute_ol_rank.fetch_league_ol_attributes(year, session=session, browser_fetch=browser_fetch)
            league = list(attrs_by_team.values())
            ol_rank_results = [compute_ol_rank.compute_ol_rank(a, league, ol_rank_weights) for a in league]
            ranked = compute_ol_rank.rank_league(ol_rank_results)

        roster_results: list[dict] = []
        if not skip_roster:
            roster_results = populate_all_rosters(roster_teams, year, today, browser_fetch=browser_fetch)

        index_entries, failures = render_all_games(
            matchups, year, week, sp_plus_table, talent_table, top25,
            active_labels=active_labels, browser_fetch=browser_fetch, team_colors=team_colors,
        )

    week_label = f"{year}, Week {week}"
    index_html = render_widget.render_index(week_label, index_entries)
    week_dir_name = f"{year}-wk{week:02d}"
    (OUTPUT_DIR / week_dir_name / "index.html").write_text(index_html)

    if compute_ol_rank_table:
        hist_dir = HISTORY_DIR / week_dir_name
        compute_ol_rank.write_ol_rank_table_csv(ranked, attrs_by_team, hist_dir / "ol_rank_table.csv")
        compute_ol_rank.write_ol_rank_table_json(ranked, attrs_by_team, hist_dir / "ol_rank_table.json")
        compute_ol_rank.write_lineman_stats_csv(attrs_by_team, hist_dir / "lineman_stats.csv")
        compute_ol_rank.write_lineman_stats_json(attrs_by_team, hist_dir / "lineman_stats.json")
        _copy_csv_to_frontend(hist_dir / "ol_rank_table.csv")
        _copy_csv_to_frontend(hist_dir / "lineman_stats.csv")

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
        "roster_warnings": [r for r in roster_results if r.get("warnings")],
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
    if summary["roster_warnings"]:
        print(f"  {len(summary['roster_warnings'])} team(s) have a partial multi-year snap total (one or more prior seasons failed to fetch):")
        for r in summary["roster_warnings"]:
            for w in r["warnings"]:
                print(f"    - {w}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run a full Trench Edge week: discover matchups, populate rosters, render.")
    parser.add_argument("--year", type=int, default=None, help="Defaults to the current UTC calendar year")
    parser.add_argument("--week", type=int, default=None, help="Defaults to CFBD's current week for --year, via GET /calendar")
    parser.add_argument("--skip-roster", action="store_true", help="Skip live puntandrally roster population (use existing config/rosters/*.yaml as-is)")
    parser.add_argument("--teams", default=None, help="Comma-separated team names -- only populate rosters for and re-render matchups touching these teams; every other matchup reuses its existing history snapshot for the index. For a fix-up pass after manual roster research, not a first run.")
    parser.add_argument("--matchups", default=None, help="Comma-separated matchup labels -- same restriction as --teams, by label instead of team name.")
    parser.add_argument("--with-ol-rank", action="store_true", help="Also compute the league-wide TrenchEdge OL Rank across ALL FBS teams (not just this week's matchups) and surface it as a badge -- multiplies this run's network/browser cost several times over (see compute_ol_rank_table's docstring on run_week()). Requires config/rosters/*.yaml coverage across the league -- see scripts/populate_all_fbs_rosters.py.")
    args = parser.parse_args()

    year = args.year if args.year is not None else _dt.datetime.now(_dt.timezone.utc).year
    week = args.week if args.week is not None else fetch_cfbd.detect_current_week(year)
    if args.week is None:
        print(f"--week not given -- auto-detected week {week} for {year} via CFBD's /calendar")

    only_teams = [t.strip() for t in args.teams.split(",")] if args.teams else None
    only_matchups = [m.strip() for m in args.matchups.split(",")] if args.matchups else None

    result = run_week(
        year, week, skip_roster=args.skip_roster, only_teams=only_teams, only_matchups=only_matchups,
        compute_ol_rank_table=args.with_ol_rank,
    )
    print_summary(result)
    sys.exit(1 if result["rendered_count"] == 0 and result["matchup_count"] > 0 else 0)
