"""Mass -- live rosters + snap counts from puntandrally.com (supplements
DESIGN.md 4b). Wired into run_week.py as the primary roster source, and
into fetch_talent.py's Experience metric via its year-parameterized fetch.

Unlike ourlads.com (src/fetch_ourlads.py, a plain server-rendered site),
puntandrally.com sits behind Cloudflare's managed JS challenge on every
page -- confirmed live 2026-09-16: a plain `requests` GET to
teamroster.php returns HTTP 403 and a "Just a moment..." challenge page,
not the roster. Plain `requests` cannot solve that challenge; getting the
real page requires an actual browser executing real JS, so this module
drives headless Chromium via Playwright instead of `requests`. Confirmed
live that this needs no stealth/anti-detection trickery -- a default
`playwright.chromium.launch(headless=True)` with a normal desktop Chrome
UA gets the full rendered page (matches page length fetched with a
visible browser almost exactly: 303,702 vs 303,696 chars). This is a
materially heavier dependency than every other fetch_*.py module in this
repo (a real browser binary, not just `requests`) -- deliberate, following
explicit direction not to attempt any Cloudflare-bypass shortcut.

The page is server-rendered (confirmed live: the roster HTML is present
in the direct response to the `teamroster.php` GET itself -- no separate
XHR/JSON call happens after load), but it renders progressively: the DOM
right after `page.goto()` returns is an ~12KB skeleton; the full ~300KB
roster only appears a few seconds later once Cloudflare's challenge
clears and the page hydrates. `_browser_fetch_html` waits on a real
content selector (`.tr-section-title`) rather than a fixed sleep, but the
underlying `page.goto()` + challenge + hydration cycle is still the
dominant cost per team -- expect this to run markedly slower per team
than fetch_ourlads.py's plain HTTP GET.

Team names: puntandrally's team-roster grid
(https://www.puntandrally.com/teamsgrid.php?geturl=roster) lists all 138
FBS teams, and -- confirmed live by comparing the full list against
CFBD's canonical spellings -- every one of them already matches CFBD
directly, including every name ourlads.com itself needed an alias for
("NC State", "Ole Miss", "Hawai'i", "San José State", "UL Monroe", "Miami
(OH)"). The one apparent miss, "Texas A&M", isn't a real spelling
mismatch -- the site's HTML literally has "Texas A&amp;M" and
fetch_team_index() unescapes that; a raw-regex extraction that skips
unescaping will wrongly flag it. TEAM_NAME_ALIASES is left as an empty
dict rather than removed, so a real future mismatch (a spelling change on
puntandrally's end) has somewhere to go without restructuring the module.

Roster page structure (confirmed live against Miami's and Wisconsin's
pages, 2026-09-16): each position group is a `<div class="tr-section">`
with a `<div class="tr-section-title">` (e.g. "Offensive Line",
"Defensive Line") followed by BOTH a `<table class="tr-table">` and a
`<div class="tr-cards-grid">` rendering of the *same* players -- only the
`<table>` is parsed here; the cards grid is a duplicate, CSS-toggled
alternate view, not additional data. Within the table, each player's
`<td class="player">` cell holds "#<jersey> <Name> (<TAG>) <class-year>",
and a `<td class="snaps ...">` cell holds "<snaps> (<share>%)" when the
player has recorded snaps, or a blank/`&nbsp;` cell when they don't (a
true-freshman/unproven backup with no game snaps yet -- not a parse
failure).

Position tags are NOT what fetch_ourlads.py sees. ourlads gives an
explicit per-slot row label (LT, RT, LG, RG, C); puntandrally only tags
"T" (both tackles pooled together), "G" (both guards pooled), or "C" --
plus a generic "OL" tag for unestablished linemen with no recorded snaps
at any specific spot yet. So `starters_for_group` here ranks by snap
count *within* each tag and takes the top N for that tag (2 for "T", 2
for "G", 1 for "C" -- OL_STARTER_COUNTS), rather than ourlads' "first
listed" per an already-specific slot label.

DL tagging is looser still, and this is a genuine, only partly-verified
coverage gap (same class of problem fetch_ourlads.py's own docstring
flags for 3-man vs 4-man fronts): Miami (a 4-3 team) tags edge rushers
"DE" and interior linemen "DT"/"DL". Wisconsin (a 3-4 team) had ZERO
"DE"-tagged players anywhere in its Defensive Line section -- confirmed
live -- everyone there was "DT" or generic "DL". That almost certainly
means puntandrally counts a 3-4 team's edge rushers under Linebackers
instead of Defensive Line, but this module does NOT verify that by
checking Wisconsin's Linebackers section for edge-rusher-shaped snap/
pass-rush stats -- so treat "DL front size looks smaller for 3-4 teams"
as a known, named risk to check before trusting DL starter counts at
full scale, not a confirmed non-issue. Because of this, DL_STARTER_COUNTS
is deliberately NOT defined the way OL_STARTER_COUNTS is -- there's no
safe fixed (DE, DT) split that holds across both scheme families; a
caller must decide per-team front size before calling starters_for_group
for DL, which is exactly the kind of judgment call this module leaves to
its caller rather than guessing.

Any row whose parenthesized tag falls outside the section's own known set
(KNOWN_OL_TAGS / KNOWN_DL_TAGS) -- e.g. long snappers tagged "(LS)" show
up INSIDE Wisconsin's "Offensive Line" section, confirmed live -- is
excluded from that section's parsed rows and reported in `warnings`
rather than silently miscounted as a lineman.

Multi-year snap data: confirmed live 2026-09-16 that `teamroster.php`
takes a `year=` query param and returns that season's real, accurate
FULL-SEASON snap totals for a completed year (e.g. Miami's 2025 Carson
Beck: 1033 snaps; 2024 Cam Ward: 868 snaps -- both correct against known
real stats) or in-progress totals for the current year. Data quality
holds back to at least 2022 (verified: real Offensive Line section, real
snap data); 2020 is degraded (duplicate entries, no snap data at all) --
treat "reliable" as roughly 2022-present, not further back without
re-verifying. `fetch_roster`'s `year` param is required (no default) so a
caller can never accidentally fetch the wrong season silently.
"""

