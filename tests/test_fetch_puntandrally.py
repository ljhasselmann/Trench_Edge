import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import fetch_puntandrally


# Real captured fragments from puntandrally.com's teamroster.php, live
# 2026-09-16 (Miami = 4-3 front, tags T/G/C/DE/DT; Wisconsin = 3-4 front,
# no DE tags at all, plus a long-snapper "(LS)" tagged row that shows up
# inside the Offensive Line section itself).
SAMPLE_MIAMI_OL_HTML = """
<div class="tr-section"><div class="tr-section-title">Offensive Line</div><div class="tr-section-body"><table class="tr-table"><thead><tr><th>Player</th></tr></thead><tbody>
<tr><td class="player player"><a href="/playersearch.php?name=Matthew+McCoy" title="View player profile">
            <span class="jersey-badge">
            <span class="num">#78</span>
            </span> Matthew McCoy (T) <span class="elig-badge elig-SR ">SR </span></a></td><td class="snaps usage-green">90 (64%)</td></tr>
<tr><td class="player player"><a href="/playersearch.php?name=Samson+Okunlola" title="View player profile">
            <span class="jersey-badge">
            <span class="num">#63</span>
            </span> Samson Okunlola (T) <span class="elig-badge elig-JR ">JR </span></a></td><td class="snaps usage-green">90 (64%)</td></tr>
<tr><td class="player player"><a href="/playersearch.php?name=Jacob+Hawks" title="View player profile">
            <span class="jersey-badge">
            <span class="num">#75</span>
            </span> Jacob Hawks (T) <span class="elig-badge elig-SO ">SO </span></a></td><td class="snaps usage-green-light">38 (27%)</td></tr>
<tr><td class="player player"><a href="/playersearch.php?name=Max+Buchanan" title="View player profile">
            <span class="jersey-badge">
            <span class="num">#66</span>
            </span> Max Buchanan (G) <span class="elig-badge elig-SO ">SO </span></a></td><td class="snaps usage-green">79 (56%)</td></tr>
<tr><td class="player player"><a href="/playersearch.php?name=Jackson+Cantwell" title="View player profile">
            <span class="jersey-badge">
            <span class="num">#79</span>
            </span> Jackson Cantwell (G) <span class="elig-badge elig-FR ">FR </span></a></td><td class="snaps usage-green">60 (43%)</td></tr>
<tr><td class="player player"><a href="/playersearch.php?name=Ryan+Rodriguez" title="View player profile">
            <span class="jersey-badge">
            <span class="num">#76</span>
            </span> Ryan Rodriguez (C) <span class="elig-badge elig-SR ">SR </span></a></td><td class="snaps usage-green">90 (64%)</td></tr>
<tr><td class="player player"><a href="/playersearch.php?name=Demetrius+Campbell" title="View player profile">
            <span class="jersey-badge">
            <span class="num">#72</span>
            </span> Demetrius Campbell (OL) <span class="elig-badge elig-FR ">FR </span></a></td><td class="snaps usage-red">&nbsp;</td></tr>
</tbody></table><div class="tr-cards-grid"><div class="tr-card">duplicate cards-grid view -- must not be double-counted</div></div></div></div>
"""

SAMPLE_MIAMI_DL_HTML = """
<div class="tr-section"><div class="tr-section-title">Defensive Line</div><div class="tr-section-body"><table class="tr-table"><thead><tr><th>Player</th></tr></thead><tbody>
<tr><td class="player player"><a href="/playersearch.php?name=Marquise+Lightfoot" title="View player profile">
            <span class="jersey-badge">
            <span class="num">#8</span>
            </span> Marquise Lightfoot (DE) <span class="elig-badge elig-JR ">JR </span></a></td><td class="snaps usage-green">69 (55%)</td></tr>
<tr><td class="player player"><a href="/playersearch.php?name=Ahmad+Moten+Sr." title="View player profile">
            <span class="jersey-badge">
            <span class="num">#99</span>
            </span> Ahmad Moten Sr. (DT) <span class="elig-badge elig-SR ">SR </span></a></td><td class="snaps usage-green">64 (61%)</td></tr>
<tr><td class="player player"><a href="/playersearch.php?name=Justin+Scott" title="View player profile">
            <span class="jersey-badge">
            <span class="num">#5</span>
            </span> Justin Scott (DT) <span class="elig-badge elig-JR ">JR </span></a></td><td class="snaps usage-green">62 (59%)</td></tr>
</tbody></table><div class="tr-cards-grid"></div></div></div>
"""

