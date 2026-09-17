"""Recruiting Talent Differential (DESIGN.md Section 4c/5) -- real per-player
recruiting ratings from 247Sports.com.

Confirmed live 2026-09-16: 247Sports sits behind the same class of
bot-detection puntandrally.com does -- a plain `requests` GET 403s (empty
body) -- but a plain, non-stealth `playwright.chromium.launch(headless=True)`
gets through cleanly (a real 356KB roster page, correct title), same as
puntandrally. No paywall on the data needed either -- a team's full current
roster with a per-player rating renders with no login. Because the browser
mechanics here are identical to puntandrally's (this is generically "launch
headless Chromium, navigate, wait for a selector," nothing puntandrally-
specific about it), this module reuses fetch_puntandrally's
`browser_session()`/`_import_playwright`/`_navigate_and_get_html` directly
rather than re-implementing them -- one shared browser process can serve
both sites in the same run_week.py run.

Team roster page, one fetch per team (no cross-referencing recruiting-class
archives or per-player profile pages needed -- a real, better-than-feared
result): `https://247sports.com/college/{slug}/Season/{year}-Football/Roster/`
lists the CURRENT roster with jersey/position/height/weight/class-year/age/
high-school AND a per-player composite rating, all at once.

Real markup (captured live against Miami's actual 2026 roster page): two
row-synchronized `<table>` elements -- `<table class="name-table"
data-id="name">` (one `<td class="name">` per player, a link to their
profile) and `<table data-id="data">` (jersey/POS/height/weight/Yr/Age/High
School/Rating, each cell's value also in a `data-sort="..."` attribute --
reliable for short codes, but NOT for text columns like High School, where
an unknown value's `data-sort` is a sort-to-bottom sentinel ("ZZZZZZ"), not
the displayed "-"; this module reads High School from the cell's own text,
never `data-sort`, for that reason). The Rating cell carries BOTH numbers at
once: `data-sort="93"` (0-100 composite) AND real star icons
(`<span class="icon-starsolid yellow"></span>` x star count) inside it. An
unrated/walk-on player shows `<span class="rating">NA</span>` with zero
star spans and `data-sort="0"` -- parsed as `rating=None, stars=None`, never
a fake zero standing in for "unrated."

Team slugs mostly match CFBD's name lowercased/hyphenated ("nc-state",
"ole-miss" both resolve correctly, confirmed live) but not always --
confirmed live: "Miami (OH)" is `miami-ohio` here, not `miami-oh` (which
404s cleanly -- 247Sports' own 404 page is unambiguous, unlike
puntandrally/ourlads, which needed guards against a silent wrong-page
fallback). TEAM_NAME_ALIASES below is seeded with only what's been checked
live so far -- run scripts/check_team_name_coverage.py's 247Sports check
(once added) before trusting this at full ~45-team/week scale, the same
discipline every other fetch_*.py module in this repo went through.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Optional
from urllib.parse import quote

from fetch_puntandrally import _import_playwright, _navigate_and_get_html, PuntAndRallyFetchError

TEAM_ROSTER_URL_TMPL = "https://247sports.com/college/{slug}/Season/{year}-Football/Roster/"
CONTENT_SELECTOR = 'table[data-id="data"]'

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)

# Confirmed live 2026-09-16. Left small and explicit rather than guessed at
# scale -- see module docstring on running the coverage check before trust.
TEAM_NAME_ALIASES: dict[str, str] = {
    "Miami (OH)": "miami-ohio",
}

_NAME_TABLE_PATTERN = re.compile(r'<table class="name-table" data-id="name">(.*?)</table>', re.S)
_DATA_TABLE_PATTERN = re.compile(r'<table data-id="data">(.*?)</table>', re.S)
_ROW_PATTERN = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S)
_NAME_CELL_PATTERN = re.compile(r'<a href="[^"]*">([^<]+)</a>|<span>([^<]+)</span>')
_TD_PATTERN = re.compile(r'<td[^>]*\bdata-sort="([^"]*)"[^>]*>(.*?)</td>', re.S)
_TAG_PATTERN = re.compile(r"<[^>]+>")
_STAR_ICON_PATTERN = re.compile(r"icon-starsolid")

# Column order in the data-table, confirmed live against the real header row.
_DATA_COLUMNS = ("jersey", "position", "height", "weight", "class_year", "age", "high_school", "rating")


class TwoFortySevenFetchError(RuntimeError):
    """Raised on a browser/network failure, or when the page's structure
    doesn't match what parsing expects (never returns a guessed/partial
    result silently)."""


@dataclass
class RecruitRating:
    name: str
    position: Optional[str]
    class_year: Optional[str]
    high_school: Optional[str]
    rating: Optional[int]  # 0-100 composite rating; None if unrated ("NA")
    stars: Optional[int]  # 0-5 star count from the real star icons; None if unrated


def _slugify(team: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", team.lower()).strip("-")


def _team_slug(team: str) -> str:
    return TEAM_NAME_ALIASES.get(team, _slugify(team))


def _to_text(html_fragment: str) -> str:
    return _TAG_PATTERN.sub("", html_fragment).strip()


def _browser_fetch_html(url: str, wait_for_selector: str = CONTENT_SELECTOR) -> str:
    """One-off fetch: launches Chromium, fetches a single page, closes it.
    Reuses fetch_puntandrally's Playwright launch mechanics directly (the
    browser-launching part is generic, nothing puntandrally-specific about
    it) rather than re-implementing them -- see module docstring. Pass a
    fetch_puntandrally.browser_session() fetch as `browser_fetch` for a
    multi-team batch, same shared browser process puntandrally fetches use."""
    playwright_error, sync_playwright = _import_playwright()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            return _navigate_and_get_html(browser, url, wait_for_selector, playwright_error)
        finally:
            browser.close()


def parse_roster_page(page_html: str) -> list:
    """Returns list[RecruitRating] in the page's own row order. Raises
    TwoFortySevenFetchError if the two tables can't be found or paired --
    never guesses at a name-to-stats pairing across a row-count mismatch."""
    name_match = _NAME_TABLE_PATTERN.search(page_html)
    data_match = _DATA_TABLE_PATTERN.search(page_html)
    if not name_match or not data_match:
        raise TwoFortySevenFetchError(
            "roster page's name-table/data-table not found -- page structure may have changed"
        )

    name_rows = _ROW_PATTERN.findall(name_match.group(1))[1:]  # [0] is the header row
    data_rows = _ROW_PATTERN.findall(data_match.group(1))[1:]
    if not name_rows or not data_rows:
        raise TwoFortySevenFetchError("roster page parsed to zero players -- page structure may have changed")
    if len(name_rows) != len(data_rows):
        raise TwoFortySevenFetchError(
            f"name-table has {len(name_rows)} player row(s) but data-table has {len(data_rows)} -- "
            "can't reliably pair them by row order, page structure may have changed"
        )

    players = []
    for name_row, data_row in zip(name_rows, data_rows):
        name_match_inner = _NAME_CELL_PATTERN.search(name_row)
        if not name_match_inner:
            continue  # a non-player row (e.g. a section divider), not every <tr> has a name cell
        # A player with no 247Sports recruiting profile page (common for a
        # walk-on) has their name in a plain <span>, not an <a href> link --
        # confirmed live (Miami's real 2026 roster: 18 of 115 players are
        # link-less this way). Both forms must be captured, not just the
        # linked one, or these players silently vanish from the roster.
        name = (name_match_inner.group(1) or name_match_inner.group(2)).strip()

        cells = _TD_PATTERN.findall(data_row)
        if len(cells) < len(_DATA_COLUMNS):
            continue  # malformed row -- skip rather than guess at missing columns
        values = dict(zip(_DATA_COLUMNS, cells))

        position = values["position"][0].strip() or None
        class_year = values["class_year"][0].strip() or None
        high_school = _to_text(values["high_school"][1]) or None
        if high_school == "-":
            high_school = None

        _rating_sort, rating_html = values["rating"]
        if "NA" in rating_html:
            rating, stars = None, None
        else:
            rating_text = _to_text(rating_html)
            rating = int(rating_text) if rating_text.isdigit() else None
            stars = len(_STAR_ICON_PATTERN.findall(rating_html))

        players.append(RecruitRating(
            name=name, position=position, class_year=class_year,
            high_school=high_school, rating=rating, stars=stars,
        ))
    return players


def fetch_roster(
    team: str,
    year: int,
    browser_fetch: Optional[Callable[..., str]] = None,
) -> dict:
    """Returns {name.lower(): RecruitRating} for one team's current roster.
    `browser_fetch` follows fetch_puntandrally's `(url, wait_for_selector=
    ...) -> html` contract -- pass its browser_session() fetch here to
    share one browser process across both sites in the same run."""
    slug = _team_slug(team)
    fetcher = browser_fetch or _browser_fetch_html
    url = TEAM_ROSTER_URL_TMPL.format(slug=quote(slug), year=year)
    try:
        page_html = fetcher(url, wait_for_selector=CONTENT_SELECTOR)
    except PuntAndRallyFetchError as exc:
        # The shared browser-fetch plumbing (see module docstring) raises
        # puntandrally's own error type on a navigation/timeout failure --
        # re-raise as this module's own type so callers only ever need to
        # catch TwoFortySevenFetchError, matching parse_roster_page's
        # already-documented contract.
        raise TwoFortySevenFetchError(f"browser fetch failed: {exc}") from exc

    players = parse_roster_page(page_html)
    return {p.name.lower(): p for p in players}


if __name__ == "__main__":
    import argparse
    import json

    parser = argparse.ArgumentParser(description="Fetch real recruiting ratings from 247Sports.com for one team.")
    parser.add_argument("team")
    parser.add_argument("--year", type=int, required=True)
    args = parser.parse_args()

    roster = fetch_roster(args.team, args.year)
    print(json.dumps({name: vars(p) for name, p in roster.items()}, indent=2))