from __future__ import annotations

import html
import re
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Callable, Optional
from urllib.parse import quote

TEAM_ROSTER_URL_TMPL = "https://www.puntandrally.com/teamroster.php?year={year}&team={team}"
TEAM_INDEX_URL = "https://www.puntandrally.com/teamsgrid.php?geturl=roster"
TEAM_INDEX_SELECTOR = ".np-team-name"

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)
NAVIGATION_TIMEOUT_MS = 30_000
CONTENT_SELECTOR_TIMEOUT_MS = 20_000
CONTENT_SELECTOR = ".tr-section-title"

KNOWN_OL_TAGS = {"T", "G", "C", "OL"}
KNOWN_DL_TAGS = {"DE", "DT", "DL"}

# Standard 5-man offensive line: 2 tackles, 2 guards, 1 center. See module
# docstring -- there is no equivalent fixed split for DL (front size
# varies by scheme), so callers must size that themselves per team.
OL_STARTER_COUNTS = {"T": 2, "G": 2, "C": 1}

# Flat top-N-by-snaps count for DL, regardless of DE/DT/DL tag -- the same
# simplification run_week.py used to keep privately; promoted here so
# fetch_talent.py's Experience computation and run_week.py's roster-file
# writer can never disagree about who counts as this year's DL starters.
DL_STARTER_COUNT = 4

# Confirmed live 2026-09-16 against puntandrally's own team-roster grid --
# see module docstring. Left as an empty dict (not removed) so a future
# real mismatch has somewhere to go.
TEAM_NAME_ALIASES: dict[str, str] = {}

_SECTION_TABLE_PATTERN_TMPL = r'<div class="tr-section-title">{title}</div>.*?<table class="tr-table">(.*?)</table>'
_TBODY_PATTERN = re.compile(r"<tbody>(.*?)</tbody>", re.S)
_ROW_PATTERN = re.compile(r"<tr>(.*?)</tr>", re.S)
_PLAYER_CELL_PATTERN = re.compile(
    r'<td class="player[^"]*">.*?<span class="num">#\d+</span>\s*</span>\s*'
    r"([^<(]+?)\s*(?:\(([A-Z]+)\))?\s*<span class=\"elig-badge",
    re.S,
)
_SNAPS_CELL_PATTERN = re.compile(r'<td class="snaps[^"]*">([^<]*)</td>')
_SNAPS_VALUE_PATTERN = re.compile(r"(\d+)\s*\((\d+)%\)")
_TEAM_INDEX_PATTERN = re.compile(r'<a href="/teamroster\.php\?team=[^"]+"><img[^>]*><span class="np-team-name">([^<]+)</span></a>')


class PuntAndRallyFetchError(RuntimeError):
    """Raised on a browser/network failure, or when the page's structure
    doesn't match what parsing expects (never returns a guessed/partial
    result silently)."""


@dataclass
class PlayerSnaps:
    name: str
    position_tag: str
    snaps: Optional[int]
    snap_share_pct: Optional[float]