SAMPLE_WISCONSIN_OL_HTML = """
<div class="tr-section"><div class="tr-section-title">Offensive Line</div><div class="tr-section-body"><table class="tr-table"><thead><tr><th>Player</th></tr></thead><tbody>
<tr><td class="player player"><a href="/playersearch.php?name=Kevin+Heywood" title="View player profile">
            <span class="jersey-badge">
            <span class="num">#55</span>
            </span> Kevin Heywood (T) <span class="elig-badge elig-JR ">JR </span></a></td><td class="snaps usage-green">108 (93%)</td></tr>
<tr><td class="player player"><a href="/playersearch.php?name=James+Roe" title="View player profile">
            <span class="jersey-badge">
            <span class="num">#56</span>
            </span> James Roe (LS) <span class="elig-badge elig-JR ">JR </span></a></td><td class="snaps usage-red">&nbsp;</td></tr>
</tbody></table><div class="tr-cards-grid"></div></div></div>
"""

# Confirmed live: Wisconsin's (3-4 front) Defensive Line section has zero
# "(DE)" tagged rows at all -- everyone is DT or generic DL.
SAMPLE_WISCONSIN_DL_HTML = """
<div class="tr-section"><div class="tr-section-title">Defensive Line</div><div class="tr-section-body"><table class="tr-table"><thead><tr><th>Player</th></tr></thead><tbody>
<tr><td class="player player"><a href="/playersearch.php?name=Hammond+Russell+IV" title="View player profile">
            <span class="jersey-badge">
            <span class="num">#91</span>
            </span> Hammond Russell IV (DT) <span class="elig-badge elig-GR ">GR </span></a></td><td class="snaps usage-green">61 (49%)</td></tr>
<tr><td class="player player"><a href="/playersearch.php?name=Torin+Pettaway" title="View player profile">
            <span class="jersey-badge">
            <span class="num">#98</span>
            </span> Torin Pettaway (DL) <span class="elig-badge elig-FR ">FR </span></a></td><td class="snaps usage-red">&nbsp;</td></tr>
</tbody></table><div class="tr-cards-grid"></div></div></div>
"""


def test_parse_position_section_extracts_players_in_depth_order():
    section = fetch_puntandrally.parse_position_section(SAMPLE_MIAMI_OL_HTML, "Offensive Line", fetch_puntandrally.KNOWN_OL_TAGS)
    names = [p.name for p in section.players]
    assert names == [
        "Matthew McCoy", "Samson Okunlola", "Jacob Hawks",
        "Max Buchanan", "Jackson Cantwell", "Ryan Rodriguez", "Demetrius Campbell",
    ]
    assert not section.warnings


def test_parse_position_section_extracts_snaps_and_share():
    section = fetch_puntandrally.parse_position_section(SAMPLE_MIAMI_OL_HTML, "Offensive Line", fetch_puntandrally.KNOWN_OL_TAGS)
    mccoy = next(p for p in section.players if p.name == "Matthew McCoy")
    assert mccoy.snaps == 90
    assert mccoy.snap_share_pct == 64.0
    assert mccoy.position_tag == "T"


def test_parse_position_section_extracts_jersey_and_class_year():
    section = fetch_puntandrally.parse_position_section(SAMPLE_MIAMI_OL_HTML, "Offensive Line", fetch_puntandrally.KNOWN_OL_TAGS)
    mccoy = next(p for p in section.players if p.name == "Matthew McCoy")
    assert mccoy.jersey == "78"
    assert mccoy.class_year == "SR"

    okunlola = next(p for p in section.players if p.name == "Samson Okunlola")
    assert okunlola.jersey == "63"
    assert okunlola.class_year == "JR"


def test_parse_position_section_handles_blank_snaps_as_none_not_zero():
    section = fetch_puntandrally.parse_position_section(SAMPLE_MIAMI_OL_HTML, "Offensive Line", fetch_puntandrally.KNOWN_OL_TAGS)
    campbell = next(p for p in section.players if p.name == "Demetrius Campbell")
    assert campbell.snaps is None
    assert campbell.snap_share_pct is None


