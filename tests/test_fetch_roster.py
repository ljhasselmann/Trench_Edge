import datetime as dt
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import fetch_roster


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


LIVE_ROSTER = [
    {"firstName": "Jacob", "lastName": "Hawks", "position": "OL", "weight": 330},
    {"firstName": "Damon", "lastName": "Wilson", "position": "DL", "weight": 250},
    {"firstName": "No", "lastName": "Weight", "position": "OL", "weight": None},
]


def _write_config(tmp_path, monkeypatch, config: dict):
    monkeypatch.setattr(fetch_roster, "ROSTERS_DIR", tmp_path)
    path = tmp_path / f"{config['team']}.yaml"
    with open(path, "w") as f:
        yaml.safe_dump(config, f)
    return path


def test_missing_config_file_returns_warning_not_crash(monkeypatch, tmp_path):
    monkeypatch.setenv("CFBD_API_KEY", "k")
    monkeypatch.setattr(fetch_roster, "ROSTERS_DIR", tmp_path)
    result = fetch_roster.compute_mass_inputs("Nonexistent Team", 2026, session=_FakeSession(_FakeResponse(200, [])))
    assert result.avg_ol_weight is None
    assert "does not exist" in result.warnings[0]


def test_matches_live_roster_and_computes_average(monkeypatch, tmp_path):
    monkeypatch.setenv("CFBD_API_KEY", "k")
    now = dt.datetime(2026, 9, 15, tzinfo=dt.timezone.utc)
    _write_config(tmp_path, monkeypatch, {
        "team": "Miami",
        "updated_by_human_at": "2026-09-14",
        "starters": {"OL": [{"name": "Jacob Hawks"}], "DL": [{"name": "Damon Wilson"}]},
    })
    session = _FakeSession(_FakeResponse(200, LIVE_ROSTER))

    result = fetch_roster.compute_mass_inputs("Miami", 2026, session=session, now=now)

    assert result.avg_ol_weight == 330
    assert result.avg_dl_weight == 250
    assert result.ol_starters[0].confidence == "confirmed"
    assert not any("stale" in w for w in result.warnings)


def test_truncated_puntandrally_name_resolves_against_live_roster(monkeypatch, tmp_path):
    # puntandrally staged this starter as "J. Hawks" (truncated first name)
    # -- config/rosters/{team}.yaml carries whatever name it staged, so an
    # exact match against CFBD's "Jacob Hawks" would otherwise fail and
    # silently drop a real starter (see fetch_puntandrally.resolve_truncated_name).
    monkeypatch.setenv("CFBD_API_KEY", "k")
    now = dt.datetime(2026, 9, 15, tzinfo=dt.timezone.utc)
    _write_config(tmp_path, monkeypatch, {
        "team": "Miami",
        "updated_by_human_at": "2026-09-14",
        "starters": {"OL": [{"name": "J. Hawks"}], "DL": []},
    })
    session = _FakeSession(_FakeResponse(200, LIVE_ROSTER))

    result = fetch_roster.compute_mass_inputs("Miami", 2026, session=session, now=now)

    assert result.avg_ol_weight == 330
    assert result.ol_starters[0].confidence == "confirmed"
    assert any("matched live CFBD roster as 'Jacob Hawks'" in w for w in result.warnings)


def test_suffix_dropped_puntandrally_name_resolves_against_live_roster(monkeypatch, tmp_path):
    # puntandrally staged this starter as "Damon Wilson" (no suffix) --
    # CFBD's real name carries a generational suffix. See
    # fetch_puntandrally.resolve_name_variant.
    monkeypatch.setenv("CFBD_API_KEY", "k")
    now = dt.datetime(2026, 9, 15, tzinfo=dt.timezone.utc)
    live_roster = [{"firstName": "Damon", "lastName": "Wilson II", "position": "DL", "weight": 250}]
    _write_config(tmp_path, monkeypatch, {
        "team": "Miami",
        "updated_by_human_at": "2026-09-14",
        "starters": {"OL": [], "DL": [{"name": "Damon Wilson"}]},
    })
    session = _FakeSession(_FakeResponse(200, live_roster))

    result = fetch_roster.compute_mass_inputs("Miami", 2026, session=session, now=now)

    assert result.avg_dl_weight == 250
    assert result.dl_starters[0].confidence == "confirmed"
    assert any("matched live CFBD roster as 'Damon Wilson II'" in w for w in result.warnings)


def test_flags_stale_starter_list(monkeypatch, tmp_path):
    monkeypatch.setenv("CFBD_API_KEY", "k")
    now = dt.datetime(2026, 9, 15, tzinfo=dt.timezone.utc)
    _write_config(tmp_path, monkeypatch, {
        "team": "Miami",
        "updated_by_human_at": "2026-09-01",  # 14 days old
        "starters": {"OL": [], "DL": []},
    })
    session = _FakeSession(_FakeResponse(200, LIVE_ROSTER))

    result = fetch_roster.compute_mass_inputs("Miami", 2026, session=session, now=now)

    assert any("14 days old" in w for w in result.warnings)


