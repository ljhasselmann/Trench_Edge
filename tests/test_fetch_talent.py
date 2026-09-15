import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import fetch_roster
import fetch_talent


class _FakeResponse:
    def __init__(self, status_code, json_body):
        self.status_code = status_code
        self._json_body = json_body
        self.text = str(json_body)

    def json(self):
        return self._json_body


class _FakeSession:
    def __init__(self, response):
        self._response = response

    def get(self, url, params, headers, timeout):
        return self._response


TALENT_ROWS = [
    {"year": 2026, "team": "Georgia", "talent": 1003.67},
    {"year": 2026, "team": "Miami", "talent": 885.94},
]


def _write_config(tmp_path, monkeypatch, config: dict):
    monkeypatch.setattr(fetch_roster, "ROSTERS_DIR", tmp_path)
    path = tmp_path / f"{config['team']}.yaml"
    with open(path, "w") as f:
        yaml.safe_dump(config, f)


def test_fetch_team_talent_returns_value_for_present_team(monkeypatch):
    monkeypatch.setenv("CFBD_API_KEY", "k")
    session = _FakeSession(_FakeResponse(200, TALENT_ROWS))
    assert fetch_talent.fetch_team_talent("Miami", 2026, session=session) == 885.94


def test_fetch_team_talent_returns_none_for_absent_team(monkeypatch):
    monkeypatch.setenv("CFBD_API_KEY", "k")
    session = _FakeSession(_FakeResponse(200, TALENT_ROWS))
    assert fetch_talent.fetch_team_talent("Directional State", 2026, session=session) is None


def test_compute_continuity_inputs_full_config(monkeypatch, tmp_path):
    monkeypatch.setenv("CFBD_API_KEY", "k")
    _write_config(tmp_path, monkeypatch, {
        "team": "Miami",
        "updated_by_human_at": "2026-09-14",
        "starters": {
            "OL": [{"name": "Jacob Hawks"}, {"name": "New Guy"}],
            "DL": [{"name": "Damon Wilson"}],
        },
        "prior_season_starters": {
            "OL": [{"name": "Jacob Hawks"}, {"name": "Departed Guy"}],
            "DL": [{"name": "Damon Wilson"}],
        },
        "continuity_note": {"driver": "scheme", "note": "Beat reporters cite new DC scheme."},
    })
    session = _FakeSession(_FakeResponse(200, TALENT_ROWS))

    result = fetch_talent.compute_continuity_inputs("Miami", 2026, session=session)

    assert result.talent_composite == 885.94
    assert result.returning_ol_starters == 1  # only "Jacob Hawks" overlaps
    assert result.returning_dl_starters == 1
    assert result.continuity_driver == "scheme"
    assert result.continuity_note == "Beat reporters cite new DC scheme."
    assert result.warnings == []


def test_compute_continuity_inputs_missing_config_flags_and_continues(monkeypatch, tmp_path):
    monkeypatch.setenv("CFBD_API_KEY", "k")
    monkeypatch.setattr(fetch_roster, "ROSTERS_DIR", tmp_path)
    session = _FakeSession(_FakeResponse(200, TALENT_ROWS))

    result = fetch_talent.compute_continuity_inputs("Miami", 2026, session=session)

    assert result.talent_composite == 885.94
    assert result.returning_ol_starters is None
    assert any("does not exist" in w for w in result.warnings)


def test_compute_continuity_inputs_missing_prior_season_block(monkeypatch, tmp_path):
    monkeypatch.setenv("CFBD_API_KEY", "k")
    _write_config(tmp_path, monkeypatch, {
        "team": "Miami",
        "updated_by_human_at": "2026-09-14",
        "starters": {"OL": [{"name": "Jacob Hawks"}], "DL": []},
    })
    session = _FakeSession(_FakeResponse(200, TALENT_ROWS))

    result = fetch_talent.compute_continuity_inputs("Miami", 2026, session=session)

    assert result.returning_ol_starters is None
    assert any("prior_season_starters" in w for w in result.warnings)
    assert any("continuity_note" in w for w in result.warnings)