def test_parse_position_section_ignores_cards_grid_duplicate():
    section = fetch_puntandrally.parse_position_section(SAMPLE_MIAMI_OL_HTML, "Offensive Line", fetch_puntandrally.KNOWN_OL_TAGS)
    assert len(section.players) == 7  # not doubled by the cards-grid view of the same players


def test_parse_position_section_flags_unrecognized_tag_as_warning_not_silent_drop():
    section = fetch_puntandrally.parse_position_section(SAMPLE_WISCONSIN_OL_HTML, "Offensive Line", fetch_puntandrally.KNOWN_OL_TAGS)
    names = [p.name for p in section.players]
    assert names == ["Kevin Heywood"]  # James Roe (LS) excluded, not silently kept as OL
    assert len(section.warnings) == 1
    assert "James Roe" in section.warnings[0]
    assert "LS" in section.warnings[0]


def test_parse_position_section_dl_handles_scheme_with_no_edge_tag():
    # Wisconsin's 3-4 front: no "(DE)" rows at all, just DT/DL -- must not
    # raise or warn just because a scheme lacks a tag Miami's does have.
    section = fetch_puntandrally.parse_position_section(SAMPLE_WISCONSIN_DL_HTML, "Defensive Line", fetch_puntandrally.KNOWN_DL_TAGS)
    tags = {p.position_tag for p in section.players}
    assert tags == {"DT", "DL"}
    assert not section.warnings


def test_parse_position_section_raises_on_missing_section():
    with pytest.raises(fetch_puntandrally.PuntAndRallyFetchError, match="Special Teams"):
        fetch_puntandrally.parse_position_section(SAMPLE_MIAMI_OL_HTML, "Special Teams", fetch_puntandrally.KNOWN_OL_TAGS)


def test_fetch_roster_uses_injected_browser_fetch_not_real_playwright():
    combined_html = SAMPLE_MIAMI_OL_HTML + SAMPLE_MIAMI_DL_HTML

    def fake_fetch(url):
        assert "Miami" in url
        return combined_html

    ol_section, dl_section = fetch_puntandrally.fetch_roster("Miami", 2026, browser_fetch=fake_fetch)
    assert [p.name for p in ol_section.players][:2] == ["Matthew McCoy", "Samson Okunlola"]
    assert [p.name for p in dl_section.players] == ["Marquise Lightfoot", "Ahmad Moten Sr.", "Justin Scott"]


def test_fetch_roster_puts_year_in_the_url():
    seen_urls = []

    def fake_fetch(url):
        seen_urls.append(url)
        return SAMPLE_MIAMI_OL_HTML + SAMPLE_MIAMI_DL_HTML

    fetch_puntandrally.fetch_roster("Miami", 2025, browser_fetch=fake_fetch)
    assert "year=2025" in seen_urls[0]

    fetch_puntandrally.fetch_roster("Miami", 2026, browser_fetch=fake_fetch)
    assert "year=2026" in seen_urls[1]


def test_resolve_truncated_name_matches_unique_initial_and_surname():
    candidates = ["Malcolm Alcorn-Crowder", "Christian Davis", "Ira Singleton"]
    assert fetch_puntandrally.resolve_truncated_name("M. Alcorn-Crowder", candidates) == "Malcolm Alcorn-Crowder"


def test_resolve_truncated_name_returns_none_when_ambiguous():
    # Two candidates share the initial "M" and surname "Smith" -- must
    # never guess which one a truncated "M. Smith" actually refers to.
    candidates = ["Marcus Smith", "Michael Smith"]
    assert fetch_puntandrally.resolve_truncated_name("M. Smith", candidates) is None


def test_resolve_truncated_name_returns_none_when_no_match():
    candidates = ["Christian Davis", "Ira Singleton"]
    assert fetch_puntandrally.resolve_truncated_name("M. Alcorn-Crowder", candidates) is None


def test_resolve_truncated_name_returns_none_for_a_normal_full_name():
    # Not every short name is a truncation -- a real "Al Smith" shouldn't
    # be treated as an initial just because it's two words.
    assert fetch_puntandrally.resolve_truncated_name("Christian Davis", ["Christian Davis"]) is None


