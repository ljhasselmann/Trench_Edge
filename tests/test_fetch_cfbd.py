import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import fetch_cfbd


class _FakeResponse:
    def __init__(self, status_code, json_body, text=""):
        self.status_code = status_code
        self._json_body = json_body
        self.text = text or str(json_body)

    def json(self):
        return self._json_body


class _FakeSession:
    """Stands in for requests.Session so tests never touch the network."""

    def __init__(self, response, captured=None):
        self._response = response
        self._captured = captured if captured is not None else {}

    def get(self, url, params, headers, timeout):
        self._captured["url"] = url
        self._captured["params"] = params
        self._captured["headers"] = headers
        self._captured["timeout"] = timeout
        return self._response


def test_get_api_key_raises_when_missing(monkeypatch):
    monkeypatch.delenv("CFBD_API_KEY", raising=False)
    with pytest.raises(fetch_cfbd.CFBDAuthError):
        fetch_cfbd.get_api_key()


def test_get_api_key_reads_from_environment(monkeypatch):
    monkeypatch.setenv("CFBD_API_KEY", "test-key-123")
    assert fetch_cfbd.get_api_key() == "test-key-123"


def test_fetch_advanced_stats_sends_bearer_auth_and_never_logs_key(monkeypatch, capsys):
    monkeypatch.setenv("CFBD_API_KEY", "super-secret-key")
    captured = {}
    fake_payload = [
        {
            "team": "Miami",
            "offense": {
                "stuffRate": 0.18,
                "lineYards": 2.9,
                "powerSuccess": 0.83,
                "havoc": {"total": 0.16, "frontSeven": 0.11, "db": 0.05},
            },
            "defense": {
                "stuffRate": 0.21,
                "lineYards": 2.6,
                "powerSuccess": 0.71,
                "havoc": {"total": 0.19, "frontSeven": 0.13, "db": 0.06},
            },
        }
    ]
    session = _FakeSession(_FakeResponse(200, fake_payload), captured)

    stats = fetch_cfbd.fetch_advanced_stats("Miami", 2025, session=session)

    assert captured["headers"]["Authorization"] == "Bearer super-secret-key"
    assert captured["params"] == {"year": 2025, "team": "Miami"}
    assert "super-secret-key" not in capsys.readouterr().out

    assert stats.offense.stuff_rate == 0.18
    assert stats.offense.power_success == 0.83
    assert stats.offense.havoc_front_seven == 0.11
    assert stats.defense.line_yards == 2.6
    assert stats.offense.warnings == []


def test_fetch_advanced_stats_flags_missing_fields_instead_of_crashing(monkeypatch):
    monkeypatch.setenv("CFBD_API_KEY", "k")
    # Simulates a schema drift / partial response -- exactly the scenario
    # this session can't rule out without live access to CFBD.
    fake_payload = [{"team": "Miami", "offense": {}, "defense": None}]
    session = _FakeSession(_FakeResponse(200, fake_payload))

    stats = fetch_cfbd.fetch_advanced_stats("Miami", 2025, session=session)

    assert stats.offense.stuff_rate is None
    assert "stuffRate field not present in response" in stats.offense.warnings
    assert "powerSuccess field not present in response" in stats.offense.warnings
    assert stats.defense.warnings == ["side payload missing entirely from API response"]


def test_fetch_advanced_stats_raises_on_non_200(monkeypatch):
    monkeypatch.setenv("CFBD_API_KEY", "k")
    session = _FakeSession(_FakeResponse(403, {}, text="Forbidden"))
    with pytest.raises(fetch_cfbd.CFBDRequestError):
        fetch_cfbd.fetch_advanced_stats("Miami", 2025, session=session)


def test_fetch_advanced_stats_raises_without_key(monkeypatch):
    monkeypatch.delenv("CFBD_API_KEY", raising=False)
    session = _FakeSession(_FakeResponse(200, [{}]))
    with pytest.raises(fetch_cfbd.CFBDAuthError):
        fetch_cfbd.fetch_advanced_stats("Miami", 2025, session=session)


