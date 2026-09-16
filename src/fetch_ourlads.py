"""Mass -- live depth charts from ourlads.com (supplements DESIGN.md 4b).

Confirmed live: this environment's network egress allowlist (added
ourlads.com) took effect for plain `requests` calls, but NOT for the
`WebFetch` tool -- that's a separate, independent egress gate that hasn't
picked up the change. So a fired Routine session's WebFetch-based research
still fails even after the domain is allowlisted; this module is a plain
`requests` script instead, callable via Bash like fetch_cfbd.py, which
sidesteps that gate entirely.

Team name -> ourlads URL isn't guessable (e.g. Miami is
depth-chart/miami/91073) -- fetch_team_index() parses it live from
ourlads's own team-picker page rather than hardcoding a lookup table that
would drift. ourlads's team names mostly match CFBD's convention ("Miami",
not "Miami-FL" like the SP+ sheet) -- but not always: checked live at full
scale (scripts/check_team_name_coverage.py, all 138 CFBD FBS teams) and
found 6 real mismatches (TEAM_NAME_ALIASES below), the same class of
problem fetch_sp_plus.py already has, just smaller. The earlier claim here
that no alias map was needed was based on checking only 2 teams (Miami,
Wake Forest) and didn't hold at scale.

ONE TEAM IS GENUINELY MISSING, not mismatched: ourlads's index has no
Washington State entry under any name (confirmed live -- its "Washington"
entry is the actual University of Washington, a different school; ourlads
happens to also list a "FCS & Small College NFL Prospects" catch-all
entry, which is why its total team count coincidentally still matches
CFBD's 138). Don't alias Washington State to anything -- fetch_depth_chart
correctly raises OurladsFetchError for it, which is the right outcome; it
needs the WebSearch/WebFetch fallback path, same as any other genuine
ourlads miss.

Position row labels are NOT uniform across teams -- they vary by each
team's actual defensive scheme, confirmed live: Miami's OL is the fixed
LT/LG/C/RG/RT ordering, but Wake Forest's D-line front is NT/DT/LDE/RDE (four
spots), not the 3-name front an earlier preview-article search suggested
(that search also misspelled a real starter's name -- "Krischke" for
"Kirschke" -- which is why fetch_roster.py's exact-match lookup wrongly
treated him as not on the roster; ourlads spells it correctly). So
parse_depth_chart() returns every row generically as {label: [names in
depth order]}; OL_ROW_LABELS / DL_ROW_LABELS are a superset of labels seen
across teams checked so far, not an exhaustive enum -- extend as new ones
turn up, the same pattern as fetch_roster.py's CFBD position-tag sets.
"""

from __future__ import annotations

import re
from difflib import get_close_matches
from typing import Optional

import requests

TEAM_INDEX_URL = "https://www.ourlads.com/ncaa-football-depth-charts/"
DEPTH_CHART_URL_TMPL = "https://www.ourlads.com/ncaa-football-depth-charts/depth-chart/{slug}/{team_id}"
REQUEST_TIMEOUT_SECONDS = 20
# ourlads returns a non-content response to some default client UAs.
DEFAULT_HEADERS = {"User-Agent": "Mozilla/5.0"}

OL_ROW_LABELS = {"LT", "LG", "C", "RG", "RT", "OT", "OG"}
DL_ROW_LABELS = {"LDE", "RDE", "LE", "RE", "DT", "NT", "DL", "LDT", "RDT"}

# CFBD's canonical name -> ourlads's own spelling. Seeded live via
# scripts/check_team_name_coverage.py (2026-09-16); each entry verified by
# listing ourlads's own team index directly, not just trusting a fuzzy
# string match (that heuristic mismatched real teams here -- e.g. "NC
# State" suggested against "Utah State", "Washington State" against
# "Washington" -- both wrong, different schools).
TEAM_NAME_ALIASES = {
    "Hawai'i": "Hawaii",
    "Miami (OH)": "Miami (Ohio)",
    "NC State": "North Carolina State",
    "Ole Miss": "Mississippi",
    "San José State": "San Jose State",
    "UL Monroe": "Louisiana-Monroe",
}

