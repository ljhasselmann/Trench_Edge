#!/usr/bin/env python3
"""Report whether the TrenchEdge OL Rank (compute_ol_rank.py -- an
absolute, league-wide OL score, not a matchup differential) actually
predicts a team's own real offensive output this season. Reports only;
never tunes weights -- same posture as analyze_ats_correlation.py, just at
the team-attribute level instead of the matchup level.

Confirmed outcome variable (per this session's decision): CFBD's
offensive SP+ rating (`TeamSPPlus.off_sp_plus`), a standalone per-team
value, not a differential -- fetched league-wide in one call via
fetch_sp_plus.fetch_fbs_week_table(). One row per FBS team with both an
OL Rank composite and an off_sp_plus value; a team missing either
(unranked -- see compute_ol_rank.rank_league -- or absent from that
week's SP+ sheet) is skipped, not guessed at.

Sample size is reported prominently, same MIN_SAMPLE_FOR_MEANING=30
threshold as the ATS script: this repo's real roster coverage is still
building out (see scripts/populate_all_fbs_rosters.py), so an honest
small-sample non-result is the correct thing to print here too.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import yaml

from _stats import pearson_correlation

import compute_ol_rank
import fetch_sp_plus

REPO_ROOT = Path(__file__).resolve().parents[1]
WEIGHTS_FILE = REPO_ROOT / "config" / "weights.yaml"

MIN_SAMPLE_FOR_MEANING = 30


def rows_with_rank_and_output(
    ranked: list, off_sp_plus_by_team: dict
) -> list:
    rows = []
    for r in ranked:
        if r.composite_0_100 is None:
            continue
        off_sp_plus = off_sp_plus_by_team.get(r.team)
        if off_sp_plus is None:
            continue
        rows.append({"team": r.team, "ol_rank_composite": r.composite_0_100, "off_sp_plus": off_sp_plus})
    return rows


def run_analysis(rows: list) -> dict:
    xs = [r["ol_rank_composite"] for r in rows]
    ys = [r["off_sp_plus"] for r in rows]
    return {
        "sample_size": len(rows),
        "pearson_r": pearson_correlation(xs, ys),
    }


def print_report(analysis: dict) -> None:
    n = analysis["sample_size"]
    print(f"OL Rank vs. offensive-output correlation report -- {n} team(s) with both a ranked OL composite and an off_sp_plus value")
    if n < MIN_SAMPLE_FOR_MEANING:
        print(f"  ** Sample size is below {MIN_SAMPLE_FOR_MEANING} -- NOT statistically meaningful yet. **")
        print("  ** Reporting the number below honestly anyway, per DESIGN.md Section 8. **")
    r = analysis["pearson_r"]
    r_text = f"{r:+.3f}" if r is not None else "n/a"
    print(f"  pearson r (OL Rank composite vs. offensive SP+): {r_text}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Report OL Rank vs. offensive-output correlation.")
    parser.add_argument("--year", type=int, default=2026)
    parser.add_argument("--week", type=int, required=True, help="Week number for the SP+ sheet's 'FBS Week N' tab")
    parser.add_argument("--weights-file", default=str(WEIGHTS_FILE))
    args = parser.parse_args()

    with open(args.weights_file) as f:
        weights = yaml.safe_load(f)

    attrs_by_team = compute_ol_rank.fetch_league_ol_attributes(args.year)
    league = list(attrs_by_team.values())
    results = [compute_ol_rank.compute_ol_rank(a, league, weights) for a in league]
    ranked = compute_ol_rank.rank_league(results)

    sp_plus_table = fetch_sp_plus.fetch_fbs_week_table(args.week)
    off_sp_plus_by_team = {team: row.off_sp_plus for team, row in sp_plus_table.items()}

    rows = rows_with_rank_and_output(ranked, off_sp_plus_by_team)
    analysis = run_analysis(rows)
    print_report(analysis)
