"""Tier 2 -- talent and continuity (DESIGN.md Section 4c).

Live-checked against CFBD's full API spec and real responses, not assumed:

- /talent: {year, team, talent} -- a single team-wide composite. There is
  no position-group breakdown available anywhere in CFBD's API. Section 4c's
  "position-group-specific where available, team-wide as fallback" resolves
  to: team-wide is the *only* option CFBD offers, full stop.

- /player/returning: percentPPA/usage broken out by passing/receiving/
  rushing only. This is CFBD's PPA framework, which -- like /player/usage
  (see fetch_roster.py) -- only scores plays with an individually
  attributable value: it has no defensive fields at all and nothing for
  line play, which never touches the ball. It cannot answer "how many OL/DL
  starters are returning" at any level.

So "returning starters, by position group" (the other half of Section 4c,
alongside team talent) has to come from the same place Mass's starters
already come from: a human-maintained list, not an API. Rather than invent
a new config file, this reuses config/rosters/{team}.yaml (see
_template.yaml) with a `prior_season_starters` block set once per season,
and computes name-overlap against the current `starters` block that
fetch_roster.py already requires a human to keep current weekly.

The qualitative "talent-driven vs. scheme-driven" flag (Section 4c) is
likewise a plain human-authored field on the same file (`continuity_note`).
DESIGN.md does not specify a numeric discount for a scheme-driven flag --
only "discount further" -- so this module does not invent one. It surfaces
the flag and lets the caller (the rendered report) show it as a caveat,
per Section 6's "every number must be traceable... never silently absorbed."
Picking an actual discount factor is an open modeling decision, not
something to guess at here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import requests

from fetch_cfbd import CFBD_BASE_URL, REQUEST_TIMEOUT_SECONDS, get_api_key, CFBDRequestError
from fetch_roster import _load_starter_config

TALENT_ENDPOINT = "/talent"


@dataclass
class TalentInputs:
    team: str
    talent_composite: Optional[float] = None
    returning_ol_starters: Optional[int] = None
    returning_dl_starters: Optional[int] = None
    continuity_driver: Optional[str] = None  # "talent" | "scheme" | "mixed", human-set
    continuity_note: Optional[str] = None
    warnings: list[str] = field(default_factory=list)


def fetch_team_talent(team: str, year: int, session: Optional[requests.Session] = None) -> Optional[float]:
    """Fetch CFBD's team-wide talent composite. Returns None (with the
    caller expected to warn) if the team isn't in that year's list --
    CFBD's talent composite only covers teams with enough recruiting data,
    so a small/new program can legitimately be absent."""
    api_key = get_api_key()
    http = session or requests
    try:
        response = http.get(
            f"{CFBD_BASE_URL}{TALENT_ENDPOINT}",
            params={"year": year},
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        raise CFBDRequestError(f"request to CFBD failed for /talent year={year}: {exc}") from exc

    if response.status_code != 200:
        raise CFBDRequestError(f"CFBD returned HTTP {response.status_code} for /talent year={year}: {response.text[:500]}")

    rows = response.json() or []
    for row in rows:
        if row.get("team") == team:
            return row.get("talent")
    return None


def _count_returning(current: list[dict], prior: list[dict]) -> int:
    current_names = {entry["name"].lower() for entry in current}
    prior_names = {entry["name"].lower() for entry in prior}
    return len(current_names & prior_names)


def compute_continuity_inputs(team: str, year: int, session: Optional[requests.Session] = None) -> TalentInputs:
    inputs = TalentInputs(team=team)

    try:
        inputs.talent_composite = fetch_team_talent(team, year, session=session)
        if inputs.talent_composite is None:
            inputs.warnings.append(f"{team} not present in CFBD's {year} /talent list")
    except CFBDRequestError as exc:
        inputs.warnings.append(f"talent composite fetch failed: {exc}")

    config = _load_starter_config(team)
    if config is None:
        inputs.warnings.append(
            f"config/rosters/{team}.yaml does not exist -- returning-starter count unavailable"
        )
        return inputs

    prior = config.get("prior_season_starters")
    current = config.get("starters", {})
    if prior is None:
        inputs.warnings.append(
            f"config/rosters/{team}.yaml has no prior_season_starters block -- set once per "
            "season by a human (who started for this team last year); returning-starter "
            "count cannot be computed without it"
        )
    else:
        inputs.returning_ol_starters = _count_returning(current.get("OL", []), prior.get("OL", []))
        inputs.returning_dl_starters = _count_returning(current.get("DL", []), prior.get("DL", []))

    note_block = config.get("continuity_note")
    if note_block is None:
        inputs.warnings.append(
            f"config/rosters/{team}.yaml has no continuity_note -- talent-driven vs. "
            "scheme-driven flag (DESIGN.md 4c) not set for this team/season"
        )
    else:
        inputs.continuity_driver = note_block.get("driver")
        inputs.continuity_note = note_block.get("note")

    return inputs


if __name__ == "__main__":
    import argparse
    import json

    parser = argparse.ArgumentParser(description="Compute Tier 2 talent/continuity inputs for one team.")
    parser.add_argument("team")
    parser.add_argument("--year", type=int, default=2026)
    args = parser.parse_args()

    result = compute_continuity_inputs(args.team, args.year)
    print(json.dumps(vars(result), indent=2))
