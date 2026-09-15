"""Render the Trench Edge widget (DESIGN.md Section 6).

build_context() is the orchestrator: it calls fetch_cfbd, fetch_roster, and
fetch_talent for one matchup (as configured in config/teams.yaml) and
assembles everything render() needs. render() itself is pure templating
-- no network -- so it's testable without CFBD access.

PUSH IS NOT WIRED TO REAL NUMBERS. Section 5 says Push should eventually
use real Stuff Rate / Line Yards differentials instead of the SP+
placeholder, now that Tier 1 fetching is live. But naively differencing
team_a.offense.stuffRate against team_b.defense.stuffRate doesn't actually
answer "who wins this matchup" -- both are season-long rates against a full
schedule of opponents, and validly combining them needs a league-average
baseline to regress each team's effect against (the thing SP+-style models
actually do). This repo has no such baseline. Inventing a formula that
*looks* quantitative without one would violate DESIGN.md Section 2's own
non-goal: "not claiming precision beyond what the inputs support."

So: Push renders as explicitly unavailable, and the composite is NOT
computed until Push has a real input (either wired to a defensible
Stuff-Rate/Line-Yards formula, once one exists, or supplied externally as
an SP+ gap from the broader workflow DESIGN.md Section 1 references).
Showing nothing beats showing a wrong number labeled as final.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from jinja2 import Environment, FileSystemLoader

from fetch_cfbd import fetch_team_trench_stats
from fetch_roster import compute_mass_inputs
from fetch_talent import compute_continuity_inputs
from compute_composite import normalize_mass, normalize_continuity

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

    # Tier 1 fetch runs regardless, so its numbers are visible even though
    # Push isn't scored yet -- see module docstring.
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

    composite = None
    if mass_score is not None and continuity_score is not None:
        warnings.append(
            "Composite not computed -- Push has no validated real-data formula yet "
            "(see render_widget.py module docstring); DESIGN.md Section 5's formula "
            "needs all three components."
        )

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
        push={"available": False, "raw": push_raw},
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


if __name__ == "__main__":
    import argparse
    import sys

    import yaml

    parser = argparse.ArgumentParser(description="Render the Trench Edge widget for one configured matchup.")
    parser.add_argument("matchup_label", help="Must match a 'label' in config/teams.yaml")
    parser.add_argument("--teams-file", default="config/teams.yaml")
    parser.add_argument("--out", default="output/latest.html")
    args = parser.parse_args()

    with open(args.teams_file) as f:
        teams_config = yaml.safe_load(f)

    matchup = next((m for m in teams_config["matchups"] if m["label"] == args.matchup_label), None)
    if matchup is None:
        print(f"No matchup labeled {args.matchup_label!r} in {args.teams_file}", file=sys.stderr)
        sys.exit(1)

    ctx = build_context(matchup, teams_config["year"])
    html = render(ctx)
    Path(args.out).write_text(html)
    print(f"wrote {args.out}")
    if ctx.warnings:
        print(f"{len(ctx.warnings)} warning(s):", file=sys.stderr)
        for w in ctx.warnings:
            print(f"  - {w}", file=sys.stderr)