def test_resolve_name_variant_matches_dropped_generational_suffix():
    candidates = ["Mike Wallace Jr.", "Christian Davis"]
    assert fetch_puntandrally.resolve_name_variant("Mike Wallace", candidates) == "Mike Wallace Jr."


def test_resolve_name_variant_matches_stripped_accent_marks():
    candidates = ["André Otto", "Christian Davis"]
    assert fetch_puntandrally.resolve_name_variant("Andre Otto", candidates) == "André Otto"


def test_resolve_name_variant_returns_none_when_ambiguous():
    candidates = ["Mike Wallace Jr.", "Mike Wallace Sr."]
    assert fetch_puntandrally.resolve_name_variant("Mike Wallace", candidates) is None


def test_resolve_name_variant_returns_none_when_no_match():
    assert fetch_puntandrally.resolve_name_variant("Mike Wallace", ["Christian Davis"]) is None


def test_resolve_any_name_match_forward_truncation():
    assert fetch_puntandrally.resolve_any_name_match("M. Alcorn-Crowder", ["Malcolm Alcorn-Crowder"]) == "Malcolm Alcorn-Crowder"


def test_resolve_any_name_match_forward_suffix_variant():
    assert fetch_puntandrally.resolve_any_name_match("Mike Wallace", ["Mike Wallace Jr."]) == "Mike Wallace Jr."


def test_resolve_any_name_match_reverse_direction():
    # `name` here is the FULLER form (e.g. an ourlads-sourced name); the
    # candidate is the one that's truncated/suffix-dropped -- the
    # opposite direction from the other two tests.
    assert fetch_puntandrally.resolve_any_name_match("Malcolm Alcorn-Crowder", ["M. Alcorn-Crowder"]) == "M. Alcorn-Crowder"
    assert fetch_puntandrally.resolve_any_name_match("Mike Wallace Jr.", ["Mike Wallace"]) == "Mike Wallace"


def test_resolve_any_name_match_exact():
    assert fetch_puntandrally.resolve_any_name_match("Christian Davis", ["Christian Davis", "Ira Singleton"]) == "Christian Davis"


def test_resolve_any_name_match_returns_none_when_no_match():
    assert fetch_puntandrally.resolve_any_name_match("Nobody Here", ["Christian Davis"]) is None


def test_browser_session_reuses_one_browser_across_multiple_fetches(monkeypatch):
    # Can't launch a real browser in a unit test; verify the efficiency
    # contract instead -- chromium.launch() is called exactly once for
    # the whole `with` block, no matter how many fetch() calls happen
    # inside it, and each fetch() reuses that same browser object rather
    # than relaunching.
    launch_calls = []
    fetch_calls = []

    class FakeBrowser:
        def new_page(self, user_agent=None):
            raise fetch_puntandrally.PuntAndRallyFetchError("should not navigate in this test")

        def close(self):
            pass

    class FakeChromium:
        def launch(self, headless=True):
            launch_calls.append(headless)
            return FakeBrowser()

    class FakePlaywrightContext:
        def __enter__(self):
            return type("P", (), {"chromium": FakeChromium()})()

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(fetch_puntandrally, "_navigate_and_get_html", lambda browser, url, sel, err: fetch_calls.append(url) or "<html></html>")
    monkeypatch.setattr(fetch_puntandrally, "_import_playwright", lambda: (Exception, lambda: FakePlaywrightContext()))

    with fetch_puntandrally.browser_session() as fetch:
        fetch("https://www.puntandrally.com/teamroster.php?team=Miami")
        fetch("https://www.puntandrally.com/teamroster.php?team=Wisconsin")

    assert launch_calls == [True]  # chromium.launch() called exactly once for both fetches
    assert len(fetch_calls) == 2


def test_fetch_roster_raises_typed_error_when_browser_fetch_fails():
    def failing_fetch(url):
        raise fetch_puntandrally.PuntAndRallyFetchError("browser navigation failed: timeout")

    with pytest.raises(fetch_puntandrally.PuntAndRallyFetchError, match="timeout"):
        fetch_puntandrally.fetch_roster("Miami", 2026, browser_fetch=failing_fetch)


