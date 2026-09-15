"""Render the Trench Edge widget (DESIGN.md Section 6).

build_context() is the orchestrator: it calls fetch_cfbd, fetch_roster, and
fetch_talent for one matchup (as configured in config/teams.yaml) and
assembles everything render() needs. render() itself is pure templating
-- no network -- so it's testable without CFBD access.

PUSH comes from `sp_plus_gap` in config/teams.yaml (or the --sp-plus-gap
CLI override), not from a fetch_*.py module. This repo can't compute a
defensible Push number from CFBD's Tier 1 stats alone: naively differencing
team_a.offense.stuffRate against team_b.defense.stuffRate doesn't actually
answer "who wins this matchup" -- both are season-long rates against a full
schedule of different opponents, and validly combining them would need a
league-average baseline to regress each team's effect against (what
SP+-style models do internally), which CFBD doesn't provide. So Push uses
DESIGN.md Section 5's resolved formula instead: overall SP+ differential
(team_a.SP+ - team_b.SP+, from Bill Connelly's weekly SP+ sheet -- see
README.md), divided by 5, capped at +/-10 -- see Section 5 for why the
naive Off/Def-specific combination was rejected (it saturates the cap on
nearly every real matchup; overall SP+ diff stays in the range the /5
divisor was actually calibrated against).

Reading that sheet is a Claude Drive-connector call, not a REST API a
script can hit -- so this module can't fetch it itself the way fetch_cfbd.py
fetches CFBD. Whatever reads the sheet each week (a human, or the Routine's
own session per DESIGN.md Section 7) has to write the resulting gap into
config/teams.yaml before running this script.

If sp_plus_gap is absent, Push (and the composite, which needs all three
components per Section 5) render as explicitly unavailable -- never a
fabricated zero standing in for "unmeasured."
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from jinja2 import Environment, FileSystemLoader

import yaml

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


def build_context(matchup: dict, year: int) -> WidgetContext:
    team_a = matchup["team_a"]
    team_b = matchup["team_b"]
    warnings: list[str] = []

    mass_a = compute_mass_inputs(team_a, year)
    mass_b = compute_mass_inputs(team_b, year)
    warnings += [f"[{team_a} Mass] {w}" for w in mass_a.warnings]
    warnings += [f"[{team_b} Mass] {w}" for w in mass_b.warnings]

    mass_score = None
    weight_diff = None
    if mass_a.avg_ol_weight is not None and mass_b.avg_dl_weight is not None:
        weight_diff = mass_a.avg_ol_weight - mass_b.avg_dl_weight
        mass_score = normalize_mass(weight_diff)

    continuity_a = compute_continuity_inputs(team_a, year)
    continuity_b = compute_continuity_inputs(team_b, year)
    warnings += [f"[{team_a} Continuity] {w}" for w in continuity_a.warnings]
    warnings += [f"[{team_b} Continuity] {w}" for w in continuity_b.warnings]

    continuity_score = None
    net_returning = None
    if continuity_a.returning_ol_starters is not None and continuity_b.returning_dl_starters is not None:
        net_returning = continuity_a.returning_ol_starters - continuity_b.returning_dl_starters
        continuity_score = normalize_continuity(net_returning)

    # Tier 1 fetch is still pulled for the record even though Push doesn't
    # use it (see module docstring) -- these numbers stay useful context.
    try:
        stats_a = fetch_team_trench_stats(team_a, year)
        stats_b = fetch_team_trench_stats(team_b, year)
        push_raw = {
            f"{team_a}_offense_stuff_rate": stats_a.offense.stuff_rate,
            f"{team_a}_offense_line_yards": stats_a.offense.line_yards,
            f"{team_b}_defense_stuff_rate": stats_b.defense.stuff_rate,
            f"{team_b}_defense_line_yards": stats_b.defense.line_yards,
        }
        warnings += [f"[{team_a} Tier1] {w}" for w in stats_a.offense.warnings]
        warnings += [f"[{team_b} Tier1] {w}" for w in stats_b.defense.warnings]
    except Exception as exc:  # noqa: BLE001 -- surface as a warning, never crash the render
        push_raw = {}
        warnings.append(f"Tier 1 fetch failed: {exc}")

    sp_plus_gap = matchup.get("sp_plus_gap")
    push_score = None
    if sp_plus_gap is not None:
        push_score = normalize_push(sp_plus_gap)
    else:
        warnings.append(
            "sp_plus_gap not set in config/teams.yaml for this matchup -- Push "
            "unavailable (see README.md for the SP+ source and how to read it)"
        )

    composite = None
    if mass_score is not None and push_score is not None and continuity_score is not None:
        with open(WEIGHTS_FILE) as f:
            weights = yaml.safe_load(f)
        result = compute_composite(weight_diff, sp_plus_gap, net_returning, weights)
        composite = {"value": result.composite, "verdict": result.verdict}
    else:
        missing = [
            name for name, val in (("Mass", mass_score), ("Push", push_score), ("Continuity", continuity_score))
            if val is None
        ]
        warnings.append(f"Composite not computed -- missing: {', '.join(missing)}")

    return WidgetContext(
        matchup_label=matchup["label"],
        team_a=team_a,
        team_b=team_b,
        side=matchup["side"],
        year=year,
        generated_at=_dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        mass={
            "team_a_avg_weight": mass_a.avg_ol_weight,
            "team_b_avg_weight": mass_b.avg_dl_weight,
            "weight_diff_lbs": weight_diff,
            "score": mass_score,
            "team_a_starters": mass_a.ol_starters,
            "team_b_starters": mass_b.dl_starters,
        },
        push={"available": push_score is not None, "score": push_score, "sp_plus_gap": sp_plus_gap, "raw": push_raw},
        continuity={
            "team_a_returning": continuity_a.returning_ol_starters,
            "team_b_returning": continuity_b.returning_dl_starters,
            "net_returning": net_returning,
            "score": continuity_score,
            "team_a_driver": continuity_a.continuity_driver,
            "team_a_note": continuity_a.continuity_note,
            "team_b_driver": continuity_b.continuity_driver,
            "team_b_note": continuity_b.continuity_note,
        },
        composite=composite,
        warnings=warnings,
    )


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
