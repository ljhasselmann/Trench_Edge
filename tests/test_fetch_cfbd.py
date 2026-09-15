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
                "havoc": {"total": 0.16, "frontSeven": 0.11, "db": 0.05},
            },
            "defense": {
                "stuffRate": 0.21,
                "lineYards": 2.6,
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
