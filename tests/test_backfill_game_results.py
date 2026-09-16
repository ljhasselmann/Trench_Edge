import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import fetch_sp_plus
import backfill_game_results as backfill


def _sample_snapshot(team_a="Miami", team_b="Wake Forest"):
    return {
        "matchup_label": "2026-wk03-miami-wake-forest",
        "team_a": team_a,
        "team_b": team_b,
        "year": 2026,
        "generated_at": "2026-09-16 00:00 UTC",
        "direction_a": {"composite": {"value": 3.28, "verdict": "slight-to-moderate edge"}},
        "direction_b": {"composite": {"value": -1.36, "verdict": "negligible edge"}},
    }


def _write_snapshot(week_dir, label, data):
    week_dir.mkdir(parents=True, exist_ok=True)
    path = week_dir / f"{label}.json"
    path.write_text(json.dumps(data))
    return path


class _FakeMatchupsSession:
    """Stands in for the session fetch_matchups.fetch_game_results uses."""
    def __init__(self, games):
        self._games = games

    def get(self, url, params, headers, timeout):
        class _R:
            status_code = 200
            def json(inner_self):
                return self._games if url.endswith("/games") else []
        return _R()


def test_compute_result_block_with_real_ats_line():
    game_result = {"home_points": 14, "away_points": 34}
    line = fetch_sp_plus.GameLine(
        away_team="Miami-FL", home_team="Wake Forest", favorite="Miami-FL", spread=22.5,
        ats_pick="Wake Forest", proj_margin=19.7, over_under=55.5, ou_pick="Under",
    )
    result = backfill.compute_result_block("Miami", "Wake Forest", game_result, line)

    assert result["margin_for_team_a"] == 20  # 34 - 14
    assert result["favorite"] == "Miami"
    assert result["expected_margin_for_team_a"] == 22.5
    assert result["cover_margin_for_team_a"] == -2.5  # won by 20, expected to win by 22.5 -- didn't cover
    assert result["ats_pick"] == "Wake Forest"


def test_compute_result_block_underdog_favorite_reorients_correctly():
    # Team B (home) is the favorite this time.
    game_result = {"home_points": 30, "away_points": 10}
    line = fetch_sp_plus.GameLine(
        away_team="Miami-FL", home_team="Wake Forest", favorite="Wake Forest", spread=6.0,
        ats_pick="Miami-FL", proj_margin=None, over_under=None, ou_pick=None,
    )
    result = backfill.compute_result_block("Miami", "Wake Forest", game_result, line)

    assert result["margin_for_team_a"] == -20  # away (team_a) lost by 20
    assert result["favorite"] == "Wake Forest"
    assert result["expected_margin_for_team_a"] == -6.0
    assert result["cover_margin_for_team_a"] == -14.0  # lost by 20, expected to lose by only 6


def test_compute_result_block_no_line_omits_ats_fields():
    game_result = {"home_points": 14, "away_points": 34}
    result = backfill.compute_result_block("Miami", "Some FCS Team", game_result, None)
    assert result["margin_for_team_a"] == 20
    assert "cover_margin_for_team_a" not in result
    assert "spread" not in result


def test_backfill_week_writes_result_and_reports_pending(monkeypatch, tmp_path):
    week_dir = tmp_path / "2026-wk03"
    _write_snapshot(week_dir, "2026-wk03-miami-wake-forest", _sample_snapshot())
    _write_snapshot(week_dir, "2026-wk03-texas-ohio-state", _sample_snapshot("Ohio State", "Texas"))

    games = [
        {"homeTeam": "Wake Forest", "awayTeam": "Miami", "completed": True, "homePoints": 14, "awayPoints": 34},
        {"homeTeam": "Texas", "awayTeam": "Ohio State", "completed": False, "homePoints": None, "awayPoints": None},
    ]
    monkeypatch.setenv("CFBD_API_KEY", "k")
    monkeypatch.setattr(backfill.fetch_matchups, "fetch_fbs_schedule", lambda year, week, **kw: games)
    monkeypatch.setattr(backfill.fetch_sp_plus, "fetch_week_lines", lambda week, **kw: [
        fetch_sp_plus.GameLine(away_team="Miami-FL", home_team="Wake Forest", favorite="Miami-FL", spread=22.5,
                                ats_pick="Wake Forest", proj_margin=19.7, over_under=55.5, ou_pick="Under"),
    ])

    summary = backfill.backfill_week(2026, 3, history_dir=tmp_path)

    assert summary["updated"] == ["2026-wk03-miami-wake-forest.json"]
    assert summary["pending"] == ["2026-wk03-texas-ohio-state.json"]
    assert summary["already_up_to_date"] == []

    written = json.loads((week_dir / "2026-wk03-miami-wake-forest.json").read_text())
    assert written["result"]["cover_margin_for_team_a"] == -2.5


