"""Tier 1 — CFBD advanced stats (DESIGN.md Section 4a).

Pulls, per team, per side of the ball:
  - Stuff rate
  - Line yards / opportunity rate
  - Power success rate (short-yardage run conversion -- a direct
    run-blocking signal, used by compute_ol_rank.py's Performance
    attribute; not scored anywhere in the matchup-differential composite)
  - Front-seven havoc rate (front seven only, DB havoc excluded)
  - Adjusted sack rate (sacks per dropback)

Auth via Authorization: Bearer $CFBD_API_KEY, read from the environment.
The key is never hardcoded, logged, or printed -- callers that need to
confirm the key is present should check truthiness only, never print it.

SCHEMA STATUS: `_extract_side` (stuffRate / lineYards / powerSuccess /
havoc.*) has been verified against live /stats/season/advanced responses
for Miami and Wake Forest, 2025 season -- all fields present, zero
warnings. It's still left defensive (missing keys surface as a warning,
never a KeyError) because a provider can change a schema at any time;
that's not a hedge against this being untested anymore.

/stats/season/advanced has no sack-rate field at all -- confirmed by
inspecting a live response, not assumed. Adjusted sack rate instead comes
from /stats/season's raw counting stats (`sacks`, `sacksOpponent`,
`passAttempts`, `passAttemptsOpponent`), per the offense/defense naming
convention CFBD uses there: a bare stat name (e.g. `rushingYards`) is the
team's own offensive production; the `...Opponent` variant is what
opponents produced against them, i.e. that team's defense. Verified against
live Miami 2025 data: rushingYards (2428) >> rushingYardsOpponent (1429),
consistent with that convention, not the reverse.

So, per team:
  - offense sack rate allowed = sacksOpponent / (passAttempts + sacksOpponent)
  - defense sack rate forced  = sacks / (passAttemptsOpponent + sacks)

with dropbacks approximated as pass attempts + sacks taken, since CFBD's
season stats don't expose dropbacks directly.
"""

from __future__ import annotations

import datetime as _dt
import os
from dataclasses import dataclass, field
from typing import Optional

import requests

CFBD_BASE_URL = "https://api.collegefootballdata.com"
ADVANCED_STATS_ENDPOINT = "/stats/season/advanced"
SEASON_STATS_ENDPOINT = "/stats/season"
REQUEST_TIMEOUT_SECONDS = 20


class CFBDAuthError(RuntimeError):
    """Raised when CFBD_API_KEY is missing from the environment."""


class CFBDRequestError(RuntimeError):
    """Raised when the CFBD API call itself fails (network, HTTP status)."""


@dataclass
class SideStats:
    """One side of the ball (offense or defense) for one team."""

    stuff_rate: Optional[float] = None
    line_yards: Optional[float] = None
    power_success: Optional[float] = None
    havoc_total: Optional[float] = None
    havoc_front_seven: Optional[float] = None
    havoc_db: Optional[float] = None
    adjusted_sack_rate: Optional[float] = None
    warnings: list[str] = field(default_factory=list)


@dataclass
class TeamAdvancedStats:
    team: str
    year: int
    offense: SideStats
    defense: SideStats
    raw: dict


def get_api_key() -> str:
    key = os.environ.get("CFBD_API_KEY")
    if not key:
        raise CFBDAuthError(
            "CFBD_API_KEY is not set in the environment. "
            "Set it as a secret (routine config) or in a local .env, "
            "never hardcode it in source."
        )
    return key


