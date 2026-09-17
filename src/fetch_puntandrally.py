"""Mass -- live rosters + snap counts from puntandrally.com (supplements
DESIGN.md 4b). Wired into run_week.py as a by-name ENRICHMENT source on
top of fetch_ourlads.py's authoritative depth chart (jersey, class_year,
snaps_multi_year), and into fetch_talent.py's Experience metric via its
year-parameterized fetch. This module briefly stood in as the primary
depth-chart source itself (its top-N-by-snaps ranking approximating "who
starts") before that was confirmed live to misrepresent a real starter's
actual slot -- see DESIGN.md Section 4b and fetch_ourlads.py.

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

Position tags are NOT what fetch_ourlads.py sees, and are no longer used
to decide who starts (see the module-docstring note above) -- they're
only carried through for reference. ourlads gives an explicit per-slot
row label (LT, RT, LG, RG, C); puntandrally only tags "T" (both tackles
pooled together), "G" (both guards pooled), or "C" -- plus a generic "OL"
tag for unestablished linemen with no recorded snaps at any specific spot
yet. DL tagging is looser still: Miami (a 4-3 team) tags edge rushers "DE"
and interior linemen "DT"/"DL"; Wisconsin (a 3-4 team) had ZERO
"DE"-tagged players anywhere in its Defensive Line section -- confirmed
live -- everyone there was "DT" or generic "DL", almost certainly because
puntandrally counts a 3-4 team's edge rushers under Linebackers instead.

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
import os
import re
import shutil
import subprocess
import tempfile
import unicodedata
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Callable, Optional
from urllib.parse import quote

_NSS_SEEDED = False


def _seed_chromium_nss_from_env() -> None:
    """One-time per process: seed ~/.pki/nssdb with every PEM cert from the
    CA bundle pointed to by SSL_CERT_FILE or REQUESTS_CA_BUNDLE, so headless
    Chromium on Linux trusts TLS-intercepting proxies that requests/curl
    already trust via those env vars. Chromium reads that NSS db for CA
    trust on Linux — but it doesn't pick up SSL_CERT_FILE/REQUESTS_CA_BUNDLE
    on its own, hence ERR_CERT_AUTHORITY_INVALID in environments with an
    egress proxy whose CA is only in those env-var bundles.

    Silent no-op when: neither env var is set, the file doesn't exist,
    certutil isn't on PATH (libnss3-tools not installed), or any step
    fails. A seeding failure leaves Chromium's trust store unchanged —
    callers handle the resulting navigation errors as before, unchanged."""
    global _NSS_SEEDED
    if _NSS_SEEDED:
        return
    _NSS_SEEDED = True  # set before any exception so we don't retry on failure

    ca_bundle = os.environ.get("SSL_CERT_FILE") or os.environ.get("REQUESTS_CA_BUNDLE")
    if not ca_bundle or not os.path.exists(ca_bundle):
        return
    certutil = shutil.which("certutil")
    if certutil is None:
        return

    try:
        nss_dir = os.path.expanduser("~/.pki/nssdb")
        os.makedirs(nss_dir, exist_ok=True)
        db_arg = f"sql:{nss_dir}"
        if not os.path.exists(os.path.join(nss_dir, "cert9.db")):
            subprocess.run(
                [certutil, "-N", "-d", db_arg, "--empty-password"],
                check=False, capture_output=True, timeout=15,
            )
        with open(ca_bundle) as f:
            bundle = f.read()
        pem_certs = re.findall(r"-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----", bundle, re.S)
        for i, pem in enumerate(pem_certs):
            with tempfile.NamedTemporaryFile(mode="w", suffix=".pem", delete=False) as tf:
                tf.write(pem)
                tf_path = tf.name
            try:
                subprocess.run(
                    [certutil, "-A", "-d", db_arg, "-t", "CT,,", "-n", f"env-ca-{i}", "-i", tf_path],
                    check=False, capture_output=True, timeout=10,
                )
            finally:
                os.unlink(tf_path)
    except Exception:  # noqa: BLE001 -- seeding failure must not abort the fetch attempt
        pass


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

# Confirmed live 2026-09-16 against puntandrally's own team-roster grid --
# see module docstring. Left as an empty dict (not removed) so a future
# real mismatch has somewhere to go.
TEAM_NAME_ALIASES: dict[str, str] = {}

_SECTION_TABLE_PATTERN_TMPL = r'<div class="tr-section-title">{title}</div>.*?<table class="tr-table">(.*?)</table>'
_TBODY_PATTERN = re.compile(r"<tbody>(.*?)</tbody>", re.S)
_ROW_PATTERN = re.compile(r"<tr>(.*?)</tr>", re.S)
_PLAYER_CELL_PATTERN = re.compile(
    r'<td class="player[^"]*">.*?<span class="num">#(\d+)</span>\s*</span>\s*'
    r"([^<(]+?)\s*(?:\(([A-Z]+)\))?\s*<span class=\"elig-badge[^\"]*\">\s*([A-Z]*)\s*</span>",
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
    jersey: Optional[str] = None  # kept as a string ("00" has a real leading zero)
    class_year: Optional[str] = None  # "FR" | "SO" | "JR" | "SR" | "GR", from the page's own eligibility badge


@dataclass
class SnapHistory:
    totals: dict = field(default_factory=dict)  # name -> summed snaps across every year actually fetched
    years_fetched: list = field(default_factory=list)
    warnings: list = field(default_factory=list)


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
        _seed_chromium_nss_from_env()
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
        _seed_chromium_nss_from_env()
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
        jersey = player_match.group(1)
        name = _to_first_last_or_full(player_match.group(2))
        tag = player_match.group(3)
        class_year = player_match.group(4) or None
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

        result.players.append(PlayerSnaps(
            name=name, position_tag=tag, snaps=snaps, snap_share_pct=snap_share_pct,
            jersey=jersey, class_year=class_year,
        ))

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


# How many consecutive seasons (inclusive of through_year) fetch_snap_history
# sums by default -- NOT a true career total (see module docstring's
# ~2022 reliability floor), just a multi-year sum over whatever seasons
# were actually fetched.
DEFAULT_SNAP_HISTORY_YEARS = 3


def fetch_snap_history(
    team: str,
    through_year: int,
    years: int = DEFAULT_SNAP_HISTORY_YEARS,
    browser_fetch: Optional[Callable[..., str]] = None,
) -> SnapHistory:
    """Sums OL+DL snap counts per player name across `years` consecutive
    seasons ending at `through_year` (inclusive) -- e.g. years=3,
    through_year=2026 sums 2024, 2025, 2026. A player absent from a given
    season simply doesn't contribute for that year, not excluded. If a
    season's fetch fails (e.g. predates puntandrally's ~2022 reliability
    floor, or a site hiccup), that season is skipped with a warning
    appended -- every OTHER season still fetched still contributes, this
    never fails the whole call over one bad year."""
    result = SnapHistory()
    for year in range(through_year - years + 1, through_year + 1):
        try:
            ol_section, dl_section = fetch_roster(team, year, browser_fetch=browser_fetch)
        except PuntAndRallyFetchError as exc:
            result.warnings.append(f"{team} {year} snap-count fetch failed, excluded from multi-year total: {exc}")
            continue
        result.years_fetched.append(year)
        for player in ol_section.players + dl_section.players:
            if player.snaps is not None:
                result.totals[player.name] = result.totals.get(player.name, 0) + player.snaps
    return result


_TRUNCATED_NAME_PATTERN = re.compile(r"^([A-Za-z])\.\s+(.+)$")


def resolve_truncated_name(name: str, candidate_full_names: list) -> Optional[str]:
    """puntandrally truncates a long first name to a single initial on its
    own snap-count page -- confirmed live: SMU's real 'Malcolm
    Alcorn-Crowder' is listed there as 'M. Alcorn-Crowder'. An exact-name
    match against any other source's full name (CFBD's live roster,
    247Sports) then silently fails, dropping a real starter entirely
    rather than just missing one field.

    Recovers the real full name by matching the truncated name's initial +
    surname against `candidate_full_names`. Returns None (never guesses)
    when zero or more than one candidate matches -- an ambiguous initial
    must never silently resolve to the wrong player."""
    match = _TRUNCATED_NAME_PATTERN.match(name.strip())
    if not match:
        return None
    initial, surname = match.group(1).lower(), match.group(2).lower()
    matches = [
        full_name for full_name in candidate_full_names
        if full_name.lower().endswith(surname) and full_name[:1].lower() == initial
    ]
    return matches[0] if len(matches) == 1 else None


_GENERATIONAL_SUFFIX_PATTERN = re.compile(r"\s+(Jr\.?|Sr\.?|II|III|IV|V)$", re.IGNORECASE)


def _normalize_name_variant(name: str) -> str:
    """Folds away the two other real name-variance gaps confirmed live
    between puntandrally and CFBD/247Sports: puntandrally drops a
    trailing generational suffix ('Mike Wallace' for CFBD's 'Mike Wallace
    Jr.') and strips accent marks ('Andre Otto' for CFBD's real 'André
    Otto') -- neither is the initial-truncation pattern
    resolve_truncated_name handles."""
    stripped = _GENERATIONAL_SUFFIX_PATTERN.sub("", name).strip()
    folded = unicodedata.normalize("NFKD", stripped).encode("ascii", "ignore").decode("ascii")
    return folded.lower()


def resolve_name_variant(name: str, candidate_full_names: list) -> Optional[str]:
    """Recovers a real full name when puntandrally's version differs from
    a candidate only by a dropped generational suffix or stripped accent
    marks (see _normalize_name_variant) -- returns None (never guesses)
    unless exactly one candidate normalizes to the same form as `name`."""
    target = _normalize_name_variant(name)
    matches = [c for c in candidate_full_names if _normalize_name_variant(c) == target]
    return matches[0] if len(matches) == 1 else None


def resolve_any_name_match(name: str, candidate_full_names: list) -> Optional[str]:
    """Tries every name-variance recovery this module knows (initial
    truncation, dropped generational suffix, stripped accent marks) in
    BOTH directions -- `name` might be the shortened one (puntandrally's
    own name matched against a fuller source like CFBD or 247Sports) or
    a candidate might be (e.g. an ourlads-sourced name matched against
    puntandrally's own, differently-truncated historical spelling).
    Returns None (never guesses) unless exactly one candidate resolves."""
    exact = [c for c in candidate_full_names if c.lower() == name.lower()]
    if len(exact) == 1:
        return exact[0]
    resolved = resolve_truncated_name(name, candidate_full_names) or resolve_name_variant(name, candidate_full_names)
    if resolved is not None:
        return resolved
    reverse_matches = [
        c for c in candidate_full_names
        if resolve_truncated_name(c, [name]) == name or resolve_name_variant(c, [name]) == name
    ]
    return reverse_matches[0] if len(reverse_matches) == 1 else None


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
        "OL": {"players": [vars(p) for p in ol_section.players], "warnings": ol_section.warnings},
        "DL": {"players": [vars(p) for p in dl_section.players], "warnings": dl_section.warnings},
    }, indent=2))
