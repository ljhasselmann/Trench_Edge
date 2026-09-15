"""Scoring model (DESIGN.md Section 5).

Trench Edge = w_mass * Mass + w_push * Push + w_continuity * Continuity

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


def normalize_continuity(net_returning_starters: int) -> float:
    """1 point per net returning-starter differential."""
    return _clamp(float(net_returning_starters))


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
    continuity: float
    composite: float
    verdict: str


def compute_composite(
    weight_diff_lbs: float,
    sp_plus_gap: float,
    net_returning_starters: int,
    weights: dict,
) -> CompositeResult:
    mass = normalize_mass(weight_diff_lbs)
    push = normalize_push(sp_plus_gap)
    continuity = normalize_continuity(net_returning_starters)

    composite = (
        weights["mass"] * mass
        + weights["push"] * push
        + weights["continuity"] * continuity
    )
    composite = _clamp(composite)

    return CompositeResult(
        mass=mass,
        push=push,
        continuity=continuity,
        composite=composite,
        verdict=verdict_for(composite),
    )


if __name__ == "__main__":
    import argparse
    import yaml

    parser = argparse.ArgumentParser(description="Compute a Trench Edge composite score.")
    parser.add_argument("--weight-diff-lbs", type=float, required=True)
    parser.add_argument("--sp-plus-gap", type=float, required=True)
    parser.add_argument("--net-returning-starters", type=int, required=True)
    parser.add_argument("--weights-file", default="config/weights.yaml")
    args = parser.parse_args()

    with open(args.weights_file) as f:
        weights = yaml.safe_load(f)

    result = compute_composite(
        args.weight_diff_lbs, args.sp_plus_gap, args.net_returning_starters, weights
    )
    print(result)
