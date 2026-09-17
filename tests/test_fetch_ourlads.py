import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import fetch_ourlads


# Real captured fragments from ourlads.com, live 2026-09-15.
SAMPLE_INDEX_HTML = """
<div class='nfl-dc-mm-team'><img src='https://www.ourlads.com/images/colleges/FBS_MIA.gif' alt='Miami' class='nfl-dc-mm-logo' /><div class='nfl-dc-mm-team-name'>Miami </div></div><div class='ncaa-dc-mm-team-links'><a href='depth-chart.aspx?s=miami&id=91073'>Depth Chart</a>
<div class='nfl-dc-mm-team'><img src='https://www.ourlads.com/images/colleges/FBS_WAK.gif' alt='Wake Forest' class='nfl-dc-mm-logo' /><div class='nfl-dc-mm-team-name'>Wake Forest </div></div><div class='ncaa-dc-mm-team-links'><a href='depth-chart.aspx?s=wake-forest&id=92430'>Depth Chart</a>
"""

SAMPLE_CHART_HTML = """
<tr class='row-dc-grey'><td class='row-dc-grey'>LT</td><td>63</td><td><a href='https://www.ourlads.com/ncaa-football-depth-charts/player/samson-okunlola/160362' class=''>Okunlola, Samson RS JR</a></td><td>71</td><td><a href='https://www.ourlads.com/ncaa-football-depth-charts/player/jamal-meriweather/166922' class='lc_gold'>Meriweather, Jamal RS JR/TR</a></td></tr>
<tr class='row-dc-wht'><td class='row-dc-wht'>NT</td><td>94</td><td><a href='https://www.ourlads.com/ncaa-football-depth-charts/player/zach-lohavichan/150747' class=''>Lohavichan, Zach RS SR</a></td></tr>
<tr class='row-dc-grey'><td class='row-dc-grey'>LDE</td><td>10</td><td><a href='https://www.ourlads.com/ncaa-football-depth-charts/player/gabe-kirschke/157239' class=''>Kirschke, Gabe RS SR/TR</a></td><td>14</td><td><a href='https://www.ourlads.com/ncaa-football-depth-charts/player/tyler-walton/166373' class=''>Walton, Tyler RS JR</a></td></tr>
<tr class='row-dc-wht'><td class='row-dc-wht'>WLB</td><td>43</td><td><a href='https://www.ourlads.com/ncaa-football-depth-charts/player/frank-cusano/169540' class=''>Cusano, Frank RS SO/TR</a></td></tr>
"""

SAMPLE_PAGE_HTML = f"""
<h2 class='MIA'>Offense <small><b>Air Raid</b></small></h2>
{SAMPLE_CHART_HTML}
<h2 class='MIA'>Defense <small><b>4-2-5</b></small></h2>
"""


class _FakeResponse:
    def __init__(self, status_code, text):
        self.status_code = status_code
        self.text = text


class _FakeSession:
    def __init__(self, response):
        self._response = response

    def get(self, url, headers, timeout):
        return self._response


def test_fetch_team_index_parses_names_to_slug_id():
    session = _FakeSession(_FakeResponse(200, SAMPLE_INDEX_HTML))
    index = fetch_ourlads.fetch_team_index(session=session)
    assert index["Miami"] == ("miami", "91073")
    assert index["Wake Forest"] == ("wake-forest", "92430")


def test_fetch_team_index_raises_on_empty_parse():
    session = _FakeSession(_FakeResponse(200, "<html>nothing here</html>"))
    with pytest.raises(fetch_ourlads.OurladsFetchError, match="zero teams"):
        fetch_ourlads.fetch_team_index(session=session)


def test_parse_depth_chart_extracts_rows_in_depth_order():
    chart = fetch_ourlads.parse_depth_chart(SAMPLE_CHART_HTML)
    assert chart["LT"] == ["Samson Okunlola", "Jamal Meriweather"]
    assert chart["NT"] == ["Zach Lohavichan"]
    assert chart["LDE"] == ["Gabe Kirschke", "Tyler Walton"]
    assert chart["WLB"] == ["Frank Cusano"]


def test_to_first_last_handles_initials_and_transfer_markers():
    assert fetch_ourlads._to_first_last("Johnson, D.J. RS SO/TR") == "D.J. Johnson"
    assert fetch_ourlads._to_first_last("Smalls-Allen, Lucas FR") == "Lucas Smalls-Allen"


def test_starters_for_group_takes_depth_position_one_only():
    chart = fetch_ourlads.TeamDepthChart(rows=fetch_ourlads.parse_depth_chart(SAMPLE_CHART_HTML))
    ol_starters = fetch_ourlads.starters_for_group(chart, fetch_ourlads.OL_ROW_LABELS, fetch_ourlads.OL_ROW_ORDER)
    assert ol_starters == ["Samson Okunlola"]  # not Meriweather (2nd string)

    dl_starters = fetch_ourlads.starters_for_group(chart, fetch_ourlads.DL_ROW_LABELS, fetch_ourlads.DL_ROW_ORDER)
    assert set(dl_starters) == {"Zach Lohavichan", "Gabe Kirschke"}
    assert "Frank Cusano" not in dl_starters  # LB, not DL


