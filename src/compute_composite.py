"""Scoring model (DESIGN.md Section 5).

Trench Edge = w_mass * Mass + w_push * Push + w_experience * Experience

Each subscore is normalized to -10 (favors Team B) .. +10 (favors Team A)
before weighting. This module is pure computation -- no network calls --
so it's fully testable offline, unlike fetch_cfbd.py.
"""

from __future__ import annotations

from dataclasses import dataclass


def _clamp(value: float, low: float = -10.0, high: float = 10.0) -> float:
    return max(low, min(high, value))


def normalize_mass(weight_diff_lbs: float) -> float:
    """1 point per 10 lbs of average weight differential, capped at +/-10."""
    return _clamp(weight_diff_lbs / 10.0)


def normalize_push(sp_plus_gap: float) -> float:
    """Placeholder: 1 point per 5 SP+ points of off-vs-def gap.
    To be replaced with real Stuff Rate / Line Yards differential once
    Tier 1 wiring (fetch_cfbd.py) is validated against live data."""
    return _clamp(sp_plus_gap / 5.0)


def normalize_experience(experience_diff_pct: float) -> float:
    """1 point per 10 percentage-points of returning-snap-share
    differential, capped at +/-10 -- a starting hunch, same spirit as
    Mass's "10 lbs/point" and Push's "5 SP+ points/point" above.
    `experience_diff_pct` is `ol_side.returning_ol_snap_pct -
    dl_side.returning_dl_snap_pct` (see fetch_talent.ExperienceInputs) --
    the average share of THIS season's starters' snaps, at their own team,
    that were played by the same players last season."""
    return _clamp(experience_diff_pct / 10.0)


VERDICT_BANDS = [
    (2.0, "negligible edge"),
    (5.0, "slight-to-moderate edge"),
    (8.0, "significant edge"),
    (10.0, "dominant edge"),
]


def verdict_for(composite: float) -> str:
    magnitude = abs(composite)
    for threshold, label in VERDICT_BANDS:
        if magnitude <= threshold:
            return label
    return VERDICT_BANDS[-1][1]


@dataclass
class CompositeResult:
    mass: float
    push: float
    experience: float
    composite: float
    verdict: str


def compute_composite(
    weight_diff_lbs: float,
    sp_plus_gap: float,
    experience_diff_pct: float,
    weights: dict,
) -> CompositeResult:
    mass = normalize_mass(weight_diff_lbs)
    push = normalize_push(sp_plus_gap)
    experience = normalize_experience(experience_diff_pct)

    composite = (
        weights["mass"] * mass
        + weights["push"] * push
        + weights["experience"] * experience
    )
    composite = _clamp(composite)

    return CompositeResult(
        mass=mass,
        push=push,
        experience=experience,
        composite=composite,
        verdict=verdict_for(composite),
    )


if __name__ == "__main__":
    import argparse
    import yaml

    parser = argparse.ArgumentParser(description="Compute a Trench Edge composite score.")
    parser.add_argument("--weight-diff-lbs", type=float, required=True)
    parser.add_argument("--sp-plus-gap", type=float, required=True)
    parser.add_argument("--experience-diff-pct", type=float, required=True)
    parser.add_argument("--weights-file", default="config/weights.yaml")
    args = parser.parse_args()

    with open(args.weights_file) as f:
        weights = yaml.safe_load(f)

    result = compute_composite(
        args.weight_diff_lbs, args.sp_plus_gap, args.experience_diff_pct, weights
    )
    print(result)
