import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import fetch_sp_plus


VALID_CSV = (
    '"Date","Time (ET)","Game","","","","","","","","","","",'
    '"Team","Conference","Rec.","SP+","Rk","Off. SP+","Rk","Def. SP+","Rk","ST SP+","Rk","LW","LW Rk"\n'
    '"17-Sep","7:30 PM","Syracuse at Pittsburgh","","","","","","","","","","",'
    '"Miami-FL","ACC","2-0","25.6","4","37.2","10","11.9","4","0.3","42","1","5"\n'
    '"17-Sep","7:30 PM","Syracuse at Pittsburgh","","","","","","","","","","",'
    '"Wake Forest","ACC","2-0","3.4","62","25.3","79","22.0","47","0.0","75","2","67"\n'
)

FALLBACK_CSV = (
    '"Week","ATS record vs early-week spread (FBS vs FBS only)","%"\n'
    '"0-1","28-22-1","55.9%"\n'
)


class _FakeResponse:
    def __init__(self, status_code, text):
        self.status_code = status_code
        self.text = text


class _FakeSession:
    def __init__(self, response):
        self._response = response
        self.last_params = None

    def get(self, url, params, timeout):
        self.last_params = params
        return self._response


def test_fetch_fbs_week_table_parses_valid_response():
    session = _FakeSession(_FakeResponse(200, VALID_CSV))
    table = fetch_sp_plus.fetch_fbs_week_table(3, session=session)

    assert session.last_params == {"tqx": "out:csv", "sheet": "FBS Week 3"}
    assert table["Miami-FL"].sp_plus == 25.6
    assert table["Miami-FL"].off_sp_plus_rank == 10
    assert table["Wake Forest"].def_sp_plus == 22.0


def test_fetch_fbs_week_table_rejects_silent_fallback_to_wrong_tab():
    # gviz returns HTTP 200 with the workbook's FIRST tab when the
    # requested sheet name doesn't exist -- must not be trusted as real
    # ratings data just because the status code is 200.
    session = _FakeSession(_FakeResponse(200, FALLBACK_CSV))
    with pytest.raises(fetch_sp_plus.SPPlusFetchError, match="doesn't look like an FBS ratings tab"):
        fetch_sp_plus.fetch_fbs_week_table(99, session=session)


def test_fetch_fbs_week_table_raises_on_non_200():
    session = _FakeSession(_FakeResponse(500, ""))
    with pytest.raises(fetch_sp_plus.SPPlusFetchError):
        fetch_sp_plus.fetch_fbs_week_table(3, session=session)


def test_team_sp_plus_resolves_known_alias():
    session = _FakeSession(_FakeResponse(200, VALID_CSV))
    result = fetch_sp_plus.team_sp_plus("Miami", 3, session=session)  # CFBD name, not sheet name
    assert result.team == "Miami-FL"
    assert result.sp_plus == 25.6


def test_team_sp_plus_raises_with_suggestions_on_miss():
    session = _FakeSession(_FakeResponse(200, VALID_CSV))
    with pytest.raises(fetch_sp_plus.SPPlusFetchError, match="Closest names"):
        fetch_sp_plus.team_sp_plus("Wake Forrest", 3, session=session)  # typo


def test_compute_sp_plus_gap():
    session = _FakeSession(_FakeResponse(200, VALID_CSV))
    gap = fetch_sp_plus.compute_sp_plus_gap("Miami", "Wake Forest", 3, session=session)
    assert round(gap, 1) == 22.2


def test_compute_sp_plus_gap_reuses_prefetched_table_without_refetching():
    session = _FakeSession(_FakeResponse(200, VALID_CSV))
    table = fetch_sp_plus.fetch_fbs_week_table(3, session=session)

    class _ExplodingSession:
        def get(self, *a, **k):
            raise AssertionError("should not fetch again -- a table was already provided")

    gap = fetch_sp_plus.compute_sp_plus_gap("Miami", "Wake Forest", 3, table=table, session=_ExplodingSession())
    assert round(gap, 1) == 22.2
