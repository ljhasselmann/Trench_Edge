"""Team-name canonicalization -- an offline reconciliation tool, not a
production render-path dependency. See fetch_sp_plus.TEAM_NAME_ALIASES's
own docstring for why: adding live alternateNames resolution to every run
would add a fetch and a matching-ambiguity risk (silently picking the
wrong alternate name) for a problem that's actually rare. Instead,
scripts/check_team_name_coverage.py uses this to find every mismatch
across this repo's external sources at once, offline, before scale-out
multiplies the team count from 2 (where "Miami" -> "Miami-FL" was found
one failure at a time) to ~44.
"""

from __future__ import annotations


def build_canonical_alias_map(teams: list[dict]) -> dict[str, str]:
    """teams: CFBD's /teams/fbs response shape (list of {school,
    alternateNames, ...}). Returns {lowercased name or alias: canonical
    `school` name} -- covers both the canonical name itself and every
    alternate spelling CFBD knows about."""
    alias_map: dict[str, str] = {}
    for team in teams:
        canonical = team["school"]
        alias_map[canonical.lower()] = canonical
        for alt in team.get("alternateNames") or []:
            alias_map[alt.lower()] = canonical
    return alias_map