@dataclass
class RosterSection:
    players: list = field(default_factory=list)  # list[PlayerSnaps], depth order as returned by the site
    warnings: list = field(default_factory=list)  # list[str]


def _import_playwright():
    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise PuntAndRallyFetchError(
            "playwright is required to fetch puntandrally.com (it sits behind a Cloudflare JS "
            "challenge plain `requests` can't solve) -- install it and its browser via "
            "`pip install playwright && playwright install chromium`"
        ) from exc
    return PlaywrightError, sync_playwright


def _navigate_and_get_html(browser, url: str, wait_for_selector: str, playwright_error) -> str:
    try:
        page = browser.new_page(user_agent=DEFAULT_USER_AGENT)
        try:
            page.goto(url, timeout=NAVIGATION_TIMEOUT_MS)
            page.wait_for_selector(wait_for_selector, timeout=CONTENT_SELECTOR_TIMEOUT_MS)
            return page.content()
        finally:
            page.close()
    except playwright_error as exc:
        raise PuntAndRallyFetchError(f"browser navigation to {url!r} failed: {exc}") from exc


def _browser_fetch_html(url: str, wait_for_selector: str = CONTENT_SELECTOR) -> str:
    """One-off fetch: launches Chromium, fetches a single page, closes it.
    Fine for a single team lookup (the CLI below), but launching a fresh
    browser process per call is real overhead (several seconds on top of
    the Cloudflare challenge itself) -- a multi-team batch should use
    browser_session() instead so one browser process serves every fetch."""
    playwright_error, sync_playwright = _import_playwright()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            return _navigate_and_get_html(browser, url, wait_for_selector, playwright_error)
        finally:
            browser.close()


@contextmanager
def browser_session():
    """One Chromium process, reused across many fetch() calls -- for a
    week's ~45-team roster batch, this is the difference between
    launching Chromium once vs. 45 times. Yields a
    `fetch(url, wait_for_selector=CONTENT_SELECTOR) -> str` callable with
    the same signature `_browser_fetch_html` has, suitable for
    fetch_roster's/fetch_team_index's `browser_fetch` param:

        with browser_session() as fetch:
            for team in teams:
                ol_section, dl_section = fetch_roster(team, browser_fetch=fetch)
    """
    playwright_error, sync_playwright = _import_playwright()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            def fetch(url: str, wait_for_selector: str = CONTENT_SELECTOR) -> str:
                return _navigate_and_get_html(browser, url, wait_for_selector, playwright_error)

            yield fetch
        finally:
            browser.close()


def _to_first_last_or_full(raw: str) -> str:
    """puntandrally already formats players as 'First Last' (unlike
    ourlads' 'Last, First'), so this is just whitespace cleanup."""
    return re.sub(r"\s+", " ", raw).strip()


def parse_position_section(page_html: str, section_title: str, known_tags: set) -> RosterSection:
    table_match = re.search(_SECTION_TABLE_PATTERN_TMPL.format(title=re.escape(section_title)), page_html, re.S)
    if not table_match:
        raise PuntAndRallyFetchError(
            f"{section_title!r} section (or its table) not found on the page -- page structure may have changed"
        )
    tbody_match = _TBODY_PATTERN.search(table_match.group(1))
    if not tbody_match:
        raise PuntAndRallyFetchError(f"{section_title!r} section's table has no <tbody> -- page structure may have changed")

    result = RosterSection()
    for row_html in _ROW_PATTERN.findall(tbody_match.group(1)):
        player_match = _PLAYER_CELL_PATTERN.search(row_html)
        if not player_match:
            continue  # a comment-only or otherwise non-player row; not every <tr> in the source has a player cell
        name = _to_first_last_or_full(player_match.group(1))
        tag = player_match.group(2)
        if not tag or tag not in known_tags:
            result.warnings.append(
                f"{name!r} in {section_title!r} section has an unrecognized position tag {tag!r} -- excluded from parsed rows"
            )
            continue

        snaps_match = _SNAPS_CELL_PATTERN.search(row_html)
        snaps: Optional[int] = None
        snap_share_pct: Optional[float] = None
        if snaps_match:
            value_match = _SNAPS_VALUE_PATTERN.search(snaps_match.group(1))
            if value_match:
                snaps = int(value_match.group(1))
                snap_share_pct = float(value_match.group(2))

        result.players.append(PlayerSnaps(name=name, position_tag=tag, snaps=snaps, snap_share_pct=snap_share_pct))

    if not result.players and not result.warnings:
        raise PuntAndRallyFetchError(
            f"{section_title!r} section parsed to zero players -- page structure may have changed"
        )
    return result


