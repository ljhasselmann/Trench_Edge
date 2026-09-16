#!/usr/bin/env python3
"""Report whether the composite (or any subscore) actually correlates
with beating the closing line -- DESIGN.md Section 8's backtesting plan.
Reports only; never tunes weights. Requires scripts/backfill_game_results.py
to have already attached a `result` block (with `cover_margin_for_team_a`)
to the history/*.json snapshots being analyzed -- a snapshot with no
`result`, or a `result` with no ATS fields (no line was posted, or the
lines fetch failed that backfill run), is skipped, not guessed at.

Each direction's `composite`/`mass.score`/`push.score`/`experience.score`
is one row, correlated against `cover_margin_for_team_a` reoriented to
that direction's own team_a (the OL team for that direction) -- see
`_cover_margin_for_direction`. Sample size is reported prominently: this
repo has real history for only a handful of weeks so far, and DESIGN.md
Section 8 is explicit that an honest small-sample non-result is the
correct thing to report, not something to explain away.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
HISTORY_DIR = REPO_ROOT / "history"

MIN_SAMPLE_FOR_MEANING = 30


def load_all_game_histories(history_root: Path = HISTORY_DIR) -> list:
    games = []
    for path in sorted(history_root.glob("**/*.json")):
        data = json.loads(path.read_text())
        if "direction_a" not in data:
            continue  # legacy pre-four-corners snapshot
        games.append(data)
    return games


def _cover_margin_for_direction(game: dict, direction_key: str) -> "float | None":
    """Reorients the game-level cover_margin_for_team_a (relative to the
    GAME's own team_a=away) to this DIRECTION's team_a (the OL team for
    that direction, which is team_b when direction_b swaps sides)."""
    result = game.get("result")
    if result is None or "cover_margin_for_team_a" not in result:
        return None
    game_margin = result["cover_margin_for_team_a"]
    direction = game[direction_key]
    # direction_a's team_a == game's team_a (same side); direction_b swaps.
    return game_margin if direction_key == "direction_a" else -game_margin


def results_with_composite(games: list) -> list:
    """Flattens to one row per direction with both a non-null composite
    AND a usable ATS result -- the only rows a correlation can use."""
    rows = []
    for game in games:
        for direction_key in ("direction_a", "direction_b"):
            direction = game[direction_key]
            composite = direction.get("composite")
            cover_margin = _cover_margin_for_direction(game, direction_key)
            if composite is None or cover_margin is None:
                continue
            rows.append({
                "label": direction.get("matchup_label", game.get("matchup_label")),
                "composite": composite["value"],
                "mass": direction["mass"]["score"],
                "push": direction["push"]["score"],
                "experience": direction["experience"]["score"],
                "cover_margin": cover_margin,
            })
    return rows


def sign_agreement(rows: list, field: str) -> dict:
    agree = disagree = zero = 0
    for row in rows:
        score, margin = row[field], row["cover_margin"]
        if score == 0 or margin == 0:
            zero += 1
        elif (score > 0) == (margin > 0):
            agree += 1
        else:
            disagree += 1
    total = agree + disagree  # zero-score/zero-margin rows aren't a directional agreement or disagreement
    return {
        "agree": agree, "disagree": disagree, "zero": zero, "total": total,
        "pct": round(100 * agree / total, 1) if total else None,
    }


def pearson_correlation(xs: list, ys: list) -> "float | None":
    n = len(xs)
    if n < 2:
        return None
    mean_x, mean_y = sum(xs) / n, sum(ys) / n
    cov = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    var_x = sum((x - mean_x) ** 2 for x in xs)
    var_y = sum((y - mean_y) ** 2 for y in ys)
    if var_x == 0 or var_y == 0:
        return None
    return cov / (var_x ** 0.5 * var_y ** 0.5)


def run_analysis(rows: list) -> dict:
    fields = ("composite", "mass", "push", "experience")
    return {
        "sample_size": len(rows),
        "by_field": {
            field: {
                "sign_agreement": sign_agreement(rows, field),
                "pearson_r": pearson_correlation([r[field] for r in rows], [r["cover_margin"] for r in rows]),
            }
            for field in fields
        },
    }


def print_report(analysis: dict) -> None:
    n = analysis["sample_size"]
    print(f"ATS correlation report -- {n} direction(s) with both a composite and a real ATS result")
    if n < MIN_SAMPLE_FOR_MEANING:
        print(f"  ** Sample size is below {MIN_SAMPLE_FOR_MEANING} -- NOT statistically meaningful yet. **")
        print("  ** Reporting the numbers below honestly anyway, per DESIGN.md Section 8. **")
    print()
    for field, stats in analysis["by_field"].items():
        sa = stats["sign_agreement"]
        r = stats["pearson_r"]
        r_text = f"{r:+.3f}" if r is not None else "n/a"
        pct_text = f"{sa['pct']}%" if sa["pct"] is not None else "n/a"
        print(f"  {field:<11} sign agreement: {sa['agree']}/{sa['total']} ({pct_text})  pearson r: {r_text}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Report ATS correlation for the composite and its subscores.")
    parser.add_argument("--history-dir", default=str(HISTORY_DIR))
    args = parser.parse_args()

    games = load_all_game_histories(Path(args.history_dir))
    rows = results_with_composite(games)
    analysis = run_analysis(rows)
    print_report(analysis)
