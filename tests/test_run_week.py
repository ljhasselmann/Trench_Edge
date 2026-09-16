import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import fetch_ourlads
import render_widget
import run_week


SAMPLE_GAMES = [
    {"id": 1, "homeTeam": "Wake Forest", "awayTeam": "Miami"},
    {"id": 2, "homeTeam": "Ohio State", "awayTeam": "Texas"},
    {"id": 3, "homeTeam": "BadTeamB", "awayTeam": "BadTeamA"},
]

SAMPLE_TOP25 = {"Miami", "Texas", "Ohio State", "BadTeamA"}


def _fake_chart(team: str) -> dict:
    return {"LT": [f"{team} LT1"], "RT": [f"{team} RT1"], "DT": [f"{team} DT1"], "NT": [f"{team} NT1"]}


def _fake_fetch_depth_chart(team, index=None, session=None):
    if team == "Ohio State":
        raise fetch_ourlads.OurladsFetchError(f"mock failure for {team}")
    return _fake_chart(team)


def _fake_ctx(label: str, side: str) -> render_widget.WidgetContext:
    return render_widget.WidgetContext(
        matchup_label=label,
        team_a="TeamA",
        team_b="TeamB",
        side=side,
        year=2026,
        generated_at="2026-01-01 00:00 UTC",
        mass={
            "team_a_avg_weight": 300, "team_b_avg_weight": 280, "weight_diff_lbs": 20,
            "score": 1.0, "team_a_starters": [], "team_b_starters": [],
        },
        push={"available": True, "score": 1.0, "sp_plus_gap": 5.0, "raw": {}},
        continuity={
            "team_a_returning": 3, "team_b_returning": 2, "net_returning": 1, "score": 1.0,
            "team_a_driver": None, "team_a_note": None, "team_b_driver": None, "team_b_note": None,
        },
        composite={"value": 1.5, "verdict": "test verdict"},
        warnings=[],
    )


def _fake_build_both_directions(matchup, year, sp_plus_table=None, talent_table=None):
    if "badteama" in matchup["label"]:
        raise RuntimeError(f"mock render failure for {matchup['label']}")
    label = matchup["label"]
    return _fake_ctx(f"{label}-a", "team_a_ol_vs_team_b_dl"), _fake_ctx(f"{label}-b", "team_b_ol_vs_team_a_dl")


def _patch_dirs(monkeypatch, tmp_path):
    monkeypatch.setattr(run_week, "MATCHUPS_DIR", tmp_path / "config" / "matchups")
    monkeypatch.setattr(run_week, "ROSTERS_DIR", tmp_path / "config" / "rosters")
    monkeypatch.setattr(run_week, "TEAMS_FILE", tmp_path / "config" / "teams.yaml")
    monkeypatch.setattr(run_week, "OUTPUT_DIR", tmp_path / "output")
    monkeypatch.setattr(run_week, "HISTORY_DIR", tmp_path / "history")


def _patch_network(monkeypatch):
    monkeypatch.setattr(run_week.time, "sleep", lambda seconds: None)  # skip the real courtesy delay in tests
    monkeypatch.setattr(run_week.fetch_matchups, "fetch_fbs_schedule", lambda year, week, **kw: SAMPLE_GAMES)
    monkeypatch.setattr(run_week.fetch_matchups, "fetch_ap_top25", lambda year, week, **kw: SAMPLE_TOP25)
    monkeypatch.setattr(run_week.fetch_ourlads, "fetch_team_index", lambda **kw: {"dummy": ("dummy", "0")})
    monkeypatch.setattr(run_week.fetch_ourlads, "fetch_depth_chart", _fake_fetch_depth_chart)
    monkeypatch.setattr(run_week.fetch_sp_plus, "fetch_fbs_week_table", lambda week, **kw: {})
    monkeypatch.setattr(run_week.fetch_talent, "fetch_talent_table", lambda year, **kw: {})
    monkeypatch.setattr(run_week.render_widget, "build_both_directions", _fake_build_both_directions)