def test_fetch_rushing_direction_splits_computes_success_rate_per_direction(monkeypatch):
    monkeypatch.setenv("CFBD_API_KEY", "k")
    rows = [
        {"offense": "Miami", "rushDirection": "left", "directionAnalysisEligible": True, "success": True},
        {"offense": "Miami", "rushDirection": "left", "directionAnalysisEligible": True, "success": False},
        {"offense": "Miami", "rushDirection": "middle", "directionAnalysisEligible": True, "success": True},
        {"offense": "Miami", "rushDirection": "right", "directionAnalysisEligible": True, "success": True},
        {"offense": "Miami", "rushDirection": "right", "directionAnalysisEligible": True, "success": True},
        # excluded: Miami on defense, not offense
        {"offense": "Ohio State", "defense": "Miami", "rushDirection": "left", "directionAnalysisEligible": True, "success": True},
        # excluded: not direction-analysis-eligible
        {"offense": "Miami", "rushDirection": "left", "directionAnalysisEligible": False, "success": True},
        # excluded: no resolved direction
        {"offense": "Miami", "rushDirection": None, "directionAnalysisEligible": True, "success": True},
    ]
    session = _FakeSession(_FakeResponse(200, rows))

    splits = fetch_cfbd.fetch_rushing_direction_splits("Miami", 2025, session=session)

    assert splits.left.play_count == 2
    assert splits.left.success_rate == pytest.approx(0.5)
    assert splits.middle.play_count == 1
    assert splits.middle.success_rate == pytest.approx(1.0)
    assert splits.right.play_count == 2
    assert splits.right.success_rate == pytest.approx(1.0)
    assert splits.warnings == []


def test_fetch_rushing_direction_splits_no_resolved_plays_is_none_not_zero(monkeypatch):
    monkeypatch.setenv("CFBD_API_KEY", "k")
    rows = [
        {"offense": "Miami", "rushDirection": None, "directionAnalysisEligible": False, "success": None},
    ]
    session = _FakeSession(_FakeResponse(200, rows))

    splits = fetch_cfbd.fetch_rushing_direction_splits("Miami", 2025, session=session)

    assert splits.left.success_rate is None
    assert splits.left.play_count == 0
    assert splits.middle.success_rate is None
    assert splits.right.success_rate is None
    assert len(splits.warnings) == 1
    assert "direction splits unavailable" in splits.warnings[0]


def test_fetch_rushing_direction_splits_raises_on_non_200(monkeypatch):
    monkeypatch.setenv("CFBD_API_KEY", "k")
    session = _FakeSession(_FakeResponse(500, {}, text="Server Error"))
    with pytest.raises(fetch_cfbd.CFBDRequestError):
        fetch_cfbd.fetch_rushing_direction_splits("Miami", 2025, session=session)


def test_fetch_season_stat_map_flattens_rows(monkeypatch):
    monkeypatch.setenv("CFBD_API_KEY", "k")
    rows = [
        {"season": 2025, "team": "Miami", "statName": "sacks", "statValue": 50},
        {"season": 2025, "team": "Miami", "statName": "passAttempts", "statValue": 496},
    ]
    session = _FakeSession(_FakeResponse(200, rows))
    stat_map = fetch_cfbd.fetch_season_stat_map("Miami", 2025, session=session)
    assert stat_map == {"sacks": 50, "passAttempts": 496}


def test_apply_sack_rates_matches_worked_example():
    # Real values pulled live for Miami, 2025 season (see fetch_cfbd.py
    # module docstring for the offense/defense naming convention).
    stat_map = {
        "passAttempts": 496,
        "sacksOpponent": 20,
        "passAttemptsOpponent": 521,
        "sacks": 50,
    }
    offense, defense = fetch_cfbd.SideStats(), fetch_cfbd.SideStats()
    fetch_cfbd._apply_sack_rates(offense, defense, stat_map)

    assert round(offense.adjusted_sack_rate, 4) == round(20 / (496 + 20), 4)
    assert round(defense.adjusted_sack_rate, 4) == round(50 / (521 + 50), 4)
    assert offense.warnings == []
    assert defense.warnings == []


