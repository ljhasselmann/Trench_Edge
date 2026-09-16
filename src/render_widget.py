"""Render the Trench Edge widget (DESIGN.md Section 6).

Split into a fetch layer and a combine layer, specifically so four-corners
scoring (both OL-vs-DL directions for one game) doesn't double the
network cost. `fetch_team_data(team, year, ...)` fetches everything Mass/
Continuity/Tier1 need for one team -- and it already fetches BOTH sides
(OL and DL weight, offense and defense stats) for that team, since
compute_mass_inputs/compute_continuity_inputs/fetch_team_trench_stats all
do that regardless of which "side" of a matchup the team plays. So
scoring both directions of a game only means calling fetch_team_data once
per team (twice total) and combining that same data twice, not fetching
twice as much.

`combine_context(...)` is the pure, no-network layer that decides which
team is playing OL and which is playing DL for a given `side`, and builds
one `WidgetContext` from two already-fetched `TeamData`s. `build_context()`
(single direction, matches this module's original contract -- existing
CLI and tests keep working unchanged) and `build_both_directions()` (both
directions, one game) are both thin wrappers over fetch + combine.

`render()` itself is pure templating -- no network -- so it's testable
without CFBD access.

PUSH comes from fetch_sp_plus.py when `week` is set on the matchup in
config/teams.yaml -- a live fetch of Bill Connelly's weekly SP+ sheet via
docs.google.com's gviz endpoint (no Drive connector needed; that was an
earlier, now-obsolete limitation -- see fetch_sp_plus.py's docstring for
why the sheet needed a specific endpoint and tab-naming discipline to
fetch reliably as plain REST). This repo can't compute a defensible Push
number from CFBD's Tier 1 stats alone: naively differencing
team_a.offense.stuffRate against team_b.defense.stuffRate doesn't actually
answer "who wins this matchup" -- both are season-long rates against a full
schedule of different opponents, and validly combining them would need a
league-average baseline to regress each team's effect against (what
SP+-style models do internally), which CFBD doesn't provide. So Push uses
DESIGN.md Section 5's resolved formula instead: overall SP+ differential
(team_a.SP+ - team_b.SP+), divided by 5, capped at +/-10 -- see Section 5
for why the naive Off/Def-specific combination was rejected (it saturates
the cap on nearly every real matchup; overall SP+ diff stays in the range
the /5 divisor was actually calibrated against).

If the live fetch fails (network hiccup, that week's tab not published
yet), this falls back to matchup["sp_plus_gap"] in config/teams.yaml if
present, with a warning either way about which source was actually used.

If no gap is available at all, Push (and the composite, which needs all
three components per Section 5) render as explicitly unavailable -- never a
fabricated zero standing in for "unmeasured."
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from jinja2 import Environment, FileSystemLoader

import yaml

import requests

import fetch_sp_plus
from fetch_cfbd import fetch_team_trench_stats
from fetch_roster import compute_mass_inputs
from fetch_talent import compute_continuity_inputs
from compute_composite import compute_composite, normalize_mass, normalize_continuity, normalize_push

WEIGHTS_FILE = Path(__file__).resolve().parents[1] / "config" / "weights.yaml"

TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "templates"


@dataclass
class WidgetContext:
    matchup_label: str
    team_a: str
    team_b: str
    side: str
    year: int
    generated_at: str
    mass: dict
    push: dict
    continuity: dict
    composite: Optional[dict]
    warnings: list[str] = field(default_factory=list)


@dataclass
class TeamData:
    """Everything Mass/Continuity/Tier1 need for one team -- both sides
    (OL and DL weight, offense and defense stats), since the underlying
    fetches (compute_mass_inputs, compute_continuity_inputs,
    fetch_team_trench_stats) all compute both regardless of which side of
    a matchup this team plays. Fetching this once per team (not once per
    matchup direction) is what makes four-corners scoring not double the
    network cost -- see module docstring."""

    team: str
    mass: object  # fetch_roster.MassInputs
    continuity: object  # fetch_talent.TalentInputs
    tier1: Optional[object]  # fetch_cfbd.TeamAdvancedStats, None if the fetch failed
    warnings: list[str] = field(default_factory=list)


def fetch_team_data(
    team: str, year: int, talent_table: Optional[dict] = None, session: Optional[requests.Session] = None
) -> TeamData:
    warnings: list[str] = []

    mass = compute_mass_inputs(team, year, session=session)
    warnings += [f"[{team} Mass] {w}" for w in mass.warnings]

    continuity = compute_continuity_inputs(team, year, talent_table=talent_table, session=session)
    warnings += [f"[{team} Continuity] {w}" for w in continuity.warnings]

    try:
        tier1 = fetch_team_trench_stats(team, year, session=session)
        warnings += [f"[{team} Tier1 offense] {w}" for w in tier1.offense.warnings]
        warnings += [f"[{team} Tier1 defense] {w}" for w in tier1.defense.warnings]
    except Exception as exc:  # noqa: BLE001 -- surface as a warning, never crash the render
        tier1 = None
        warnings.append(f"[{team} Tier1] fetch failed: {exc}")

    return TeamData(team=team, mass=mass, continuity=continuity, tier1=tier1, warnings=warnings)


def _resolve_sp_plus_gap(
    matchup: dict, team_a: str, team_b: str, sp_plus_table: Optional[dict] = None, session: Optional[requests.Session] = None
) -> tuple[Optional[float], list[str]]:
    """The forward-direction gap (team_a.SP+ - team_b.SP+). combine_context
    negates it itself for the reverse direction -- SP+ diff is symmetric
    by construction, so this only ever needs computing once per matchup."""
    warnings: list[str] = []
    week = matchup.get("week")
    if week is not None:
        try:
            gap = fetch_sp_plus.compute_sp_plus_gap(team_a, team_b, week, table=sp_plus_table, session=session)
        except Exception as exc:  # noqa: BLE001 -- fall back to config, never crash the render
            fallback = matchup.get("sp_plus_gap")
            warnings.append(f"Live SP+ fetch failed ({exc}); using config/teams.yaml's stored sp_plus_gap={fallback!r} instead")
            gap = fallback
    else:
        gap = matchup.get("sp_plus_gap")
        if gap is not None:
            warnings.append("No 'week' set for this matchup -- used config/teams.yaml's static sp_plus_gap instead of a live SP+ fetch")
    return gap, warnings


def combine_context(
    matchup_label: str,
    team_a: str,
    team_b: str,
    side: str,
    year: int,
    team_a_data: TeamData,
    team_b_data: TeamData,
    sp_plus_gap: Optional[float],
    weights: dict,
) -> WidgetContext:
    """Pure, no-network: decides which team is playing OL and which is
    playing DL for `side`, and builds one WidgetContext from two already-
    fetched TeamData. WidgetContext.team_a/team_b mean "the OL team"/"the
    DL team" for THIS direction (matching widget.html.jinja's "{{ ctx.team_a }}
    OL vs {{ ctx.team_b }} DL" heading) -- not necessarily the matchup's
    own team_a/team_b, which is why the reverse direction swaps them."""
    warnings = list(team_a_data.warnings) + list(team_b_data.warnings)

    if side == "team_a_ol_vs_team_b_dl":
        ol_data, dl_data = team_a_data, team_b_data
        gap = sp_plus_gap
    elif side == "team_b_ol_vs_team_a_dl":
        ol_data, dl_data = team_b_data, team_a_data
        gap = -sp_plus_gap if sp_plus_gap is not None else None
    else:
        raise ValueError(f"unknown side {side!r}")
    ol_label, dl_label = ol_data.team, dl_data.team

    mass_score = None
    weight_diff = None
    if ol_data.mass.avg_ol_weight is not None and dl_data.mass.avg_dl_weight is not None:
        weight_diff = ol_data.mass.avg_ol_weight - dl_data.mass.avg_dl_weight
        mass_score = normalize_mass(weight_diff)

    continuity_score = None
    net_returning = None
    if ol_data.continuity.returning_ol_starters is not None and dl_data.continuity.returning_dl_starters is not None:
        net_returning = ol_data.continuity.returning_ol_starters - dl_data.continuity.returning_dl_starters
        continuity_score = normalize_continuity(net_returning)

    push_score = normalize_push(gap) if gap is not None else None
    if push_score is None:
        warnings.append(
            "sp_plus_gap unavailable (no 'week' set for a live fetch and no fallback "
            "value in config/teams.yaml) -- Push unavailable (see README.md's SP+ source)"
        )

    push_raw = {}
    if ol_data.tier1 is not None:
        push_raw[f"{ol_label}_offense_stuff_rate"] = ol_data.tier1.offense.stuff_rate
        push_raw[f"{ol_label}_offense_line_yards"] = ol_data.tier1.offense.line_yards
    if dl_data.tier1 is not None:
        push_raw[f"{dl_label}_defense_stuff_rate"] = dl_data.tier1.defense.stuff_rate
        push_raw[f"{dl_label}_defense_line_yards"] = dl_data.tier1.defense.line_yards

    composite = None
    if mass_score is not None and push_score is not None and continuity_score is not None:
        result = compute_composite(weight_diff, gap, net_returning, weights)
        composite = {"value": result.composite, "verdict": result.verdict}
    else:
        missing = [
            name for name, val in (("Mass", mass_score), ("Push", push_score), ("Continuity", continuity_score))
            if val is None
        ]
        warnings.append(f"Composite not computed -- missing: {', '.join(missing)}")

    return WidgetContext(
        matchup_label=matchup_label,
        team_a=ol_label,
        team_b=dl_label,
        side=side,
        year=year,
        generated_at=_dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        mass={
            "team_a_avg_weight": ol_data.mass.avg_ol_weight,
            "team_b_avg_weight": dl_data.mass.avg_dl_weight,
            "weight_diff_lbs": weight_diff,
            "score": mass_score,
            "team_a_starters": ol_data.mass.ol_starters,
            "team_b_starters": dl_data.mass.dl_starters,
        },
        push={"available": push_score is not None, "score": push_score, "sp_plus_gap": gap, "raw": push_raw},
        continuity={
            "team_a_returning": ol_data.continuity.returning_ol_starters,
            "team_b_returning": dl_data.continuity.returning_dl_starters,
            "net_returning": net_returning,
            "score": continuity_score,
            "team_a_driver": ol_data.continuity.continuity_driver,
            "team_a_note": ol_data.continuity.continuity_note,
            "team_b_driver": dl_data.continuity.continuity_driver,
            "team_b_note": dl_data.continuity.continuity_note,
        },
        composite=composite,
        warnings=warnings,
    )


def build_context(
    matchup: dict, year: int, sp_plus_table: Optional[dict] = None, talent_table: Optional[dict] = None
) -> WidgetContext:
    """Single direction -- this module's original contract, unchanged for
    the existing CLI and any caller that only wants one side scored."""
    team_a = matchup["team_a"]
    team_b = matchup["team_b"]
    side = matchup.get("side", "team_a_ol_vs_team_b_dl")

    team_a_data = fetch_team_data(team_a, year, talent_table=talent_table)
    team_b_data = fetch_team_data(team_b, year, talent_table=talent_table)
    sp_plus_gap, sp_warnings = _resolve_sp_plus_gap(matchup, team_a, team_b, sp_plus_table=sp_plus_table)

    with open(WEIGHTS_FILE) as f:
        weights = yaml.safe_load(f)

    ctx = combine_context(matchup["label"], team_a, team_b, side, year, team_a_data, team_b_data, sp_plus_gap, weights)
    ctx.warnings = sp_warnings + ctx.warnings
    return ctx


def build_both_directions(
    matchup: dict, year: int, sp_plus_table: Optional[dict] = None, talent_table: Optional[dict] = None
) -> tuple[WidgetContext, WidgetContext]:
    """Four corners: both OL-vs-DL directions for one game. Fetches each
    team's data exactly once (not once per direction) -- see module
    docstring."""
    team_a = matchup["team_a"]
    team_b = matchup["team_b"]
    label = matchup["label"]

    team_a_data = fetch_team_data(team_a, year, talent_table=talent_table)
    team_b_data = fetch_team_data(team_b, year, talent_table=talent_table)
    sp_plus_gap, sp_warnings = _resolve_sp_plus_gap(matchup, team_a, team_b, sp_plus_table=sp_plus_table)

    with open(WEIGHTS_FILE) as f:
        weights = yaml.safe_load(f)

    ctx_a = combine_context(f"{label}-a", team_a, team_b, "team_a_ol_vs_team_b_dl", year, team_a_data, team_b_data, sp_plus_gap, weights)
    ctx_a.warnings = sp_warnings + ctx_a.warnings

    ctx_b = combine_context(f"{label}-b", team_a, team_b, "team_b_ol_vs_team_a_dl", year, team_a_data, team_b_data, sp_plus_gap, weights)
    ctx_b.warnings = sp_warnings + ctx_b.warnings

    return ctx_a, ctx_b


def render(context: WidgetContext) -> str:
    env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)), autoescape=True)
    template = env.get_template("widget.html.jinja")
    return template.render(ctx=context)


def _starters_to_dicts(starters: list) -> list:
    return [vars(s) for s in starters]


def context_to_history_dict(context: WidgetContext) -> dict:
    """DESIGN.md Section 3/7: one JSON snapshot per matchup per week in
    history/. This is the only reliable record of "who did we say was
    starting last time" -- diffing against it (not re-deriving it ad hoc)
    is what makes week-over-week starter-change detection trustworthy."""
    return {
        "matchup_label": context.matchup_label,
        "team_a": context.team_a,
        "team_b": context.team_b,
        "side": context.side,
        "year": context.year,
        "generated_at": context.generated_at,
        "mass": {
            **{k: v for k, v in context.mass.items() if k not in ("team_a_starters", "team_b_starters")},
            "team_a_starters": _starters_to_dicts(context.mass["team_a_starters"]),
            "team_b_starters": _starters_to_dicts(context.mass["team_b_starters"]),
        },
        "push": context.push,
        "continuity": context.continuity,
        "composite": context.composite,
        "warnings": context.warnings,
    }


def write_history_snapshot(context: WidgetContext, history_dir: Path) -> Path:
    import json

    history_dir.mkdir(parents=True, exist_ok=True)
    path = history_dir / f"{context.matchup_label}.json"
    path.write_text(json.dumps(context_to_history_dict(context), indent=2))
    return path


def render_game(game_label: str, team_a: str, team_b: str, ctx_a: WidgetContext, ctx_b: WidgetContext) -> str:
    """One page per game with both directions (DESIGN.md Section 8's
    "four corners" -- a game isn't fully scored with just one direction).
    widget.html.jinja itself stays a single-direction partial, unchanged;
    game.html.jinja wraps two renders of it."""
    env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)), autoescape=True)
    template = env.get_template("game.html.jinja")
    return template.render(game_label=game_label, team_a=team_a, team_b=team_b, ctx_a=ctx_a, ctx_b=ctx_b)


def game_to_history_dict(game_label: str, team_a: str, team_b: str, year: int, ctx_a: WidgetContext, ctx_b: WidgetContext) -> dict:
    """One history file per game, both directions nested -- not two
    separate files. team_a/team_b here are the game's own identity
    (e.g. away/home), distinct from ctx_a/ctx_b's team_a/team_b, which
    mean "the OL team"/"the DL team" for that specific direction."""
    return {
        "matchup_label": game_label,
        "team_a": team_a,
        "team_b": team_b,
        "year": year,
        "generated_at": ctx_a.generated_at,
        "direction_a": context_to_history_dict(ctx_a),
        "direction_b": context_to_history_dict(ctx_b),
    }


def write_game_history_snapshot(
    game_label: str, team_a: str, team_b: str, year: int, ctx_a: WidgetContext, ctx_b: WidgetContext, history_dir: Path
) -> Path:
    import json

    history_dir.mkdir(parents=True, exist_ok=True)
    path = history_dir / f"{game_label}.json"
    path.write_text(json.dumps(game_to_history_dict(game_label, team_a, team_b, year, ctx_a, ctx_b), indent=2))
    return path


def game_index_entry(
    game_label: str, team_a: str, team_b: str, href: str, top25_teams: set, ctx_a: WidgetContext, ctx_b: WidgetContext
) -> dict:
    """One row's worth of data for render_index() -- the caller (run_week.py)
    builds a list of these across a week's games rather than hand-assembling
    the template context itself."""
    return {
        "label": game_label,
        "team_a": team_a,
        "team_b": team_b,
        "href": href,
        "top25_teams": top25_teams,
        "direction_a": ctx_a.composite,
        "direction_b": ctx_b.composite,
        "warning_count": len(ctx_a.warnings) + len(ctx_b.warnings),
    }


def render_index(week_label: str, games: list[dict]) -> str:
    """games: a list of game_index_entry() dicts."""
    env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)), autoescape=True)
    template = env.get_template("index.html.jinja")
    return template.render(week_label=week_label, games=games)


if __name__ == "__main__":
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="Render the Trench Edge widget for one configured matchup.")
    parser.add_argument("matchup_label", help="Must match a 'label' in config/teams.yaml")
    parser.add_argument("--teams-file", default="config/teams.yaml")
    parser.add_argument("--out", default="output/latest.html")
    parser.add_argument("--history-dir", default="history")
    parser.add_argument("--sp-plus-gap", type=float, default=None, help="Override config/teams.yaml's sp_plus_gap for this run")
    args = parser.parse_args()

    with open(args.teams_file) as f:
        teams_config = yaml.safe_load(f)

    matchup = next((m for m in teams_config["matchups"] if m["label"] == args.matchup_label), None)
    if matchup is None:
        print(f"No matchup labeled {args.matchup_label!r} in {args.teams_file}", file=sys.stderr)
        sys.exit(1)
    if args.sp_plus_gap is not None:
        matchup = {**matchup, "sp_plus_gap": args.sp_plus_gap}

    ctx = build_context(matchup, teams_config["year"])
    html = render(ctx)
    Path(args.out).write_text(html)
    snapshot_path = write_history_snapshot(ctx, Path(args.history_dir))
    print(f"wrote {args.out}")
    print(f"wrote {snapshot_path}")
    if ctx.warnings:
        print(f"{len(ctx.warnings)} warning(s):", file=sys.stderr)
        for w in ctx.warnings:
            print(f"  - {w}", file=sys.stderr)
