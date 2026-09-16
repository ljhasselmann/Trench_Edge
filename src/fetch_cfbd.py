"""Tier 1 — CFBD advanced stats (DESIGN.md Section 4a).

Pulls, per team, per side of the ball:
  - Stuff rate
  - Line yards / opportunity rate
  - Front-seven havoc rate (front seven only, DB havoc excluded)
  - Adjusted sack rate (sacks per dropback)

Auth via Authorization: Bearer $CFBD_API_KEY, read from the environment.
The key is never hardcoded, logged, or printed -- callers that need to
confirm the key is present should check truthiness only, never print it.

SCHEMA STATUS: `_extract_side` (stuffRate / lineYards / havoc.*) has been
verified against live /stats/season/advanced responses for Miami and Wake
Forest, 2025 season -- all fields present, zero warnings. It's still left
defensive (missing keys surface as a warning, never a KeyError) because a
provider can change a schema at any time; that's not a hedge against this
being untested anymore.

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


FBS_TEAMS_ENDPOINT = "/teams/fbs"


def fetch_fbs_teams(year: int, session: Optional[requests.Session] = None) -> list[dict]:
    """GET /teams/fbs -- confirmed live to return, per team, `school` (CFBD's
    canonical name) and `alternateNames` (e.g. Miami: ["Miami (FL)", "MIA",
    "Miami"]). This is the authoritative source for reconciling team names
    across the other external sources this repo uses (the SP+ sheet,
    ourlads) -- see scripts/check_team_name_coverage.py, which is where
    this actually gets used; the production render path doesn't do live
    name resolution (too much risk of silently picking the wrong alternate
    name on every run for a rare problem)."""
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