def test_backfill_week_is_idempotent_without_force(monkeypatch, tmp_path):
    week_dir = tmp_path / "2026-wk03"
    snapshot = _sample_snapshot()
    snapshot["result"] = {"home_points": 14, "away_points": 34}
    _write_snapshot(week_dir, "2026-wk03-miami-wake-forest", snapshot)

    monkeypatch.setenv("CFBD_API_KEY", "k")
    fetch_calls = []
    monkeypatch.setattr(backfill.fetch_matchups, "fetch_fbs_schedule", lambda year, week, **kw: fetch_calls.append(1) or [])
    monkeypatch.setattr(backfill.fetch_sp_plus, "fetch_week_lines", lambda week, **kw: [])

    summary = backfill.backfill_week(2026, 3, history_dir=tmp_path)
    assert summary["already_up_to_date"] == ["2026-wk03-miami-wake-forest.json"]
    assert summary["updated"] == []


def test_backfill_week_force_recomputes_existing_result(monkeypatch, tmp_path):
    week_dir = tmp_path / "2026-wk03"
    snapshot = _sample_snapshot()
    snapshot["result"] = {"home_points": 0, "away_points": 0}  # stale/wrong
    _write_snapshot(week_dir, "2026-wk03-miami-wake-forest", snapshot)

    games = [{"homeTeam": "Wake Forest", "awayTeam": "Miami", "completed": True, "homePoints": 14, "awayPoints": 34}]
    monkeypatch.setenv("CFBD_API_KEY", "k")
    monkeypatch.setattr(backfill.fetch_matchups, "fetch_fbs_schedule", lambda year, week, **kw: games)
    monkeypatch.setattr(backfill.fetch_sp_plus, "fetch_week_lines", lambda week, **kw: [])

    summary = backfill.backfill_week(2026, 3, history_dir=tmp_path, force=True)
    assert summary["updated"] == ["2026-wk03-miami-wake-forest.json"]
    written = json.loads((week_dir / "2026-wk03-miami-wake-forest.json").read_text())
    assert written["result"]["home_points"] == 14


def test_backfill_week_skips_legacy_snapshot_without_directions(monkeypatch, tmp_path):
    week_dir = tmp_path / "2026-wk03"
    _write_snapshot(week_dir, "legacy-game", {"team_a": "Miami", "team_b": "Wake Forest", "mass": {}})

    monkeypatch.setenv("CFBD_API_KEY", "k")
    monkeypatch.setattr(backfill.fetch_matchups, "fetch_fbs_schedule", lambda year, week, **kw: [])
    monkeypatch.setattr(backfill.fetch_sp_plus, "fetch_week_lines", lambda week, **kw: [])

    summary = backfill.backfill_week(2026, 3, history_dir=tmp_path)
    assert summary["updated"] == []
    assert summary["pending"] == []
    assert summary["already_up_to_date"] == []


def test_backfill_week_missing_directory_reports_warning_not_crash(tmp_path):
    summary = backfill.backfill_week(2026, 3, history_dir=tmp_path)
    assert summary["updated"] == []
    assert "no history directory" in summary["lines_warning"]


def test_backfill_week_continues_without_ats_data_when_lines_fetch_fails(monkeypatch, tmp_path):
    week_dir = tmp_path / "2026-wk03"
    _write_snapshot(week_dir, "2026-wk03-miami-wake-forest", _sample_snapshot())

    games = [{"homeTeam": "Wake Forest", "awayTeam": "Miami", "completed": True, "homePoints": 14, "awayPoints": 34}]
    monkeypatch.setenv("CFBD_API_KEY", "k")
    monkeypatch.setattr(backfill.fetch_matchups, "fetch_fbs_schedule", lambda year, week, **kw: games)

    def _raise(week, **kw):
        raise fetch_sp_plus.SPPlusFetchError("mock sheet outage")
    monkeypatch.setattr(backfill.fetch_sp_plus, "fetch_week_lines", _raise)

    summary = backfill.backfill_week(2026, 3, history_dir=tmp_path)
    assert summary["updated"] == ["2026-wk03-miami-wake-forest.json"]
    assert "mock sheet outage" in summary["lines_warning"]
    written = json.loads((week_dir / "2026-wk03-miami-wake-forest.json").read_text())
    assert "cover_margin_for_team_a" not in written["result"]
    assert written["result"]["home_points"] == 14
