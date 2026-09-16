"""Tier 2 -- talent and experience (DESIGN.md Section 4c).

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

"Returning starters, by position group" (the other half of Section 4c,
alongside team talent) used to require a human-maintained list (a
`prior_season_starters` block in config/rosters/{team}.yaml, hand-typed
once a season) because neither CFBD nor any other source had year-over-year
line-play data. That's no longer true: fetch_puntandrally.py's `year=`
param gives real, accurate full-season snap counts for prior seasons
(confirmed live back to at least 2022 -- see that module's docstring), so
this module now computes an automated "returning experience" score --
what share of this year's starters' snaps, at the SAME team, were played
by the same players last season -- instead of a bare name-overlap count.
A current starter absent from last year's roster (transfer-in, true
freshman) counts as 0% returning snap share for themselves, not excluded,
so it correctly depresses the team's average; a transfer's snaps at their
OLD school are never counted, since fetch_puntandrally.fetch_roster(team,
year, ...) only ever returns that one team's own page.

config/rosters/{team}.yaml's `prior_season_starters` block is kept as a
fallback only (not removed, not required going forward) -- used only if
the live year-over-year puntandrally fetch fails for a team (site issue,
or a team predating puntandrally's reliability floor). That fallback can't
reproduce a snap-share number from bare names, so it computes a coarser,
explicitly-labeled proxy: the percentage of this year's starters who also
appear in `prior_season_starters` by name, with no usage weighting.

The qualitative "talent-driven vs. scheme-driven" flag (Section 4c) is
still a plain human-authored field on the same file (`continuity_note`) --
nothing here automates it; DESIGN.md does not specify a numeric discount
for it either, only "discount further," so this module surfaces it as a
caveat rather than inventing one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional

import requests

import fetch_puntandrally
from fetch_cfbd import CFBD_BASE_URL, REQUEST_TIMEOUT_SECONDS, get_api_key, CFBDRequestError
from fetch_roster import _load_starter_config

TALENT_ENDPOINT = "/talent"


@dataclass
class ExperienceInputs:
    team: str
    talent_composite: Optional[float] = None
    returning_ol_snap_pct: Optional[float] = None  # 0-100: avg share of this team's OWN last-season snaps its current OL starters played
    returning_dl_snap_pct: Optional[float] = None
    continuity_driver: Optional[str] = None  # "talent" | "scheme" | "mixed", human-set
    continuity_note: Optional[str] = None
    warnings: list[str] = field(default_factory=list)


def fetch_talent_table(year: int, session: Optional[requests.Session] = None) -> dict:
    """Fetch CFBD's entire /talent list for a year in one call, as
    {team: talent}. /talent has no `team` query param -- it always
    returns every team -- so fetch_team_talent() was re-fetching this
    same full list once per team it was asked about. A caller scoring
    many matchups in one run should fetch this once and reuse it."""
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
    return {row["team"]: row["talent"] for row in rows if "team" in row}


def fetch_team_talent(
    team: str, year: int, table: Optional[dict] = None, session: Optional[requests.Session] = None
) -> Optional[float]:
    """Fetch CFBD's team-wide talent composite. Returns None (with the
    caller expected to warn) if the team isn't in that year's list --
    CFBD's talent composite only covers teams with enough recruiting data,
    so a small/new program can legitimately be absent.

    table lets a caller scoring many teams reuse one fetch_talent_table()
    call instead of re-fetching the full list per team."""
    table = table if table is not None else fetch_talent_table(year, session=session)
    return table.get(team)


def _avg_returning_snap_pct(current_names: list[str], prior_players: list) -> Optional[float]:
    """Average `snap_share_pct` these exact player names had on THIS SAME
    TEAM last season (RosterSection.players from fetch_puntandrally). A
    name not found there -- transfer-in, true freshman -- contributes 0,
    never excluded, so it correctly drags the team's average down. Never
    given another team's roster to search: fetch_puntandrally.fetch_roster
    only ever returns the one team's own page, so a transfer's snaps at
    their old school structurally can't count here."""
    if not current_names:
        return None
    prior_by_name = {p.name.lower(): p.snap_share_pct for p in prior_players}
    total = 0.0
    for name in current_names:
        pct = prior_by_name.get(name.lower())
        total += pct if pct is not None else 0.0
    return total / len(current_names)


