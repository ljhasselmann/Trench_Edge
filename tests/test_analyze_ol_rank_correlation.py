import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import analyze_ol_rank_correlation as analyze
from compute_ol_rank import OLRankResult


def _ranked(team, composite):
    return OLRankResult(team, mass_pctile=50, experience_pctile=50, recruiting_pctile=50, performance_pctile=50, composite_0_100=composite)


def test_rows_with_rank_and_output_skips_unranked_and_unmatched_teams():
    ranked = [
        _ranked("Miami", 80.0),
        _ranked("Wake Forest", None),  # unranked -- compute_ol_rank couldn't score it
        _ranked("Troy", 40.0),  # not in the SP+ table this week
    ]
    off_sp_plus_by_team = {"Miami": 25.0, "Buffalo": 10.0}

    rows = analyze.rows_with_rank_and_output(ranked, off_sp_plus_by_team)

    assert len(rows) == 1
    assert rows[0]["team"] == "Miami"
    assert rows[0]["ol_rank_composite"] == 80.0
    assert rows[0]["off_sp_plus"] == 25.0


def test_run_analysis_reports_sample_size_and_pearson_r():
    rows = [
        {"team": "A", "ol_rank_composite": 10, "off_sp_plus": 1},
        {"team": "B", "ol_rank_composite": 20, "off_sp_plus": 2},
        {"team": "C", "ol_rank_composite": 30, "off_sp_plus": 3},
    ]
    analysis = analyze.run_analysis(rows)
    assert analysis["sample_size"] == 3
    assert round(analysis["pearson_r"], 4) == 1.0


def test_run_analysis_handles_empty_rows():
    analysis = analyze.run_analysis([])
    assert analysis["sample_size"] == 0
    assert analysis["pearson_r"] is None


def test_print_report_flags_small_sample(capsys):
    analyze.print_report({"sample_size": 5, "pearson_r": 0.42})
    out = capsys.readouterr().out
    assert "NOT statistically meaningful yet" in out
    assert "+0.420" in out
