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

# Real schedule-half row, captured live 2026-09-16 (FBS Week 3 tab) --
# same tab VALID_CSV's ratings-half row comes from, just the columns to
# the left that fetch_fbs_week_table doesn't touch.
SCHEDULE_CSV = (
    '"Date","Time (ET)","Game","Proj. winner","Proj. margin","Win prob.","Proj. score (rounded)",'
    '"Spread","ATS Pick","Spread diff","O/U","O/U pick","O/U diff","",'
    '"Team","Conference","Rec.","SP+","Rk","Off. SP+","Rk","Def. SP+","Rk","ST SP+","Rk","LW","LW Rk"\n'
    '"18-Sep","7:30 PM","Miami-FL at Wake Forest","Miami-FL","19.7","89%","34-14",'
    '"Miami-FL -22.5","Wake Forest","2.8","55.5","Under","-7.3","",'
    '"Miami-FL","ACC","2-0","25.6","4","37.2","10","11.9","4","0.3","42","1","5"\n'
    '"17-Sep","7:30 PM","Portland State at Oregon","","","","",'
    '"","","","","","","",'
    '"Wake Forest","ACC","2-0","3.4","62","25.3","79","22.0","47","0.0","75","2","67"\n'
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


def test_fetch_week_lines_parses_real_spread_and_ats_pick():
    session = _FakeSession(_FakeResponse(200, SCHEDULE_CSV))
    lines = fetch_sp_plus.fetch_week_lines(3, session=session)

    miami_wake = next(l for l in lines if {l.away_team, l.home_team} == {"Miami-FL", "Wake Forest"})
    assert miami_wake.away_team == "Miami-FL"
    assert miami_wake.home_team == "Wake Forest"
    assert miami_wake.favorite == "Miami-FL"
    assert miami_wake.spread == 22.5
    assert miami_wake.ats_pick == "Wake Forest"  # Connelly's model picks the underdog to cover
    assert miami_wake.proj_margin == 19.7
    assert miami_wake.over_under == 55.5
    assert miami_wake.ou_pick == "Under"


def test_fetch_week_lines_handles_game_with_no_line_posted():
    session = _FakeSession(_FakeResponse(200, SCHEDULE_CSV))
    lines = fetch_sp_plus.fetch_week_lines(3, session=session)

    fcs_game = next(l for l in lines if {l.away_team, l.home_team} == {"Portland State", "Oregon"})
    assert fcs_game.favorite is None
    assert fcs_game.spread is None
    assert fcs_game.ats_pick is None


def test_find_game_line_resolves_aliases_and_either_order():
    session = _FakeSession(_FakeResponse(200, SCHEDULE_CSV))
    lines = fetch_sp_plus.fetch_week_lines(3, session=session)

    # CFBD's canonical names ("Miami", "Wake Forest"), not the sheet's own
    # spelling ("Miami-FL") -- find_game_line must resolve the alias.
    line = fetch_sp_plus.find_game_line(lines, "Miami", "Wake Forest")
    assert line is not None
    assert line.spread == 22.5

    # order-independent
    line_reversed = fetch_sp_plus.find_game_line(lines, "Wake Forest", "Miami")
    assert line_reversed is line


def test_find_game_line_returns_none_when_not_found():
    session = _FakeSession(_FakeResponse(200, SCHEDULE_CSV))
    lines = fetch_sp_plus.fetch_week_lines(3, session=session)
    assert fetch_sp_plus.find_game_line(lines, "Texas", "Ohio State") is None


def test_parse_spread_handles_blank_and_real_values():
    assert fetch_sp_plus._parse_spread("Miami-FL -22.5") == ("Miami-FL", 22.5)
    assert fetch_sp_plus._parse_spread("") is None
    assert fetch_sp_plus._parse_spread("   ") is None