def test_unmatched_starter_without_fallback_is_excluded_and_flagged(monkeypatch, tmp_path):
    monkeypatch.setenv("CFBD_API_KEY", "k")
    now = dt.datetime(2026, 9, 15, tzinfo=dt.timezone.utc)
    _write_config(tmp_path, monkeypatch, {
        "team": "Miami",
        "updated_by_human_at": "2026-09-14",
        "starters": {"OL": [{"name": "Ghost Player"}], "DL": []},
    })
    session = _FakeSession(_FakeResponse(200, LIVE_ROSTER))

    result = fetch_roster.compute_mass_inputs("Miami", 2026, session=session, now=now)

    assert result.ol_starters == []
    assert result.avg_ol_weight is None
    assert any("not found in live CFBD roster and no fallback" in w for w in result.warnings)


def test_unmatched_starter_with_fallback_weight_is_flagged_estimated(monkeypatch, tmp_path):
    monkeypatch.setenv("CFBD_API_KEY", "k")
    now = dt.datetime(2026, 9, 15, tzinfo=dt.timezone.utc)
    _write_config(tmp_path, monkeypatch, {
        "team": "Miami",
        "updated_by_human_at": "2026-09-14",
        "starters": {"OL": [{"name": "Transfer Guy", "weight": 310, "source": "beat reporter"}], "DL": []},
    })
    session = _FakeSession(_FakeResponse(200, LIVE_ROSTER))

    result = fetch_roster.compute_mass_inputs("Miami", 2026, session=session, now=now)

    assert result.avg_ol_weight == 310
    assert result.ol_starters[0].confidence == "estimated"
    assert any("using human-supplied estimated weight" in w for w in result.warnings)


def test_starter_carries_jersey_class_year_and_multi_year_snaps_from_config(monkeypatch, tmp_path):
    monkeypatch.setenv("CFBD_API_KEY", "k")
    now = dt.datetime(2026, 9, 15, tzinfo=dt.timezone.utc)
    _write_config(tmp_path, monkeypatch, {
        "team": "Miami",
        "updated_by_human_at": "2026-09-14",
        "starters": {
            "OL": [{"name": "Jacob Hawks", "jersey": "75", "class_year": "SO", "snaps_multi_year": 695}],
            "DL": [],
        },
    })
    session = _FakeSession(_FakeResponse(200, LIVE_ROSTER))

    result = fetch_roster.compute_mass_inputs("Miami", 2026, session=session, now=now)

    starter = result.ol_starters[0]
    assert starter.jersey == "75"
    assert starter.class_year == "SO"
    assert starter.snaps_multi_year == 695


def test_position_tag_mismatch_is_flagged(monkeypatch, tmp_path):
    monkeypatch.setenv("CFBD_API_KEY", "k")
    now = dt.datetime(2026, 9, 15, tzinfo=dt.timezone.utc)
    _write_config(tmp_path, monkeypatch, {
        "team": "Miami",
        "updated_by_human_at": "2026-09-14",
        # Damon Wilson is tagged DL live, but listed under OL in config -- likely a mistake.
        "starters": {"OL": [{"name": "Damon Wilson"}], "DL": []},
    })
    session = _FakeSession(_FakeResponse(200, LIVE_ROSTER))

    result = fetch_roster.compute_mass_inputs("Miami", 2026, session=session, now=now)

    assert any("verify this is the right player" in w for w in result.warnings)


def test_specific_ol_position_tag_is_not_flagged_as_a_mismatch(monkeypatch, tmp_path):
    # CFBD isn't consistent about OL tagging across teams -- some use the
    # generic "OL", others the specific "OT"/"OG"/"C" codes (confirmed
    # live: LSU tags real tackles "OT"). A specifically-tagged OL starter
    # must not falsely trigger the "verify this is the right player"
    # warning the way an actual DL/LB mismatch should.
    monkeypatch.setenv("CFBD_API_KEY", "k")
    now = dt.datetime(2026, 9, 15, tzinfo=dt.timezone.utc)
    live_roster = [{"firstName": "Jacob", "lastName": "Hawks", "position": "OT", "weight": 330}]
    _write_config(tmp_path, monkeypatch, {
        "team": "Miami",
        "updated_by_human_at": "2026-09-14",
        "starters": {"OL": [{"name": "Jacob Hawks"}], "DL": []},
    })
    session = _FakeSession(_FakeResponse(200, live_roster))

    result = fetch_roster.compute_mass_inputs("Miami", 2026, session=session, now=now)

    assert not any("verify this is the right player" in w for w in result.warnings)
