#!/usr/bin/env python3
"""Read-only diagnostic: checks whether every CFBD FBS team resolves
against the SP+ sheet, ourlads's team index, and puntandrally's team
index -- directly, via an existing alias dict, or via CFBD's own
`alternateNames` -- and prints ready-to-paste alias entries for any miss.
The puntandrally check drives a real headless browser (see
fetch_puntandrally.py's docstring for why) so this run is noticeably
slower than the ourlads/SP+ checks alone.

Run this once before scale-out (fetch_matchups.py's discovered matchups
can span dozens of teams; discovering one mismatch per failed run, the
way "Miami" -> "Miami-FL" was originally found, doesn't scale), and again
whenever a lookup-miss warning shows up in a real run.

This does not modify anything -- it only reports. Paste suggested
entries into fetch_sp_plus.TEAM_NAME_ALIASES / fetch_ourlads.py's own
alias dict (if it turns out to need one) by hand, after checking the
suggestion is actually correct.
"""

from __future__ import annotations

import sys
from difflib import get_close_matches
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import fetch_cfbd
import fetch_ourlads
import fetch_puntandrally
import fetch_sp_plus
from team_names import build_canonical_alias_map


def _resolves(canonical: str, table_keys, known_alias: str | None, alias_map: dict[str, str]) -> bool:
    if canonical in table_keys:
        return True
    if known_alias and known_alias in table_keys:
        return True
    # Does any alternate spelling of this canonical name appear in the table?
    lowered_keys = {k.lower() for k in table_keys}
    for name, resolved_canonical in alias_map.items():
        if resolved_canonical == canonical and name in lowered_keys:
            return True
    return False


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Check team-name coverage across CFBD, the SP+ sheet, and ourlads.")
    parser.add_argument("--year", type=int, default=2026)
    parser.add_argument("--week", type=int, required=True, help="Week number for the SP+ sheet's 'FBS Week N' tab")
    args = parser.parse_args()

    print(f"Fetching CFBD FBS teams (year={args.year})...")
    cfbd_teams = fetch_cfbd.fetch_fbs_teams(args.year)
    alias_map = build_canonical_alias_map(cfbd_teams)
    print(f"  {len(cfbd_teams)} teams, {len(alias_map)} known name/alias entries")

    print(f"Fetching SP+ sheet (FBS Week {args.week})...")
    sp_plus_table = fetch_sp_plus.fetch_fbs_week_table(args.week)
    print(f"  {len(sp_plus_table)} teams in SP+ sheet")

    print("Fetching ourlads team index...")
    ourlads_index = fetch_ourlads.fetch_team_index()
    print(f"  {len(ourlads_index)} teams in ourlads index")

    print("Fetching puntandrally team index (drives a real headless browser -- slower)...")
    puntandrally_index = fetch_puntandrally.fetch_team_index()
    print(f"  {len(puntandrally_index)} teams in puntandrally index")

    ourlads_aliases = getattr(fetch_ourlads, "TEAM_NAME_ALIASES", {})
    puntandrally_aliases = getattr(fetch_puntandrally, "TEAM_NAME_ALIASES", {})

    sp_plus_misses = []
    ourlads_misses = []
    puntandrally_misses = []
    for team in cfbd_teams:
        canonical = team["school"]
        if not _resolves(canonical, sp_plus_table.keys(), fetch_sp_plus.TEAM_NAME_ALIASES.get(canonical), alias_map):
            sp_plus_misses.append(canonical)
        if not _resolves(canonical, ourlads_index.keys(), ourlads_aliases.get(canonical), alias_map):
            ourlads_misses.append(canonical)
        if not _resolves(canonical, puntandrally_index, puntandrally_aliases.get(canonical), alias_map):
            puntandrally_misses.append(canonical)

    print()
    if sp_plus_misses:
        print(f"SP+ sheet: {len(sp_plus_misses)} team(s) need an alias -- suggested TEAM_NAME_ALIASES entries:")
        for team in sp_plus_misses:
            suggestions = get_close_matches(team, sp_plus_table.keys(), n=3)
            if suggestions:
                print(f'    "{team}": "{suggestions[0]}",  # or one of {suggestions!r}')
            else:
                print(f"  {team!r}: no close match found in the sheet at all -- check manually")
    else:
        print("SP+ sheet: every CFBD FBS team resolves.")

    print()
    if ourlads_misses:
        print(f"ourlads: {len(ourlads_misses)} team(s) need an alias -- suggested entries:")
        for team in ourlads_misses:
            suggestions = get_close_matches(team, ourlads_index.keys(), n=3)
            if suggestions:
                print(f'    "{team}": "{suggestions[0]}",  # or one of {suggestions!r}')
            else:
                print(f"  {team!r}: no close match found in ourlads's index at all -- check manually")
    else:
        print("ourlads: every CFBD FBS team resolves.")

    print()
    if puntandrally_misses:
        print(f"puntandrally: {len(puntandrally_misses)} team(s) need an alias -- suggested entries:")
        for team in puntandrally_misses:
            suggestions = get_close_matches(team, puntandrally_index, n=3)
            if suggestions:
                print(f'    "{team}": "{suggestions[0]}",  # or one of {suggestions!r}')
            else:
                print(f"  {team!r}: no close match found in puntandrally's index at all -- check manually")
    else:
        print("puntandrally: every CFBD FBS team resolves.")


if __name__ == "__main__":
    main()
