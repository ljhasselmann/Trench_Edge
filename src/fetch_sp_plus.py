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

Seeded live via scripts/check_team_name_coverage.py (2026-09-16, all 138
CFBD FBS teams checked): every entry below is a verified exact match in
the sheet, not a fuzzy guess -- the coverage script's own get_close_matches
suggestions included at least one wrong pairing (CFBD's "Louisiana" fuzzy-
matched to "Louisiana Tech", a different school; the real match, verified
by listing the sheet's own team names directly, is "UL-Lafayette").

REAL PER-GAME BETTING LINES, confirmed live 2026-09-16 -- the SAME
"FBS Week N" tab also carries a full schedule in columns to the LEFT of
the team-ratings table `fetch_fbs_week_table` parses (`Game`, `Spread`,
`ATS Pick`, `Proj. margin`, `O/U`, `O/U pick`) -- e.g. the real Week 3 row
for this matchup: `"Miami-FL at Wake Forest"`, `Spread: "Miami-FL -22.5"`,
`ATS Pick: "Wake Forest"`. This had gone unused; `fetch_week_lines()`
parses it for backtesting against real closing lines (see
scripts/backfill_game_results.py), not just SP+'s own aggregate
season-long ATS record (a genuinely different tab this module does NOT
parse -- gviz's silent-fallback-to-first-tab behavior above happens to
land on that aggregate tab, which only has week-level W-L-push totals
across the whole slate, no per-game data at all; confirmed live by
requesting a nonexistent week and inspecting what came back).
"""

from __future__ import annotations

import csv
import io
import re
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
    "Miami (OH)": "Miami-OH",
    "Hawai'i": "Hawaii",
    "Louisiana": "UL-Lafayette",
    "San José State": "San Jose State",
    "UL Monroe": "UL-Monroe",
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


_SHEET_NAME_TO_CANONICAL = {sheet: canonical for canonical, sheet in TEAM_NAME_ALIASES.items()}


def _from_sheet_name(sheet_name: str) -> str:
    """Reverse of _to_sheet_name -- most sheet names already match this
    repo's canonical (CFBD) spelling directly, so this is a no-op for
    the vast majority of teams; only the handful in TEAM_NAME_ALIASES
    need translating back."""
    return _SHEET_NAME_TO_CANONICAL.get(sheet_name, sheet_name)


def _fetch_week_rows(week: int, session: Optional[requests.Session] = None) -> list:
    """Raw CSV rows for the "FBS Week {week}" tab, validated to actually be
    an FBS ratings tab (see module docstring on gviz's silent wrong-tab
    fallback). Shared by fetch_fbs_week_table (the ratings half of the
    tab) and fetch_week_lines (the schedule/spread half, to its left) --
    one fetch serves both rather than hitting the sheet twice."""
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
    return rows


def fetch_fbs_week_table(week: int, session: Optional[requests.Session] = None) -> dict:
    """Fetch the "FBS Week {week}" tab. Returns {team: TeamSPPlus}.
    Raises SPPlusFetchError if that tab doesn't exist or its header
    doesn't match what's expected -- never returns data silently read
    from the wrong tab."""
    rows = _fetch_week_rows(week, session=session)
    header = rows[0]
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


@dataclass
class GameLine:
    away_team: str  # sheet's own spelling, e.g. "Miami-FL"
    home_team: str
    favorite: Optional[str]  # sheet's own spelling of the favored team; None if no line posted (e.g. an FCS game)
    spread: Optional[float]  # points the favorite is favored by (always positive); None if no line
    ats_pick: Optional[str]  # sheet's own spelling of the team Connelly's model picks to cover
    proj_margin: Optional[float]
    over_under: Optional[float]
    ou_pick: Optional[str]


_SCHEDULE_COLUMNS = ("Game", "Spread", "ATS Pick", "Proj. margin", "O/U", "O/U pick")


def _parse_game(game_text: str) -> Optional[tuple]:
    """"Away at Home" or "Team1 vs. Team2" (neutral site) -> (first, second).
    For a neutral-site game this repo has no way to know which is CFBD's
    homeTeam -- find_game_line() below matches by team IDENTITY, not by
    away/home position, specifically so that ambiguity never matters."""
    for sep in (" at ", " vs. "):
        if sep in game_text:
            first, second = game_text.split(sep, 1)
            return first.strip(), second.strip()
    return None


def _parse_spread(spread_text: str) -> Optional[tuple]:
    """"Miami-FL -22.5" -> ("Miami-FL", 22.5). Blank means no line posted."""
    spread_text = spread_text.strip()
    if not spread_text:
        return None
    match = re.match(r"^(.+?)\s+([+-]?\d+(?:\.\d+)?)$", spread_text)
    if not match:
        return None
    return match.group(1).strip(), abs(float(match.group(2)))


def _to_float_or_none(text: str) -> Optional[float]:
    text = text.strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def fetch_week_lines(week: int, session: Optional[requests.Session] = None) -> list:
    """Real per-game betting lines + Connelly's own ATS picks for a week,
    from the SAME "FBS Week {week}" tab fetch_fbs_week_table reads -- see
    module docstring. Returns a list[GameLine] (order as the sheet lists
    them); a game with no line posted (common for an FCS opponent) still
    appears, with favorite/spread as None -- never guessed at."""
    rows = _fetch_week_rows(week, session=session)
    header = rows[0]
    col = {name: header.index(name) for name in _SCHEDULE_COLUMNS if name in header}
    missing = [name for name in _SCHEDULE_COLUMNS if name not in col]
    if missing:
        raise SPPlusFetchError(
            f"FBS Week {week} tab is missing expected schedule column(s) {missing!r} -- header: {header!r}"
        )

    lines = []
    for row in rows[1:]:
        if len(row) <= col["Game"] or not row[col["Game"]].strip():
            continue
        parsed_game = _parse_game(row[col["Game"]])
        if parsed_game is None:
            continue
        away, home = parsed_game

        spread_cell = row[col["Spread"]] if len(row) > col["Spread"] else ""
        parsed_spread = _parse_spread(spread_cell)
        favorite, spread = parsed_spread if parsed_spread else (None, None)

        ats_cell = row[col["ATS Pick"]].strip() if len(row) > col["ATS Pick"] else ""
        ou_pick_cell = row[col["O/U pick"]].strip() if len(row) > col["O/U pick"] else ""

        lines.append(GameLine(
            away_team=away,
            home_team=home,
            favorite=favorite,
            spread=spread,
            ats_pick=ats_cell or None,
            proj_margin=_to_float_or_none(row[col["Proj. margin"]]) if len(row) > col["Proj. margin"] else None,
            over_under=_to_float_or_none(row[col["O/U"]]) if len(row) > col["O/U"] else None,
            ou_pick=ou_pick_cell or None,
        ))
    return lines


def find_game_line(lines: list, team_a: str, team_b: str) -> Optional[GameLine]:
    """team_a/team_b in this repo's canonical (CFBD) spelling; resolves
    aliases the same way team_sp_plus does, and matches by team IDENTITY
    (either away/home order) since a neutral-site "vs." entry's ordering
    doesn't reliably correspond to CFBD's own homeTeam/awayTeam."""
    sheet_a = _to_sheet_name(team_a)
    sheet_b = _to_sheet_name(team_b)
    wanted = {sheet_a, sheet_b}
    for line in lines:
        if {line.away_team, line.home_team} == wanted:
            return line
    return None


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
