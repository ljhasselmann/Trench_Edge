import csv
import io
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import compare_rank_vs_performance as crp
from fetch_sp_plus import TeamSPPlus


def _make_sp(team, off_sp_plus=10.0):
    return TeamSPPlus(
        team=team, record="6-0", sp_plus=20.0, sp_plus_rank=1,
        off_sp_plus=off_sp_plus, off_sp_plus_rank=1,
        def_sp_plus=10.0, def_sp_plus_rank=1,
    )


def _write_csv(tmp_path, rows: list[dict]) -> Path:
    path = tmp_path / "ol_rank_table.csv"
    fieldnames = ["team", "composite_0_100", "performance_pctile", "mass_pctile", "experience_pctile", "recruiting_pctile"]
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: (v if v is not None else "") for k, v in row.items()})
    return path


# ---------- load_ol_rank_csv ----------

def test_load_ol_rank_csv_casts_floats(tmp_path):
    week_dir = tmp_path / "2026-wk03"
    week_dir.mkdir()
    _write_csv(week_dir, [{"team": "Georgia", "composite_0_100": "90.22", "performance_pctile": "85.0",
                            "mass_pctile": "78.0", "experience_pctile": "92.0", "recruiting_pctile": "95.0"}])
    rows = crp.load_ol_rank_csv(2026, 3, history_dir=tmp_path)
    assert len(rows) == 1
    assert rows[0]["team"] == "Georgia"
    assert rows[0]["composite_0_100"] == pytest.approx(90.22)
    assert rows[0]["performance_pctile"] == pytest.approx(85.0)


def test_load_ol_rank_csv_empty_string_becomes_none(tmp_path):
    week_dir = tmp_path / "2026-wk03"
    week_dir.mkdir()
    _write_csv(week_dir, [{"team": "SMU", "composite_0_100": "55.0", "performance_pctile": "",
                            "mass_pctile": "", "experience_pctile": "60.0", "recruiting_pctile": ""}])
    rows = crp.load_ol_rank_csv(2026, 3, history_dir=tmp_path)
    assert rows[0]["performance_pctile"] is None
    assert rows[0]["mass_pctile"] is None
    assert rows[0]["experience_pctile"] == pytest.approx(60.0)


def test_load_ol_rank_csv_raises_when_file_missing(tmp_path):
    with pytest.raises(FileNotFoundError, match="ol_rank_table.csv not found"):
        crp.load_ol_rank_csv(2026, 3, history_dir=tmp_path)


# ---------- join_with_sp_plus ----------

def test_join_with_sp_plus_resolves_alias(tmp_path):
    # "Miami" (CFBD) → "Miami-FL" (SP+ sheet name)
    ol_rows = [{"team": "Miami", "composite_0_100": 80.0, "performance_pctile": 70.0,
                "mass_pctile": 60.0, "experience_pctile": 50.0, "recruiting_pctile": 90.0}]
    sp_table = {"Miami-FL": _make_sp("Miami-FL", off_sp_plus=28.5)}
    joined = crp.join_with_sp_plus(ol_rows, sp_table)
    assert len(joined) == 1
    assert joined[0]["off_sp_plus"] == pytest.approx(28.5)


def test_join_with_sp_plus_drops_unresolved_teams():
    ol_rows = [{"team": "UnknownU", "composite_0_100": 50.0, "performance_pctile": 40.0,
                "mass_pctile": 30.0, "experience_pctile": 20.0, "recruiting_pctile": 10.0}]
    sp_table = {"Georgia": _make_sp("Georgia")}
    joined = crp.join_with_sp_plus(ol_rows, sp_table)
    assert joined == []


def test_join_with_sp_plus_direct_match_no_alias():
    ol_rows = [{"team": "Georgia", "composite_0_100": 90.0, "performance_pctile": 88.0,
                "mass_pctile": 75.0, "experience_pctile": 85.0, "recruiting_pctile": 99.0}]
    sp_table = {"Georgia": _make_sp("Georgia", off_sp_plus=35.0)}
    joined = crp.join_with_sp_plus(ol_rows, sp_table)
    assert len(joined) == 1
    assert joined[0]["off_sp_plus"] == pytest.approx(35.0)


# ---------- run_analysis ----------

