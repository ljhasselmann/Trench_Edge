import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import analyze_ats_correlation as analyze


def _direction(composite_value, mass=1.0, push=1.0, experience=1.0, label="g-a"):
    return {
        "matchup_label": label,
        "composite": None if composite_value is None else {"value": composite_value, "verdict": "x"},
        "mass": {"score": mass},
        "push": {"score": push},
        "experience": {"score": experience},
    }


def _game(team_a="Miami", team_b="Wake Forest", composite_a=3.0, composite_b=-1.0, cover_margin_for_team_a=None):
    game = {
        "matchup_label": "g",
        "team_a": team_a,
        "team_b": team_b,
        "direction_a": _direction(composite_a, label="g-a"),
        "direction_b": _direction(composite_b, label="g-b"),
    }
    if cover_margin_for_team_a is not None:
        game["result"] = {"home_points": 0, "away_points": 0, "cover_margin_for_team_a": cover_margin_for_team_a}
    return game


def test_load_all_game_histories_skips_legacy_files(tmp_path):
    (tmp_path / "week").mkdir()
    (tmp_path / "week" / "real.json").write_text(json.dumps(_game()))
    (tmp_path / "week" / "legacy.json").write_text(json.dumps({"team_a": "X", "mass": {}}))

    games = analyze.load_all_game_histories(tmp_path)
    assert len(games) == 1
    assert games[0]["matchup_label"] == "g"


def test_results_with_composite_excludes_games_without_result():
    game = _game(cover_margin_for_team_a=None)  # no result attached at all
    rows = analyze.results_with_composite([game])
    assert rows == []


def test_results_with_composite_excludes_direction_with_null_composite():
    game = _game(composite_a=None, composite_b=-1.0, cover_margin_for_team_a=5.0)
    rows = analyze.results_with_composite([game])
    assert len(rows) == 1
    assert rows[0]["label"] == "g-b"


def test_results_with_composite_reorients_cover_margin_per_direction():
    # game's cover_margin_for_team_a is +10 (team_a beat the spread by 10).
    game = _game(composite_a=3.0, composite_b=-1.0, cover_margin_for_team_a=10.0)
    rows = analyze.results_with_composite([game])
    row_a = next(r for r in rows if r["label"] == "g-a")
    row_b = next(r for r in rows if r["label"] == "g-b")
    assert row_a["cover_margin"] == 10.0   # direction_a's team_a == game's team_a
    assert row_b["cover_margin"] == -10.0  # direction_b swaps sides


def test_sign_agreement_counts_matching_and_mismatched_signs():
    rows = [
        {"composite": 3.0, "cover_margin": 5.0},   # agree (both positive)
        {"composite": -2.0, "cover_margin": -1.0},  # agree (both negative)
        {"composite": 4.0, "cover_margin": -3.0},   # disagree
        {"composite": 0.0, "cover_margin": 2.0},    # zero score -- excluded from agree/disagree
    ]
    result = analyze.sign_agreement(rows, "composite")
    assert result["agree"] == 2
    assert result["disagree"] == 1
    assert result["zero"] == 1
    assert result["total"] == 3
    assert result["pct"] == round(100 * 2 / 3, 1)


def test_sign_agreement_handles_empty_rows():
    result = analyze.sign_agreement([], "composite")
    assert result["total"] == 0
    assert result["pct"] is None


def test_pearson_correlation_perfect_positive():
    xs = [1, 2, 3, 4]
    ys = [2, 4, 6, 8]
    assert round(analyze.pearson_correlation(xs, ys), 4) == 1.0


def test_pearson_correlation_perfect_negative():
    xs = [1, 2, 3, 4]
    ys = [8, 6, 4, 2]
    assert round(analyze.pearson_correlation(xs, ys), 4) == -1.0


def test_pearson_correlation_insufficient_data_returns_none():
    assert analyze.pearson_correlation([1.0], [1.0]) is None
    assert analyze.pearson_correlation([], []) is None


def test_pearson_correlation_zero_variance_returns_none():
    assert analyze.pearson_correlation([1, 1, 1], [1, 2, 3]) is None


def test_run_analysis_reports_sample_size_and_per_field_stats():
    rows = [
        {"composite": 3.0, "mass": 2.0, "push": 1.0, "experience": 0.5, "cover_margin": 4.0},
        {"composite": -2.0, "mass": -1.0, "push": -0.5, "experience": -0.2, "cover_margin": -3.0},
    ]
    analysis = analyze.run_analysis(rows)
    assert analysis["sample_size"] == 2
    assert set(analysis["by_field"].keys()) == {"composite", "mass", "push", "experience"}
    assert analysis["by_field"]["composite"]["sign_agreement"]["agree"] == 2