def fetch_team_index(browser_fetch: Optional[Callable[..., str]] = None) -> set:
    """Every team name puntandrally lists on its own roster grid --
    confirmed live to cover all 138 FBS teams, all matching CFBD's
    canonical spelling directly (see module docstring). Used by
    scripts/check_team_name_coverage.py, not by fetch_roster itself
    (which doesn't need a lookup since the URL just takes a team name).
    `browser_fetch` follows the same `(url, wait_for_selector=...) -> str`
    contract as fetch_roster's -- a browser_session() fetch works here too,
    so a caller can build the index and fetch every team's roster under
    one shared browser process."""
    fetcher = browser_fetch or _browser_fetch_html
    page_html = fetcher(TEAM_INDEX_URL, wait_for_selector=TEAM_INDEX_SELECTOR)
    index = {html.unescape(name).strip() for name in _TEAM_INDEX_PATTERN.findall(page_html)}
    if not index:
        raise PuntAndRallyFetchError("puntandrally team index parsed to zero teams -- page structure may have changed")
    return index


def fetch_roster(
    team: str,
    year: int,
    browser_fetch: Optional[Callable[..., str]] = None,
) -> tuple:
    """Returns (ol_section, dl_section) as RosterSections for the given
    season -- `year` is required (no default) so a caller can never
    accidentally fetch the wrong season silently; see module docstring for
    the confirmed-live multi-year support and its ~2022 reliability floor.
    `browser_fetch` is a pluggable `(url, wait_for_selector=...) -> html`
    callable (defaults to a one-off real Playwright launch); tests inject a
    fake one instead of requests.Session, since this module isn't
    `requests`-based -- see module docstring. Pass a browser_session()
    fetch here for a multi-team batch, instead of the default, to reuse
    one browser process across every call."""
    site_name = TEAM_NAME_ALIASES.get(team, team)
    fetcher = browser_fetch or _browser_fetch_html
    url = TEAM_ROSTER_URL_TMPL.format(year=year, team=quote(site_name))
    page_html = fetcher(url)

    ol_section = parse_position_section(page_html, "Offensive Line", KNOWN_OL_TAGS)
    dl_section = parse_position_section(page_html, "Defensive Line", KNOWN_DL_TAGS)
    return ol_section, dl_section


def starters_for_group(players: list, starter_counts: dict) -> list:
    """Top `starter_counts[tag]` names by snap count, per tag. A player
    with no recorded snaps yet (None) sorts last within its tag -- never
    guessed into a starting spot ahead of someone with real usage."""
    by_tag: dict = {}
    for player in players:
        by_tag.setdefault(player.position_tag, []).append(player)

    starters = []
    for tag, count in starter_counts.items():
        ranked = sorted(by_tag.get(tag, []), key=lambda p: p.snaps if p.snaps is not None else -1, reverse=True)
        starters.extend(p.name for p in ranked[:count])
    return starters


def dl_starters_for_group(players: list, count: int = DL_STARTER_COUNT) -> list:
    """Top `count` DL players by snap count, regardless of DE/DT/DL tag.
    puntandrally's DL tags are too coarse to split by slot the way
    starters_for_group does for OL -- front size genuinely varies by
    scheme (a 4-3's 4 down linemen vs. a 3-4's 3), and this module
    deliberately declines to guess a fixed split (see module docstring).
    A flat top-N by usage is the simplification every caller of this
    module shares -- both config/rosters/{team}.yaml's DL list and the
    Experience metric's DL snap-share lookup use this exact function, so
    they can never disagree about who counts as this year's DL starters."""
    ranked = sorted(players, key=lambda p: p.snaps if p.snaps is not None else -1, reverse=True)
    return [p.name for p in ranked[:count]]


if __name__ == "__main__":
    import argparse
    import json

    parser = argparse.ArgumentParser(description="Fetch live roster + snap counts from puntandrally.com for one team.")
    parser.add_argument("team")
    parser.add_argument("--year", type=int, required=True)
    args = parser.parse_args()

    ol_section, dl_section = fetch_roster(args.team, args.year)
    print(json.dumps({
        "team": args.team,
        "year": args.year,
        "OL": {
            "players": [vars(p) for p in ol_section.players],
            "starters": starters_for_group(ol_section.players, OL_STARTER_COUNTS),
            "warnings": ol_section.warnings,
        },
        "DL": {
            "players": [vars(p) for p in dl_section.players],
            "starters": dl_starters_for_group(dl_section.players),
            "warnings": dl_section.warnings,
        },
    }, indent=2))
