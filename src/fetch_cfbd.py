"""Tier 1 — CFBD advanced stats (DESIGN.md Section 4a).

Pulls, per team, per side of the ball:
  - Stuff rate
  - Line yards / opportunity rate
  - Front-seven havoc rate (front seven only, DB havoc excluded)
  - Adjusted sack rate (sacks per dropback)

Auth via Authorization: Bearer $CFBD_API_KEY, read from the environment.
The key is never hardcoded, logged, or printed -- callers that need to
confirm the key is present should check truthiness only, never print it.

NOTE ON SCHEMA CONFIDENCE: the field paths below (offense.stuffRate,
offense.havoc.frontSeven, etc.) reflect CFBD's documented
/stats/season/advanced response shape as of this writing, but this module
has not yet been exercised against a live response in this environment
(api.collegefootballdata.com is not on this session's network allowlist --
see DESIGN.md Section 7). Treat the parsing in `_extract_side` as
best-effort until it's been run once against real data; it's written
defensively (missing keys surface as None + a flagged warning rather than
a KeyError) specifically because of that.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional

import requests

CFBD_BASE_URL = "https://api.collegefootballdata.com"
ADVANCED_STATS_ENDPOINT = "/stats/season/advanced"
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


if __name__ == "__main__":
    import argparse
    import json

    parser = argparse.ArgumentParser(description="Fetch CFBD Tier 1 advanced stats for one team.")
    parser.add_argument("team")
    parser.add_argument("--year", type=int, default=2025)
    args = parser.parse_args()

    stats = fetch_advanced_stats(args.team, args.year)
    print(json.dumps({
        "team": stats.team,
        "year": stats.year,
        "offense": vars(stats.offense),
        "defense": vars(stats.defense),
    }, indent=2))