def _overlap_pct(current: list[dict], prior: list[dict]) -> Optional[float]:
    """Fallback proxy when the live puntandrally year-over-year fetch
    fails: percentage of this year's starters (by bare name overlap, no
    usage weighting) who also appear in config/rosters/{team}.yaml's
    human-curated prior_season_starters. Coarser than the live snap-share
    match -- the caller warns explicitly when this path is used."""
    if not current:
        return None
    current_names = {e["name"].lower() for e in current}
    prior_names = {e["name"].lower() for e in prior}
    return 100.0 * len(current_names & prior_names) / len(current_names)


def compute_experience_inputs(
    team: str,
    year: int,
    talent_table: Optional[dict] = None,
    session: Optional[requests.Session] = None,
    browser_fetch: Optional[Callable[..., str]] = None,
) -> ExperienceInputs:
    """`browser_fetch` is fetch_puntandrally's pluggable
    `(url, wait_for_selector=...) -> html` callable -- pass a
    browser_session() fetch when scoring many teams in one run, same as
    run_week.py's roster population does, so the year-1 lookups share one
    browser process instead of launching Chromium per team."""
    inputs = ExperienceInputs(team=team)

    try:
        inputs.talent_composite = fetch_team_talent(team, year, table=talent_table, session=session)
        if inputs.talent_composite is None:
            inputs.warnings.append(f"{team} not present in CFBD's {year} /talent list")
    except CFBDRequestError as exc:
        inputs.warnings.append(f"talent composite fetch failed: {exc}")

    config = _load_starter_config(team)
    if config is None:
        inputs.warnings.append(
            f"config/rosters/{team}.yaml does not exist -- returning-experience unavailable"
        )
        return inputs

    current = config.get("starters", {})
    current_ol_names = [e["name"] for e in current.get("OL", [])]
    current_dl_names = [e["name"] for e in current.get("DL", [])]

    if not current_ol_names and not current_dl_names:
        # Nothing to match a prior-year snap share against -- skip the live
        # fetch entirely rather than launching a browser for no reason.
        inputs.warnings.append(f"config/rosters/{team}.yaml has no starters yet -- returning-experience unavailable")
    else:
        try:
            prior_ol_section, prior_dl_section = fetch_puntandrally.fetch_roster(team, year - 1, browser_fetch=browser_fetch)
            inputs.returning_ol_snap_pct = _avg_returning_snap_pct(current_ol_names, prior_ol_section.players)
            inputs.returning_dl_snap_pct = _avg_returning_snap_pct(current_dl_names, prior_dl_section.players)
        except fetch_puntandrally.PuntAndRallyFetchError as exc:
            inputs.warnings.append(
                f"live {year - 1} snap-count fetch from puntandrally failed ({exc}) -- falling back to "
                f"config/rosters/{team}.yaml's prior_season_starters name-overlap (a coarser, non-snap-share proxy)"
            )
            prior = config.get("prior_season_starters")
            if prior is None:
                inputs.warnings.append(
                    f"config/rosters/{team}.yaml has no prior_season_starters fallback either -- "
                    "returning-experience cannot be computed"
                )
            else:
                inputs.returning_ol_snap_pct = _overlap_pct(current.get("OL", []), prior.get("OL", []))
                inputs.returning_dl_snap_pct = _overlap_pct(current.get("DL", []), prior.get("DL", []))

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

    parser = argparse.ArgumentParser(description="Compute Tier 2 talent/experience inputs for one team.")
    parser.add_argument("team")
    parser.add_argument("--year", type=int, default=2026)
    args = parser.parse_args()

    result = compute_experience_inputs(args.team, args.year)
    print(json.dumps(vars(result), indent=2))
