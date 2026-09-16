"""Push -- overall SP+ differential (DESIGN.md Section 5).

Source: Bill Connelly's own weekly SP+ spreadsheet (Google Sheet, title
"2026 SP+", owned by billconnelly1@gmail.com), fileId
1vwoVl-Dxy0es87Z9I1RTvFzr72Lb1fAkREfbLxbK-eg. Originally this could only be
read via Claude's Drive connector (not available to a fired Routine session
-- see git history). Once docs.google.com was added to this environment's
network allowlist, a plain REST fetch became possible via the gviz query
endpoint:

    https://docs.google.com/spreadsheets/d/{fileId}/gviz/tq?tqx=out:csv&sheet={sheet name}

NOTE: the plain /export?format=csv endpoint does NOT work here -- it
redirects to a dynamically-named *.googleusercontent.com host (confirmed
live: doc-0g-98-sheets.googleusercontent.com) that isn't allowlisted and
whose exact name isn't stable enough to allowlist statically. The gviz
endpoint serves the CSV directly from docs.google.com with no redirect,
confirmed live.

TAB NAMING: the workbook has parallel per-week tabs, e.g. "FBS Week 3",
"TOP 772 WEEK 3" (also FCS/D2/D3/NAIA variants). "TOP 772" covers all
divisions on one unified scale and is NOT the commonly-published SP+
(confirmed live: its SP+/Off/Def values differ from "FBS Week N" by a
constant offset -- ~+52.1 overall, +26.1 offense, -26.1 defense, checked
across 7 teams -- almost certainly a cross-division normalization shift,
not a different rating). Since DESIGN.md Section 5 only uses a
*difference* between two teams, that constant cancels and either tab
would give the same gap -- but "FBS Week N" is used here because its
absolute values match the commonly recognized public SP+ scale (~-30 to
+35), which is worth being right about for anyone spot-checking a number
against another source.

WEEK NUMBER IS NOT AUTO-DISCOVERED. Requesting a sheet name that doesn't
exist does not error -- gviz silently returns HTTP 200 with the
*workbook's first tab* instead (confirmed live: "FBS Week 99" returns the
ATS-tracking tab, not an error). Silently trusting that would parse the
wrong table as if it were current ratings. So this module requires an
explicit `week` argument and validates the response's header shape before
trusting it, rather than guessing the current week and risking a silent
wrong-tab read.

TEAM NAME MISMATCH: this sheet uses CFBD-incompatible names for some
teams -- confirmed live: CFBD's "Miami" is "Miami-FL" here (disambiguating
from Miami-OH). TEAM_NAME_ALIASES below is a manual map from the name used
elsewhere in this repo (config/teams.yaml, CFBD) to this sheet's name.
Extend it as new mismatches turn up -- team_sp_plus() raises with the
closest name matches when a lookup misses, specifically so a new mismatch
is easy to diagnose and add rather than silently guessed at.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from difflib import get_close_matches
from typing import Optional

import requests

SHEET_FILE_ID = "1vwoVl-Dxy0es87Z9I1RTvFzr72Lb1fAkREfbLxbK-eg"
GVIZ_URL = f"https://docs.google.com/spreadsheets/d/{SHEET_FILE_ID}/gviz/tq"
REQUEST_TIMEOUT_SECONDS = 20

EXPECTED_HEADER_MARKERS = ("Off. SP+", "Def. SP+")

TEAM_NAME_ALIASES = {
    "Miami": "Miami-FL",
}


class SPPlusFetchError(RuntimeError):
    """Raised when the sheet can't be reached, or the requested week's tab
    doesn't exist / doesn't have the expected shape (see module docstring
    on why this can't just trust an HTTP 200)."""


@dataclass
class TeamSPPlus:
    team: str
    record: str
    sp_plus: float
    sp_plus_rank: int
    off_sp_plus: float
    off_sp_plus_rank: int
    def_sp_plus: float
    def_sp_plus_rank: int


def _to_sheet_name(team: str) -> str:
    return TEAM_NAME_ALIASES.get(team, team)


def fetch_fbs_week_table(week: int, session: Optional[requests.Session] = None) -> dict:
    """Fetch the "FBS Week {week}" tab. Returns {team: TeamSPPlus}.
    Raises SPPlusFetchError if that tab doesn't exist or its header
    doesn't match what's expected -- never returns data silently read
    from the wrong tab."""
    sheet_name = f"FBS Week {week}"
    http = session or requests
    try:
        response = http.get(
            GVIZ_URL, params={"tqx": "out:csv", "sheet": sheet_name}, timeout=REQUEST_TIMEOUT_SECONDS
        )
    except requests.RequestException as exc:
        raise SPPlusFetchError(f"request to the SP+ sheet failed for {sheet_name!r}: {exc}") from exc

    if response.status_code != 200:
        raise SPPlusFetchError(f"SP+ sheet returned HTTP {response.status_code} for {sheet_name!r}")

    rows = list(csv.reader(io.StringIO(response.text)))
    if not rows:
        raise SPPlusFetchError(f"SP+ sheet returned an empty response for {sheet_name!r}")

    header = rows[0]
    if not all(marker in header for marker in EXPECTED_HEADER_MARKERS):
        raise SPPlusFetchError(
            f"{sheet_name!r} doesn't look like an FBS ratings tab (missing "
            f"{EXPECTED_HEADER_MARKERS!r} in header) -- gviz silently falls back to the "
            f"workbook's first tab for a sheet name that doesn't exist, so this week's "
            f"tab likely hasn't been published yet. Got header: {header!r}"
        )

    team_col = header.index("Team")
    teams: dict[str, TeamSPPlus] = {}
    for row in rows[1:]:
        if len(row) <= team_col or not row[team_col]:
            continue
        try:
            teams[row[team_col]] = TeamSPPlus(
                team=row[team_col],
                record=row[team_col + 2],
                sp_plus=float(row[team_col + 3]),
                sp_plus_rank=int(float(row[team_col + 4])),
                off_sp_plus=float(row[team_col + 5]),
                off_sp_plus_rank=int(float(row[team_col + 6])),
                def_sp_plus=float(row[team_col + 7]),
                def_sp_plus_rank=int(float(row[team_col + 8])),
            )
        except (ValueError, IndexError):
            continue  # non-data row (blank separator, footer, etc.)

    return teams


def team_sp_plus(team: str, week: int, table: Optional[dict] = None, session: Optional[requests.Session] = None) -> TeamSPPlus:
    table = table if table is not None else fetch_fbs_week_table(week, session=session)
    sheet_name = _to_sheet_name(team)
    if sheet_name not in table:
        suggestions = get_close_matches(sheet_name, table.keys(), n=3)
        raise SPPlusFetchError(
            f"{team!r} (looked up as {sheet_name!r}) not found in the SP+ sheet's FBS Week "
            f"{week} tab. Closest names in the sheet: {suggestions!r}. If one of these is "
            f"really {team!r}, add it to TEAM_NAME_ALIASES in fetch_sp_plus.py."
        )
    return table[sheet_name]


def compute_sp_plus_gap(
    team_a: str, team_b: str, week: int, table: Optional[dict] = None, session: Optional[requests.Session] = None
) -> float:
    """table lets a caller scoring many matchups in one run fetch the whole
    week's table once (fetch_fbs_week_table covers all FBS teams already)
    and reuse it, instead of re-fetching per matchup."""
    table = table if table is not None else fetch_fbs_week_table(week, session=session)
    a = team_sp_plus(team_a, week, table=table)
    b = team_sp_plus(team_b, week, table=table)
    return a.sp_plus - b.sp_plus


if __name__ == "__main__":
    import argparse
    import json

    parser = argparse.ArgumentParser(description="Compute the overall SP+ gap for one matchup.")
    parser.add_argument("team_a")
    parser.add_argument("team_b")
    parser.add_argument("--week", type=int, required=True)
    args = parser.parse_args()

    table = fetch_fbs_week_table(args.week)
    a = team_sp_plus(args.team_a, args.week, table=table)
    b = team_sp_plus(args.team_b, args.week, table=table)
    print(json.dumps({
        "team_a": vars(a),
        "team_b": vars(b),
        "sp_plus_gap": a.sp_plus - b.sp_plus,
    }, indent=2))
