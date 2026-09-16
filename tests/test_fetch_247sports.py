import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import fetch_247sports as fs


# Real captured fragments from 247Sports.com's real Miami 2026 roster page,
# live 2026-09-16. McCoy has a recruiting profile (name is an <a> link),
# Azziz doesn't (name is a plain <span> -- a real, live-confirmed gap:
# 18 of Miami's 115 real roster rows are link-less this way). Borchers and
# Azziz are both genuinely unrated ("NA", zero star icons).
SAMPLE_NAME_TABLE = """
<table class="name-table" data-id="name">
<tr> <th class="table-heading">Name</th> </tr>
<tr>  <td class="name" data-sort="McCoy"> <a href="https://247sports.com/player/matthew-mccoy-46125647/">Matthew McCoy</a>  </td>  </tr>
<tr>  <td class="name" data-sort="Azziz"> <span>Takai Azziz</span>  </td>  </tr>
<tr>  <td class="name" data-sort="Borchers"> <a href="https://247sports.com/player/joe-borchers-46086612/">Joe Borchers</a>  </td>  </tr>
</table>
"""

SAMPLE_DATA_TABLE = """
<table data-id="data">
<tr> <th class="table-heading">Jersey</th> <th class="table-heading">POS</th> <th class="table-heading">Height</th> <th class="table-heading">Weight</th>  <th class="table-heading">Yr</th> <th class="table-heading">Age</th> <th class="table-heading">High School</th>  <th class="table-heading">Rating</th> </tr>
<tr> <td data-sort="78">78</td> <td data-sort="OL">OL</td> <td data-sort="6-6">6-6</td> <td data-sort="325">325</td>  <td data-sort="SR">SR</td> <td data-sort="999"></td> <td class="textleft" data-sort="Creekside ">Creekside </td>  <td class="textleft" data-sort="86"> <span class="rating">86</span>  <span class="icon-starsolid yellow"></span>  <span class="icon-starsolid yellow"></span>  <span class="icon-starsolid yellow"></span>  </td> </tr>
<tr> <td data-sort="36">36</td> <td data-sort="DB">DB</td> <td data-sort="6-3">6-3</td> <td data-sort="180">180</td>  <td data-sort="JR">JR</td> <td data-sort="999"></td> <td class="textleft" data-sort="ZZZZZZ">-</td>  <td class="textleft" data-sort="0"> <span class="rating">NA</span>  </td> </tr>
<tr> <td data-sort="18">18</td> <td data-sort="QB">QB</td> <td data-sort="6-3">6-3</td> <td data-sort="220">220</td>  <td data-sort="JR">JR</td> <td data-sort="999"></td> <td class="textleft" data-sort="Riverview">Riverview</td>  <td class="textleft" data-sort="0"> <span class="rating">NA</span>  </td> </tr>
</table>
"""

SAMPLE_ROSTER_HTML = SAMPLE_NAME_TABLE + SAMPLE_DATA_TABLE


def test_parse_roster_page_extracts_linked_and_unlinked_names():
    players = fs.parse_roster_page(SAMPLE_ROSTER_HTML)
    names = [p.name for p in players]
    assert names == ["Matthew McCoy", "Takai Azziz", "Joe Borchers"]


def test_parse_roster_page_extracts_rating_and_star_count():
    players = fs.parse_roster_page(SAMPLE_ROSTER_HTML)
    mccoy = next(p for p in players if p.name == "Matthew McCoy")
    assert mccoy.rating == 86
    assert mccoy.stars == 3
    assert mccoy.position == "OL"
    assert mccoy.class_year == "SR"
    assert mccoy.high_school == "Creekside"


def test_parse_roster_page_handles_unrated_player_as_none_not_zero():
    players = fs.parse_roster_page(SAMPLE_ROSTER_HTML)
    azziz = next(p for p in players if p.name == "Takai Azziz")
    assert azziz.rating is None
    assert azziz.stars is None


def test_parse_roster_page_reads_high_school_from_text_not_sort_key():
    # Azziz's high_school data-sort is the sort-to-bottom sentinel
    # "ZZZZZZ" for an unknown school -- the displayed text is "-", which
    # must become None, never the literal sentinel string.
    players = fs.parse_roster_page(SAMPLE_ROSTER_HTML)
    azziz = next(p for p in players if p.name == "Takai Azziz")
    assert azziz.high_school is None


def test_parse_roster_page_raises_on_missing_tables():
    with pytest.raises(fs.TwoFortySevenFetchError, match="name-table/data-table"):
        fs.parse_roster_page("<html>nothing here</html>")


def test_parse_roster_page_raises_on_row_count_mismatch():
    # Drop the last data row so name-table (3 players) and data-table (2
    # players) can no longer be paired by row order.
    data_with_one_row_removed = SAMPLE_DATA_TABLE.split('<tr> <td data-sort="18"')[0] + "</table>\n"
    broken_html = SAMPLE_NAME_TABLE + data_with_one_row_removed
    with pytest.raises(fs.TwoFortySevenFetchError, match="can't reliably pair them"):
        fs.parse_roster_page(broken_html)


def test_team_slug_uses_alias_for_known_mismatch():
    assert fs._team_slug("Miami (OH)") == "miami-ohio"


def test_team_slug_falls_back_to_slugify():
    assert fs._team_slug("NC State") == "nc-state"
    assert fs._team_slug("Ole Miss") == "ole-miss"


def test_fetch_roster_uses_injected_browser_fetch_and_keys_by_lowercase_name():
    def fake_fetch(url, wait_for_selector=None):
        assert "miami" in url.lower()
        assert "2026" in url
        return SAMPLE_ROSTER_HTML

    roster = fs.fetch_roster("Miami", 2026, browser_fetch=fake_fetch)
    assert roster["matthew mccoy"].rating == 86
    assert "takai azziz" in roster


def test_fetch_roster_raises_typed_error_on_browser_failure():
    def failing_fetch(url, wait_for_selector=None):
        raise fs.TwoFortySevenFetchError("browser navigation failed: timeout")

    with pytest.raises(fs.TwoFortySevenFetchError, match="timeout"):
        fs.fetch_roster("Miami", 2026, browser_fetch=failing_fetch)