def _extract_side(payload: Optional[dict]) -> SideStats:
    """Defensively pull the fields Trench Edge needs out of one
    offense/defense block. Missing fields become None + a warning instead
    of raising, so a partial/changed schema degrades visibly rather than
    crashing the whole pipeline (DESIGN.md Section 6: every number must be
    traceable, and a silent None absorbed into a clean-looking composite
    is exactly what that section prohibits).
    """
    side = SideStats()
    if payload is None:
        side.warnings.append("side payload missing entirely from API response")
        return side

    if "stuffRate" in payload:
        side.stuff_rate = payload["stuffRate"]
    else:
        side.warnings.append("stuffRate field not present in response")

    if "lineYards" in payload:
        side.line_yards = payload["lineYards"]
    else:
        side.warnings.append("lineYards field not present in response")

    if "powerSuccess" in payload:
        side.power_success = payload["powerSuccess"]
    else:
        side.warnings.append("powerSuccess field not present in response")

    havoc = payload.get("havoc")
    if isinstance(havoc, dict):
        side.havoc_total = havoc.get("total")
        side.havoc_front_seven = havoc.get("frontSeven")
        side.havoc_db = havoc.get("db")
        if side.havoc_front_seven is None:
            side.warnings.append(
                "havoc.frontSeven not present -- front-seven-only havoc "
                "rate (DESIGN.md 4a) cannot be isolated from total havoc"
            )
    else:
        side.warnings.append("havoc block not present in response")

    return side


