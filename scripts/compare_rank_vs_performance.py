#!/usr/bin/env python3
"""Report whether the full TrenchEdge OL Rank (composite_0_100) predicts
offensive success better than the performance_pctile alone -- i.e., does
adding Mass, Experience, and Recruiting on top of the CFBD run-blocking /
sack-rate / PPA metrics actually help?

Reads the pre-computed `history/{year}-wk{week:02d}/ol_rank_table.csv` for
all 138 FBS teams (no live re-fetch of OL data needed). Fetches offensive
SP+ from the same Google Sheet this repo already reads for Push scores, as
the single proxy for "offensive success" -- it's a standalone per-team,
opponent-adjusted quality metric. Correlates each predictor column against
that outcome and prints a ranked comparison.

Why SP+, not game outcomes?
  The matchup snapshots (history/{year}-wk{week:02d}/*.json) only carry a
  `result` block after `backfill_game_results.py` has been run for a
  completed week. Until then, off_sp_plus (n ≈ 138) is the only available
  outcome variable. An ATS-based comparison follows naturally once games
  are final -- run `backfill_game_results.py`, then `analyze_ats_correlation.py`.
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import fetch_sp_plus
from _stats import pearson_correlation

HISTORY_DIR = REPO_ROOT / "history"
MIN_SAMPLE_FOR_MEANING = 30


def _to_float(val: str) -> Optional[float]:
    """Empty-string-safe CSV float cast."""
    if val is None or val.strip() == "":
        return None
    try:
        return float(val)
    except ValueError:
        return None


def load_ol_rank_csv(year: int, week: int, history_dir: Path = HISTORY_DIR) -> list[dict]:
    """Load the pre-computed ol_rank_table.csv for one week.

    Returns a list of dicts; float fields are cast (None when missing/empty).
    Rows are indexed by their CFBD canonical team name (the `team` column).
    """
    path = history_dir / f"{year}-wk{week:02d}" / "ol_rank_table.csv"
    if not path.exists():
        raise FileNotFoundError(
            f"ol_rank_table.csv not found at {path} -- run `python src/run_week.py "
            f"--year {year} --week {week} --with-ol-rank` first to generate it"
        )
    rows = []
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            rows.append({
                "team": row["team"],
                "composite_0_100": _to_float(row.get("composite_0_100", "")),
                "performance_pctile": _to_float(row.get("performance_pctile", "")),
                "mass_pctile": _to_float(row.get("mass_pctile", "")),
                "experience_pctile": _to_float(row.get("experience_pctile", "")),
                "recruiting_pctile": _to_float(row.get("recruiting_pctile", "")),
            })
    return rows


def join_with_sp_plus(ol_rows: list[dict], sp_plus_table: dict) -> list[dict]:
    """Add `off_sp_plus` to each OL row via team-name alias resolution.

    Teams whose CFBD name doesn't resolve in the SP+ sheet are dropped --
    never guessed at (same posture as analyze_ol_rank_correlation.py).
    """
    joined = []
    for row in ol_rows:
        sheet_name = fetch_sp_plus._to_sheet_name(row["team"])
        sp = sp_plus_table.get(sheet_name)
        if sp is None:
            continue
        joined.append({**row, "off_sp_plus": sp.off_sp_plus})
    return joined


PREDICTORS = [
    ("composite_0_100", "TrenchEdge composite (Mass + Exp + Rec + Perf)"),
    ("performance_pctile", "Performance alone (CFBD run-blocking / sack / PPA)"),
    ("mass_pctile", "Mass alone (avg OL weight)"),
    ("experience_pctile", "Experience alone (returning snap %)"),
    ("recruiting_pctile", "Recruiting alone (247Sports avg rating)"),
]


def run_analysis(joined: list[dict]) -> dict:
    """Pearson r for each predictor vs. off_sp_plus.

    Each predictor is computed on only the rows where BOTH the predictor
    and off_sp_plus are non-null -- partial data degrades that predictor's
    sample, not the whole run.
    """
    results = {}
    for key, label in PREDICTORS:
        pairs = [(r[key], r["off_sp_plus"]) for r in joined if r[key] is not None and r["off_sp_plus"] is not None]
        xs = [p[0] for p in pairs]
        ys = [p[1] for p in pairs]
        results[key] = {
            "label": label,
            "n": len(pairs),
            "pearson_r": pearson_correlation(xs, ys),
        }
    return results


def print_report(analysis: dict, year: int, week: int, total_joined: int) -> None:
    print(f"\nTrenchEdge composite vs. performance-alone -- {year} Week {week}")
    print(f"  {total_joined} team(s) matched between ol_rank_table.csv and the SP+ sheet")
    if total_joined < MIN_SAMPLE_FOR_MEANING:
        print(f"  ** Sample size is below {MIN_SAMPLE_FOR_MEANING} -- NOT statistically meaningful yet. **")
        print("  ** Reporting the numbers below honestly anyway, per DESIGN.md Section 8. **")
    print()
    print(f"  {'Predictor':<52} {'n':>4}  {'Pearson r vs. off_sp_plus':>26}")
    print(f"  {'-'*52}  {'-'*4}  {'-'*26}")

    sorted_items = sorted(
        analysis.items(),
        key=lambda kv: kv[1]["pearson_r"] if kv[1]["pearson_r"] is not None else -999,
        reverse=True,
    )
    for key, stats in sorted_items:
        r = stats["pearson_r"]
        r_text = f"{r:+.3f}" if r is not None else "n/a"
        print(f"  {stats['label']:<52} {stats['n']:>4}  {r_text:>26}")

    composite_r = analysis["composite_0_100"]["pearson_r"]
    perf_r = analysis["performance_pctile"]["pearson_r"]

    print()
    if composite_r is None or perf_r is None:
        print("  Verdict: insufficient data for both predictors -- cannot compare.")
    elif composite_r > perf_r:
        delta = composite_r - perf_r
        print(f"  Verdict: TrenchEdge composite (r={composite_r:+.3f}) outperforms performance "
              f"alone (r={perf_r:+.3f}) by {delta:.3f} on offensive SP+ prediction.")
    elif perf_r > composite_r:
        delta = perf_r - composite_r
        print(f"  Verdict: Performance alone (r={perf_r:+.3f}) outperforms the full composite "
              f"(r={composite_r:+.3f}) by {delta:.3f} -- adding Mass/Experience/Recruiting "
              f"hurts predictive power for this outcome.")
    else:
        print(f"  Verdict: Performance alone and the full composite are tied (r={composite_r:+.3f}).")
    print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Compare TrenchEdge composite vs. performance alone as predictors of offensive SP+."
    )
    parser.add_argument("--year", type=int, required=True)
    parser.add_argument("--week", type=int, required=True, help="Week number for the SP+ sheet's 'FBS Week N' tab")
    parser.add_argument("--history-dir", default=str(HISTORY_DIR))
    args = parser.parse_args()

    ol_rows = load_ol_rank_csv(args.year, args.week, Path(args.history_dir))
    sp_plus_table = fetch_sp_plus.fetch_fbs_week_table(args.week)
    joined = join_with_sp_plus(ol_rows, sp_plus_table)
    analysis = run_analysis(joined)
    print_report(analysis, args.year, args.week, len(joined))