def test_starters_with_labels_orders_left_to_right_by_real_ourlads_slot():
    # LDE (left end) must sort before NT (interior) regardless of the
    # chart's own row order -- real positional order, not row order.
    chart = fetch_ourlads.TeamDepthChart(rows=fetch_ourlads.parse_depth_chart(SAMPLE_CHART_HTML))
    dl = fetch_ourlads.starters_with_labels(chart, fetch_ourlads.DL_ROW_LABELS, fetch_ourlads.DL_ROW_ORDER)
    assert dl == [("LDE", "Gabe Kirschke"), ("NT", "Zach Lohavichan")]


def test_dl_row_labels_recognizes_bare_de_with_no_left_right_split():
    # Confirmed live: a MAJORITY of real team pages (Colorado State,
    # Florida State, Georgia, Arkansas, West Virginia, Indiana, Virginia,
    # ...) use a single generic "DE" row rather than separate LDE/RDE
    # rows -- this isn't a rare scheme variant, it's the common case, and
    # omitting it silently dropped a real starter for most teams.
    chart_html = """
    <tr class='row-dc-grey'><td class='row-dc-grey'>DE</td><td>91</td><td><a href='https://www.ourlads.com/ncaa-football-depth-charts/player/fisher-camac/1' class=''>Camac, Fisher SR</a></td></tr>
    <tr class='row-dc-wht'><td class='row-dc-wht'>NT</td><td>55</td><td><a href='https://www.ourlads.com/ncaa-football-depth-charts/player/anthony-britton/2' class=''>Britton, Anthony SR</a></td></tr>
    <tr class='row-dc-grey'><td class='row-dc-grey'>DT</td><td>90</td><td><a href='https://www.ourlads.com/ncaa-football-depth-charts/player/jason-hammond/3' class=''>Hammond, Jason SR</a></td></tr>
    """
    chart = fetch_ourlads.TeamDepthChart(rows=fetch_ourlads.parse_depth_chart(chart_html))
    dl = fetch_ourlads.starters_for_group(chart, fetch_ourlads.DL_ROW_LABELS, fetch_ourlads.DL_ROW_ORDER)
    assert set(dl) == {"Fisher Camac", "Anthony Britton", "Jason Hammond"}


def test_parse_schemes_extracts_offense_and_defense_labels():
    schemes = fetch_ourlads.parse_schemes(SAMPLE_PAGE_HTML)
    assert schemes == {"offense": "Air Raid", "defense": "4-2-5"}


def test_parse_schemes_returns_empty_dict_when_headings_absent():
    assert fetch_ourlads.parse_schemes("<html>nothing here</html>") == {}


def test_fetch_depth_chart_raises_clear_error_for_unknown_team():
    session = _FakeSession(_FakeResponse(200, SAMPLE_INDEX_HTML))
    index = fetch_ourlads.fetch_team_index(session=session)
    with pytest.raises(fetch_ourlads.OurladsFetchError, match="Closest matches"):
        fetch_ourlads.fetch_depth_chart("Wake Forrest", session=session, index=index)


def test_fetch_depth_chart_raises_on_non_200():
    session = _FakeSession(_FakeResponse(500, ""))
    with pytest.raises(fetch_ourlads.OurladsFetchError):
        fetch_ourlads.fetch_depth_chart("Miami", session=session, index={"Miami": ("miami", "91073")})


def test_fetch_depth_chart_returns_rows_and_schemes():
    session = _FakeSession(_FakeResponse(200, SAMPLE_PAGE_HTML))
    chart = fetch_ourlads.fetch_depth_chart("Miami", session=session, index={"Miami": ("miami", "91073")})
    assert chart.rows["LT"] == ["Samson Okunlola", "Jamal Meriweather"]
    assert chart.offense_scheme == "Air Raid"
    assert chart.defense_scheme == "4-2-5"


def test_fetch_depth_chart_resolves_known_alias():
    # CFBD calls this team "NC State"; ourlads spells it out.
    index = {"North Carolina State": ("nc-state", "12345")}
    session = _FakeSession(_FakeResponse(200, SAMPLE_CHART_HTML))
    chart = fetch_ourlads.fetch_depth_chart("NC State", session=session, index=index)
    assert chart.rows["LT"] == ["Samson Okunlola", "Jamal Meriweather"]


def test_fetch_depth_chart_missing_team_error_names_the_resolved_lookup():
    # A team genuinely absent from ourlads (e.g. Washington State) should
    # raise clearly, not silently match something else.
    index = {"Washington": ("washington", "1")}
    session = _FakeSession(_FakeResponse(200, ""))
    with pytest.raises(fetch_ourlads.OurladsFetchError, match="Washington State"):
        fetch_ourlads.fetch_depth_chart("Washington State", session=session, index=index)