def test_run_analysis_computes_pearson_r_for_each_predictor():
    # Two teams with perfectly correlated composite and SP+: r should be +1 or -1
    joined = [
        {"team": "A", "composite_0_100": 90.0, "performance_pctile": 85.0,
         "mass_pctile": 80.0, "experience_pctile": 70.0, "recruiting_pctile": 95.0, "off_sp_plus": 30.0},
        {"team": "B", "composite_0_100": 50.0, "performance_pctile": 45.0,
         "mass_pctile": 40.0, "experience_pctile": 30.0, "recruiting_pctile": 55.0, "off_sp_plus": 10.0},
    ]
    analysis = crp.run_analysis(joined)
    assert analysis["composite_0_100"]["pearson_r"] == pytest.approx(1.0)
    assert analysis["performance_pctile"]["pearson_r"] == pytest.approx(1.0)
    assert analysis["composite_0_100"]["n"] == 2


def test_run_analysis_excludes_rows_with_null_predictor():
    joined = [
        {"team": "A", "composite_0_100": 90.0, "performance_pctile": None,
         "mass_pctile": None, "experience_pctile": None, "recruiting_pctile": None, "off_sp_plus": 30.0},
        {"team": "B", "composite_0_100": 50.0, "performance_pctile": None,
         "mass_pctile": None, "experience_pctile": None, "recruiting_pctile": None, "off_sp_plus": 10.0},
    ]
    analysis = crp.run_analysis(joined)
    assert analysis["composite_0_100"]["n"] == 2
    assert analysis["performance_pctile"]["n"] == 0
    assert analysis["performance_pctile"]["pearson_r"] is None


def test_run_analysis_sample_size_gate_honored():
    # run_analysis itself does NOT apply the gate -- print_report does.
    # Verify n is accurately reported regardless of size.
    joined = [
        {"team": "A", "composite_0_100": 80.0, "performance_pctile": 75.0,
         "mass_pctile": 70.0, "experience_pctile": 65.0, "recruiting_pctile": 85.0, "off_sp_plus": 25.0},
    ]
    analysis = crp.run_analysis(joined)
    for key, _ in crp.PREDICTORS:
        assert analysis[key]["n"] == 1


# ---------- print_report verdict ----------

def test_print_report_verdict_composite_wins(capsys):
    analysis = {
        "composite_0_100": {"label": "A", "n": 50, "pearson_r": 0.55},
        "performance_pctile": {"label": "B", "n": 50, "pearson_r": 0.40},
        "mass_pctile": {"label": "C", "n": 50, "pearson_r": 0.30},
        "experience_pctile": {"label": "D", "n": 50, "pearson_r": 0.20},
        "recruiting_pctile": {"label": "E", "n": 50, "pearson_r": 0.10},
    }
    crp.print_report(analysis, 2026, 3, 50)
    out = capsys.readouterr().out
    assert "TrenchEdge composite" in out
    assert "outperforms performance" in out


def test_print_report_verdict_performance_wins(capsys):
    analysis = {
        "composite_0_100": {"label": "A", "n": 50, "pearson_r": 0.30},
        "performance_pctile": {"label": "B", "n": 50, "pearson_r": 0.55},
        "mass_pctile": {"label": "C", "n": 50, "pearson_r": 0.20},
        "experience_pctile": {"label": "D", "n": 50, "pearson_r": 0.15},
        "recruiting_pctile": {"label": "E", "n": 50, "pearson_r": 0.10},
    }
    crp.print_report(analysis, 2026, 3, 50)
    out = capsys.readouterr().out
    assert "Performance alone" in out
    assert "outperforms the full composite" in out


def test_print_report_warns_below_min_sample(capsys):
    analysis = {k: {"label": k, "n": 5, "pearson_r": 0.5}
                for k, _ in crp.PREDICTORS}
    crp.print_report(analysis, 2026, 3, 5)
    out = capsys.readouterr().out
    assert "NOT statistically meaningful" in out


def test_print_report_handles_none_pearson(capsys):
    analysis = {
        "composite_0_100": {"label": "A", "n": 0, "pearson_r": None},
        "performance_pctile": {"label": "B", "n": 0, "pearson_r": None},
        "mass_pctile": {"label": "C", "n": 0, "pearson_r": None},
        "experience_pctile": {"label": "D", "n": 0, "pearson_r": None},
        "recruiting_pctile": {"label": "E", "n": 0, "pearson_r": None},
    }
    crp.print_report(analysis, 2026, 3, 0)
    out = capsys.readouterr().out
    assert "insufficient data" in out