# Real captured fragment from puntandrally.com's teamsgrid.php?geturl=roster,
# live 2026-09-16 -- includes "Texas A&amp;M" to cover the HTML-entity
# unescaping fetch_team_index needs (a naive regex extraction wrongly
# flags this as a team-name mismatch against CFBD's "Texas A&M").
SAMPLE_TEAM_INDEX_HTML = """
<a href="/teamroster.php?team=Alabama"><img class="np-team-logo" src="x.png" alt="Alabama" loading="lazy"><span class="np-team-name">Alabama</span></a>
<a href="/teamroster.php?team=Texas+A%26M"><img class="np-team-logo" src="x.png" alt="Texas A&amp;M" loading="lazy"><span class="np-team-name">Texas A&amp;M</span></a>
"""


def test_fetch_team_index_unescapes_html_entities_in_team_names():
    index = fetch_puntandrally.fetch_team_index(browser_fetch=lambda url, **kwargs: SAMPLE_TEAM_INDEX_HTML)
    assert index == {"Alabama", "Texas A&M"}


def test_fetch_team_index_raises_on_empty_parse():
    with pytest.raises(fetch_puntandrally.PuntAndRallyFetchError, match="zero teams"):
        fetch_puntandrally.fetch_team_index(browser_fetch=lambda url, **kwargs: "<html>nothing here</html>")


def test_fetch_snap_history_sums_snaps_per_player_across_years():
    fetch_calls = []

    def fake_fetch_roster(team, year, browser_fetch=None):
        fetch_calls.append(year)
        html_by_year = {
            2026: SAMPLE_MIAMI_OL_HTML,  # McCoy 90, Okunlola 90, Hawks 38, Buchanan 79, Cantwell 60, Rodriguez 90, Campbell None
            2025: SAMPLE_WISCONSIN_OL_HTML,  # Heywood 108 (different player, no overlap with 2026 sample)
        }
        html = html_by_year.get(year)
        if html is None:
            raise fetch_puntandrally.PuntAndRallyFetchError(f"no fixture for {year}")
        return fetch_puntandrally.parse_position_section(html, "Offensive Line", fetch_puntandrally.KNOWN_OL_TAGS), fetch_puntandrally.RosterSection()

    import unittest.mock
    with unittest.mock.patch.object(fetch_puntandrally, "fetch_roster", fake_fetch_roster):
        history = fetch_puntandrally.fetch_snap_history("Miami", through_year=2026, years=2)

    assert fetch_calls == [2025, 2026]
    assert history.years_fetched == [2025, 2026]
    assert history.totals["Matthew McCoy"] == 90
    assert history.totals["Kevin Heywood"] == 108
    assert "Demetrius Campbell" not in history.totals  # snaps=None contributes nothing, not a KeyError
    assert history.warnings == []


def test_fetch_snap_history_skips_failed_year_with_warning_not_hard_failure():
    def fake_fetch_roster(team, year, browser_fetch=None):
        if year == 2024:
            raise fetch_puntandrally.PuntAndRallyFetchError("predates reliability floor")
        return (
            fetch_puntandrally.parse_position_section(SAMPLE_MIAMI_OL_HTML, "Offensive Line", fetch_puntandrally.KNOWN_OL_TAGS),
            fetch_puntandrally.RosterSection(),
        )

    import unittest.mock
    with unittest.mock.patch.object(fetch_puntandrally, "fetch_roster", fake_fetch_roster):
        history = fetch_puntandrally.fetch_snap_history("Miami", through_year=2026, years=3)

    assert history.years_fetched == [2025, 2026]  # 2024 excluded
    assert history.totals["Matthew McCoy"] == 180  # counted twice (2025 + 2026), not three times
    assert len(history.warnings) == 1
    assert "2024" in history.warnings[0]


def test_fetch_team_index_passes_its_own_content_selector_to_the_fetcher():
    # fetch_team_index's page has no ".tr-section-title" -- it must ask
    # for its own selector, not the roster-page default, whether using
    # the one-off default fetcher or an injected browser_session fetch.
    seen = {}

    def fake_fetch(url, wait_for_selector=None):
        seen["wait_for_selector"] = wait_for_selector
        return SAMPLE_TEAM_INDEX_HTML

    fetch_puntandrally.fetch_team_index(browser_fetch=fake_fetch)
    assert seen["wait_for_selector"] == fetch_puntandrally.TEAM_INDEX_SELECTOR