def fetch_season_stat_map(team: str, year: int, session: Optional[requests.Session] = None) -> dict:
    """Fetch /stats/season and flatten it to {statName: statValue}."""
    api_key = get_api_key()
    http = session or requests
    try:
        response = http.get(
            f"{CFBD_BASE_URL}{SEASON_STATS_ENDPOINT}",
            params={"year": year, "team": team},
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        raise CFBDRequestError(f"request to CFBD failed for team={team!r} year={year}: {exc}") from exc

    if response.status_code != 200:
        raise CFBDRequestError(
            f"CFBD returned HTTP {response.status_code} for team={team!r} year={year}: {response.text[:500]}"
        )

    rows = response.json() or []
    return {row["statName"]: row["statValue"] for row in rows}


def _apply_sack_rates(offense: SideStats, defense: SideStats, stat_map: dict) -> None:
    """Adjusted sack rate isn't in /stats/season/advanced; derive it from
    /stats/season raw counts. See module docstring for the formula and the
    offense/defense naming convention it relies on."""
    pass_attempts = stat_map.get("passAttempts")
    sacks_allowed = stat_map.get("sacksOpponent")
    if pass_attempts is not None and sacks_allowed is not None:
        dropbacks = pass_attempts + sacks_allowed
        offense.adjusted_sack_rate = sacks_allowed / dropbacks if dropbacks else None
    else:
        offense.warnings.append("passAttempts/sacksOpponent not present -- adjusted sack rate unavailable")

    pass_attempts_faced = stat_map.get("passAttemptsOpponent")
    sacks_forced = stat_map.get("sacks")
    if pass_attempts_faced is not None and sacks_forced is not None:
        dropbacks_faced = pass_attempts_faced + sacks_forced
        defense.adjusted_sack_rate = sacks_forced / dropbacks_faced if dropbacks_faced else None
    else:
        defense.warnings.append("passAttemptsOpponent/sacks not present -- adjusted sack rate unavailable")


def fetch_advanced_stats(team: str, year: int, session: Optional[requests.Session] = None) -> TeamAdvancedStats:
    """Fetch /stats/season/advanced for one team/year.

    Raises CFBDAuthError if no API key is configured, CFBDRequestError on
    network failure or non-200 response.
    """
    api_key = get_api_key()
    http = session or requests
    try:
        response = http.get(
            f"{CFBD_BASE_URL}{ADVANCED_STATS_ENDPOINT}",
            params={"year": year, "team": team},
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        raise CFBDRequestError(f"request to CFBD failed for team={team!r} year={year}: {exc}") from exc

    if response.status_code != 200:
        raise CFBDRequestError(
            f"CFBD returned HTTP {response.status_code} for team={team!r} year={year}: {response.text[:500]}"
        )

    payload = response.json()
    if not payload:
        raise CFBDRequestError(f"CFBD returned an empty result for team={team!r} year={year}")

    # The endpoint returns a list (one row per team matched); take the first.
    row = payload[0] if isinstance(payload, list) else payload

    return TeamAdvancedStats(
        team=team,
        year=year,
        offense=_extract_side(row.get("offense")),
        defense=_extract_side(row.get("defense")),
        raw=row,
    )


def fetch_team_trench_stats(team: str, year: int, session: Optional[requests.Session] = None) -> TeamAdvancedStats:
    """Full Section 4a fetch: advanced stats plus the derived adjusted sack
    rate, since no single CFBD endpoint carries everything DESIGN.md 4a
    asks for. This is the function the pipeline (and the CLI below) should
    call; `fetch_advanced_stats` alone is missing sack rate."""
    stats = fetch_advanced_stats(team, year, session=session)
    stat_map = fetch_season_stat_map(team, year, session=session)
    _apply_sack_rates(stats.offense, stats.defense, stat_map)
    return stats


RUSHING_PLAYS_ENDPOINT = "/rushing/plays"


@dataclass
class DirectionSplit:
    success_rate: Optional[float] = None
    play_count: int = 0


@dataclass
class RushingDirectionSplits:
    team: str
    left: DirectionSplit = field(default_factory=DirectionSplit)
    middle: DirectionSplit = field(default_factory=DirectionSplit)
    right: DirectionSplit = field(default_factory=DirectionSplit)
    warnings: list[str] = field(default_factory=list)


def fetch_rushing_direction_splits(
    team: str, year: int, session: Optional[requests.Session] = None
) -> RushingDirectionSplits:
    """GET /rushing/plays -- confirmed live (2025 season, Miami) to accept
    `year`+`team` alone for a full-season pull, no `week` required; ~59%
    of a real team-season's rush plays resolved a `rushDirection` in that
    check. This is CFBD parsing raw play text into `left`/`middle`/`right`
    (`directionAnalysisEligible`/`parseStatus` flag which rows are usable),
    NOT gap-level (no A/B/C gap exists anywhere in CFBD's API -- confirmed
    via a full API-docs search) and NOT attributable to one specific
    lineman -- this is a team-level, supplementary signal only. Rows are
    filtered to `offense == team` (the endpoint also returns plays where
    `team` was on defense) and to `directionAnalysisEligible` rows with a
    resolved `rushDirection`, so an unparseable play is excluded, never
    coerced into a bucket. A direction with zero resolved plays stays at
    its default `DirectionSplit()` (success_rate=None, play_count=0) --
    "no signal," never a fabricated 0.
    """
    api_key = get_api_key()
    http = session or requests
    try:
        response = http.get(
            f"{CFBD_BASE_URL}{RUSHING_PLAYS_ENDPOINT}",
            params={"year": year, "team": team},
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        raise CFBDRequestError(f"request to CFBD failed for /rushing/plays team={team!r} year={year}: {exc}") from exc

    if response.status_code != 200:
        raise CFBDRequestError(
            f"CFBD returned HTTP {response.status_code} for /rushing/plays team={team!r} year={year}: {response.text[:500]}"
        )

    rows = response.json() or []
    splits = RushingDirectionSplits(team=team)
    buckets: dict[str, list[bool]] = {"left": [], "middle": [], "right": []}
    for row in rows:
        if row.get("offense") != team:
            continue
        direction = row.get("rushDirection")
        if not row.get("directionAnalysisEligible") or direction not in buckets:
            continue
        success = row.get("success")
        if success is None:
            continue
        buckets[direction].append(bool(success))

    for direction, results in buckets.items():
        split = DirectionSplit(
            success_rate=sum(results) / len(results) if results else None,
            play_count=len(results),
        )
        setattr(splits, direction, split)

    if not any(b for b in buckets.values()):
        splits.warnings.append(
            f"no rushDirection-resolved offensive plays found for {team} in {year} -- "
            "direction splits unavailable (CFBD's play-text parsing didn't resolve a direction for any play)"
        )

    return splits


CALENDAR_ENDPOINT = "/calendar"


def fetch_calendar(year: int, session: Optional[requests.Session] = None) -> list[dict]:
    """GET /calendar -- per-week date ranges for a season (confirmed live:
    {season, week, seasonType, startDate, endDate, firstGameStart,
    lastGameStart}), one row per week across every seasonType (regular,
    postseason, ...). Used by detect_current_week() so a fired Routine can
    determine "this week" itself instead of needing a human to keep a week
    number current somewhere."""
    api_key = get_api_key()
    http = session or requests
    try:
        response = http.get(
            f"{CFBD_BASE_URL}{CALENDAR_ENDPOINT}",
            params={"year": year},
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        raise CFBDRequestError(f"request to CFBD failed for /calendar year={year}: {exc}") from exc

    if response.status_code != 200:
        raise CFBDRequestError(f"CFBD returned HTTP {response.status_code} for /calendar year={year}: {response.text[:500]}")

    return response.json() or []


def _parse_iso(raw: str) -> _dt.datetime:
    return _dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))


def detect_current_week(year: int, session: Optional[requests.Session] = None, now: Optional[_dt.datetime] = None) -> int:
    """The regular-season week whose [startDate, endDate] range contains
    `now` (UTC). If `now` is before the season starts, returns week 1; if
    after the season's last regular-season week ends (postseason/off-season),
    returns that last week -- a fired Routine calling this needs some answer,
    not a crash, and Top-25-involving-game discovery naturally yields zero
    matchups for a bye/off week rather than erroring."""
    calendar = fetch_calendar(year, session=session)
    regular = [row for row in calendar if row.get("seasonType") == "regular"]
    if not regular:
        raise CFBDRequestError(f"/calendar returned no regular-season weeks for year={year}")

    now = now or _dt.datetime.now(_dt.timezone.utc)
    for row in regular:
        if _parse_iso(row["startDate"]) <= now <= _parse_iso(row["endDate"]):
            return row["week"]

    first = min(regular, key=lambda r: r["week"])
    last = max(regular, key=lambda r: r["week"])
    if now < _parse_iso(first["startDate"]):
        return first["week"]
    return last["week"]


FBS_TEAMS_ENDPOINT = "/teams/fbs"


def fetch_fbs_teams(year: int, session: Optional[requests.Session] = None) -> list[dict]:
    """GET /teams/fbs -- confirmed live to return, per team, `school` (CFBD's
    canonical name) and `alternateNames` (e.g. Miami: ["Miami (FL)", "MIA",
    "Miami"]). This is the authoritative source for reconciling team names
    across the other external sources this repo uses (the SP+ sheet,
    ourlads) -- see scripts/check_team_name_coverage.py, which is where
    this actually gets used; the production render path doesn't do live
    name resolution (too much risk of silently picking the wrong alternate
    name on every run for a rare problem). Also carries `color`/
    `alternateColor` (real hex strings, confirmed live -- e.g. Miami
    "#f47321", Wake Forest "#ceb888") -- see team_colors_from_fbs_teams,
    which reuses this same response rather than fetching it again."""
    api_key = get_api_key()
    http = session or requests
    try:
        response = http.get(
            f"{CFBD_BASE_URL}{FBS_TEAMS_ENDPOINT}",
            params={"year": year},
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        raise CFBDRequestError(f"request to CFBD failed for /teams/fbs year={year}: {exc}") from exc

    if response.status_code != 200:
        raise CFBDRequestError(f"CFBD returned HTTP {response.status_code} for /teams/fbs year={year}: {response.text[:500]}")

    return response.json() or []


def team_colors_from_fbs_teams(teams: list[dict]) -> dict:
    """{school: {"color", "alt_color"}} from an already-fetched
    fetch_fbs_teams() list -- pure, no network, so a caller that already
    fetched the FBS team list for name-coverage/FCS-filtering purposes
    (see run_week.py) gets real team colors for free instead of a second
    fetch. A team missing either field (rare, but not guaranteed by CFBD)
    gets None for that field, never a fabricated color."""
    return {
        t["school"]: {"color": t.get("color"), "alt_color": t.get("alternateColor")}
        for t in teams if "school" in t
    }


if __name__ == "__main__":
    import argparse
    import json

    parser = argparse.ArgumentParser(description="Fetch CFBD Tier 1 trench stats for one team.")
    parser.add_argument("team")
    parser.add_argument("--year", type=int, default=2026)
    args = parser.parse_args()

    stats = fetch_team_trench_stats(args.team, args.year)
    print(json.dumps({
        "team": stats.team,
        "year": stats.year,
        "offense": vars(stats.offense),
        "defense": vars(stats.defense),
    }, indent=2))