_TEAM_INDEX_PATTERN = re.compile(
    r"alt='([^']+)' class='nfl-dc-mm-logo'.*?depth-chart\.aspx\?s=([a-z0-9-]+)&id=(\d+)", re.S
)
_ROW_PATTERN = re.compile(r"<td class='row-dc-\w+'>([A-Z]+)</td>(.*?)</tr>", re.S)
_PLAYER_PATTERN = re.compile(r"player/[a-z0-9.'-]+/\d+'\s*class='[a-z_]*'>([^<]+)<")


class OurladsFetchError(RuntimeError):
    """Raised on a network failure, or when the page's structure doesn't
    match what parsing expects (never returns a guessed/partial result
    silently)."""


def _to_first_last(raw: str) -> str:
    """ourlads formats players as 'Last, First[.] ClassYear[/TR]' (e.g.
    'Okunlola, Samson RS JR', 'Johnson, D.J. RS SO/TR'). First name is
    reliably the first whitespace token after the comma across every
    example checked live -- class-year tokens always follow it."""
    last, _, rest = raw.partition(",")
    first = rest.strip().split(" ")[0] if rest.strip() else ""
    return f"{first} {last.strip()}".strip()


def fetch_team_index(session: Optional[requests.Session] = None) -> dict:
    """{team_name: (slug, team_id)} parsed live from ourlads's own team
    picker -- confirmed live to cover 138 FBS teams."""
    http = session or requests
    try:
        response = http.get(TEAM_INDEX_URL, headers=DEFAULT_HEADERS, timeout=REQUEST_TIMEOUT_SECONDS)
    except requests.RequestException as exc:
        raise OurladsFetchError(f"request to ourlads team index failed: {exc}") from exc
    if response.status_code != 200:
        raise OurladsFetchError(f"ourlads team index returned HTTP {response.status_code}")

    index = {name.strip(): (slug, team_id) for name, slug, team_id in _TEAM_INDEX_PATTERN.findall(response.text)}
    if not index:
        raise OurladsFetchError("ourlads team index parsed to zero teams -- page structure may have changed")
    return index


def parse_depth_chart(html: str) -> dict:
    chart = {}
    for label, row_html in _ROW_PATTERN.findall(html):
        players = [_to_first_last(p) for p in _PLAYER_PATTERN.findall(row_html)]
        if players:
            chart[label] = players  # depth order; [0] is the starter
    return chart


def fetch_depth_chart(team: str, session: Optional[requests.Session] = None, index: Optional[dict] = None) -> dict:
    index = index if index is not None else fetch_team_index(session=session)
    site_name = TEAM_NAME_ALIASES.get(team, team)
    if site_name not in index:
        suggestions = get_close_matches(site_name, index.keys(), n=3)
        raise OurladsFetchError(
            f"{team!r} (looked up as {site_name!r}) not found in ourlads's team index. "
            f"Closest matches: {suggestions!r}. If one of these is really {team!r}, "
            "add it to TEAM_NAME_ALIASES in fetch_ourlads.py."
        )

    slug, team_id = index[site_name]
    http = session or requests
    url = DEPTH_CHART_URL_TMPL.format(slug=slug, team_id=team_id)
    try:
        response = http.get(url, headers=DEFAULT_HEADERS, timeout=REQUEST_TIMEOUT_SECONDS)
    except requests.RequestException as exc:
        raise OurladsFetchError(f"request to ourlads depth chart failed for {team!r}: {exc}") from exc
    if response.status_code != 200:
        raise OurladsFetchError(f"ourlads depth chart returned HTTP {response.status_code} for {team!r}")

    chart = parse_depth_chart(response.text)
    if not chart:
        raise OurladsFetchError(
            f"ourlads depth chart for {team!r} parsed to zero position rows -- page structure may have changed"
        )
    return chart


def starters_for_group(chart: dict, labels: set) -> list:
    """First-string name per matching row label. A team missing some
    labels (e.g. a 3-man front with no DT row) just yields fewer names --
    never pads with a guess."""
    return [chart[label][0] for label in chart if label in labels and chart[label]]


if __name__ == "__main__":
    import argparse
    import json

    parser = argparse.ArgumentParser(description="Fetch live starters from ourlads.com for one team.")
    parser.add_argument("team")
    parser.add_argument("--group", choices=["OL", "DL"], required=True)
    args = parser.parse_args()

    labels = OL_ROW_LABELS if args.group == "OL" else DL_ROW_LABELS
    chart = fetch_depth_chart(args.team)
    print(json.dumps({
        "team": args.team,
        "full_chart": chart,
        "starters": starters_for_group(chart, labels),
    }, indent=2))