def test_run_week_discovers_and_writes_matchups_config(monkeypatch, tmp_path):
    _patch_dirs(monkeypatch, tmp_path)
    _patch_network(monkeypatch)

    result = run_week.run_week(2026, 1, today="2026-09-16")

    assert result["matchup_count"] == 3
    matchups_path = tmp_path / "config" / "matchups" / "2026-wk01.yaml"
    assert matchups_path.exists()
    import yaml
    written = yaml.safe_load(matchups_path.read_text())
    labels = {m["label"] for m in written["matchups"]}
    assert labels == {
        "2026-wk01-miami-wake-forest",
        "2026-wk01-texas-ohio-state",
        "2026-wk01-badteama-badteamb",
    }


def test_run_week_one_bad_matchup_does_not_abort_the_rest(monkeypatch, tmp_path):
    _patch_dirs(monkeypatch, tmp_path)
    _patch_network(monkeypatch)

    result = run_week.run_week(2026, 1, today="2026-09-16")

    assert result["rendered_count"] == 2
    assert result["failed_count"] == 1
    assert "badteama" in result["failures"][0]["label"]


def test_run_week_output_and_history_land_in_week_namespaced_paths(monkeypatch, tmp_path):
    _patch_dirs(monkeypatch, tmp_path)
    _patch_network(monkeypatch)

    run_week.run_week(2026, 1, today="2026-09-16")

    out_dir = tmp_path / "output" / "2026-wk01"
    hist_dir = tmp_path / "history" / "2026-wk01"
    assert (out_dir / "2026-wk01-miami-wake-forest.html").exists()
    assert (out_dir / "2026-wk01-texas-ohio-state.html").exists()
    assert not (out_dir / "2026-wk01-badteama-badteamb.html").exists()  # failed, nothing written
    assert (hist_dir / "2026-wk01-miami-wake-forest.json").exists()
    assert (out_dir / "index.html").exists()

    index_html = (out_dir / "index.html").read_text()
    assert "2026-wk01-miami-wake-forest.html" in index_html
    assert "2026-wk01-texas-ohio-state.html" in index_html


def test_run_week_reports_ourlads_failures_without_aborting(monkeypatch, tmp_path):
    _patch_dirs(monkeypatch, tmp_path)
    _patch_network(monkeypatch)

    result = run_week.run_week(2026, 1, today="2026-09-16")

    failed_teams = {r["team"] for r in result["roster_failures"]}
    assert failed_teams == {"Ohio State"}
    # Rendering still succeeded for the Texas/Ohio State game despite the
    # roster fetch failure -- roster population and rendering are decoupled.
    assert result["rendered_count"] == 2


def test_run_week_roster_diff_compares_against_teams_own_prior_config(monkeypatch, tmp_path):
    _patch_dirs(monkeypatch, tmp_path)
    _patch_network(monkeypatch)

    rosters_dir = tmp_path / "config" / "rosters"
    rosters_dir.mkdir(parents=True)
    (rosters_dir / "Miami.yaml").write_text(
        "team: Miami\nupdated_by_human_at: '2026-09-01'\n"
        "starters:\n  OL:\n    - name: 'Old Starter'\n  DL: []\n"
    )

    result = run_week.run_week(2026, 1, today="2026-09-16")

    miami_change = next(r for r in result["roster_changes"] if r["team"] == "Miami")
    assert any("Old Starter" in c and "Miami LT1" in c for c in miami_change["changes"])

    written = (rosters_dir / "Miami.yaml").read_text()
    assert "Miami LT1" in written
    assert "2026-09-16" in written


def test_run_week_skip_roster_leaves_existing_files_untouched(monkeypatch, tmp_path):
    _patch_dirs(monkeypatch, tmp_path)
    _patch_network(monkeypatch)

    rosters_dir = tmp_path / "config" / "rosters"
    rosters_dir.mkdir(parents=True)
    existing_text = "team: Miami\nupdated_by_human_at: '2026-09-01'\nstarters:\n  OL:\n    - name: 'Kept Starter'\n  DL: []\n"
    (rosters_dir / "Miami.yaml").write_text(existing_text)

    result = run_week.run_week(2026, 1, today="2026-09-16", skip_roster=True)

    assert result["roster_results"] == []
    assert (rosters_dir / "Miami.yaml").read_text() == existing_text
