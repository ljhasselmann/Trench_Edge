import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from compute_composite import (
    compute_composite,
    normalize_experience,
    normalize_mass,
    normalize_push,
    verdict_for,
)

WEIGHTS = {"mass": 0.4, "push": 0.4, "experience": 0.2}


def test_normalize_mass_scales_and_caps():
    assert normalize_mass(0) == 0
    assert normalize_mass(30) == 3.0
    assert normalize_mass(500) == 10.0
    assert normalize_mass(-500) == -10.0


def test_normalize_push_scales_and_caps():
    assert normalize_push(10) == 2.0
    assert normalize_push(100) == 10.0


def test_normalize_experience_scales_and_caps():
    assert normalize_experience(0) == 0
    assert normalize_experience(20) == 2.0
    assert normalize_experience(200) == 10.0
    assert normalize_experience(-200) == -10.0


def test_verdict_bands_match_design_doc_thresholds():
    assert verdict_for(0) == "negligible edge"
    assert verdict_for(2.0) == "negligible edge"
    assert verdict_for(2.1) == "slight-to-moderate edge"
    assert verdict_for(5.0) == "slight-to-moderate edge"
    assert verdict_for(5.1) == "significant edge"
    assert verdict_for(8.0) == "significant edge"
    assert verdict_for(8.1) == "dominant edge"
    assert verdict_for(10.0) == "dominant edge"
    # symmetric for Team B favored
    assert verdict_for(-9.0) == "dominant edge"


def test_compute_composite_worked_example_miami_favored():
    # Miami OL averages ~25 lbs heavier, +10 SP+ push gap, +20 percentage
    # points of returning-snap-share differential.
    result = compute_composite(
        weight_diff_lbs=25, sp_plus_gap=10, experience_diff_pct=20, weights=WEIGHTS
    )
    assert result.mass == 2.5
    assert result.push == 2.0
    assert result.experience == 2.0
    # 0.4*2.5 + 0.4*2.0 + 0.2*2.0 = 1.0 + 0.8 + 0.4 = 2.2
    assert round(result.composite, 4) == 2.2
    assert result.verdict == "slight-to-moderate edge"


def test_compute_composite_clamps_extreme_inputs():
    result = compute_composite(
        weight_diff_lbs=1000, sp_plus_gap=1000, experience_diff_pct=1000, weights=WEIGHTS
    )
    assert result.mass == 10.0
    assert result.push == 10.0
    assert result.experience == 10.0
    assert result.composite == 10.0
    assert result.verdict == "dominant edge"


def test_compute_composite_weights_sum_need_not_be_enforced_here():
    # compute_composite trusts the caller's weights file; it doesn't
    # validate that they sum to 1. Document that behavior explicitly.
    skewed = {"mass": 1.0, "push": 1.0, "experience": 1.0}
    result = compute_composite(10, 10, 100, skewed)
    assert result.composite == 10.0  # clamped, not 13.0