def test_apply_sack_rates_flags_missing_inputs():
    offense, defense = fetch_cfbd.SideStats(), fetch_cfbd.SideStats()
    fetch_cfbd._apply_sack_rates(offense, defense, {})

    assert offense.adjusted_sack_rate is None
    assert defense.adjusted_sack_rate is None
    assert "adjusted sack rate unavailable" in offense.warnings[0]
    assert "adjusted sack rate unavailable" in defense.warnings[0]


def test_fetch_fbs_teams_returns_school_and_alternate_names(monkeypatch):
    monkeypatch.setenv("CFBD_API_KEY", "k")
    payload = [
        {"school": "Miami", "alternateNames": ["Miami (FL)", "MIA", "Miami"]},
        {"school": "Miami (OH)", "alternateNames": ["M-OH", "Miami OH"]},
    ]
    session = _FakeSession(_FakeResponse(200, payload))

    teams = fetch_cfbd.fetch_fbs_teams(2026, session=session)

    assert len(teams) == 2
    assert teams[0]["school"] == "Miami"
    assert "Miami (FL)" in teams[0]["alternateNames"]


def test_fetch_fbs_teams_raises_on_non_200(monkeypatch):
    monkeypatch.setenv("CFBD_API_KEY", "k")
    session = _FakeSession(_FakeResponse(500, []))
    with pytest.raises(fetch_cfbd.CFBDRequestError):
        fetch_cfbd.fetch_fbs_teams(2026, session=session)


SAMPLE_CALENDAR = [
    {"season": 2026, "week": 1, "seasonType": "regular", "startDate": "2026-08-29T07:00:00.000Z", "endDate": "2026-09-08T06:59:00.000Z"},
    {"season": 2026, "week": 2, "seasonType": "regular", "startDate": "2026-09-08T07:00:00.000Z", "endDate": "2026-09-14T06:59:00.000Z"},
    {"season": 2026, "week": 3, "seasonType": "regular", "startDate": "2026-09-14T07:00:00.000Z", "endDate": "2026-09-21T06:59:00.000Z"},
    {"season": 2026, "week": 1, "seasonType": "postseason", "startDate": "2027-01-01T00:00:00.000Z", "endDate": "2027-01-20T06:59:00.000Z"},
]


def test_detect_current_week_finds_the_week_containing_now(monkeypatch):
    import datetime as _dt

    monkeypatch.setenv("CFBD_API_KEY", "k")
    session = _FakeSession(_FakeResponse(200, SAMPLE_CALENDAR))
    now = _dt.datetime(2026, 9, 16, tzinfo=_dt.timezone.utc)

    week = fetch_cfbd.detect_current_week(2026, session=session, now=now)

    assert week == 3


def test_detect_current_week_before_season_returns_week_one(monkeypatch):
    import datetime as _dt

    monkeypatch.setenv("CFBD_API_KEY", "k")
    session = _FakeSession(_FakeResponse(200, SAMPLE_CALENDAR))
    now = _dt.datetime(2026, 8, 1, tzinfo=_dt.timezone.utc)

    week = fetch_cfbd.detect_current_week(2026, session=session, now=now)

    assert week == 1


def test_detect_current_week_after_regular_season_returns_last_week(monkeypatch):
    import datetime as _dt

    monkeypatch.setenv("CFBD_API_KEY", "k")
    session = _FakeSession(_FakeResponse(200, SAMPLE_CALENDAR))
    now = _dt.datetime(2026, 12, 1, tzinfo=_dt.timezone.utc)

    week = fetch_cfbd.detect_current_week(2026, session=session, now=now)

    assert week == 3  # last regular-season week in the fixture, postseason row ignored


def test_detect_current_week_raises_when_no_regular_season_rows(monkeypatch):
    monkeypatch.setenv("CFBD_API_KEY", "k")
    session = _FakeSession(_FakeResponse(200, [SAMPLE_CALENDAR[-1]]))  # only the postseason row
    with pytest.raises(fetch_cfbd.CFBDRequestError):
        fetch_cfbd.detect_current_week(2026, session=session)
