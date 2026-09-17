"""Mass -- roster weights (DESIGN.md Section 4b).

CFBD's /roster endpoint carries player weight directly (verified live --
see the API-availability findings in this repo's history/commit log), so
raw weight lookup is one API call, not hand-scraping. What CFBD does NOT
have -- confirmed against its full published API spec, not assumed -- is
any depth-chart or starter endpoint. /player/usage (the only per-player
participation data CFBD exposes) only covers QB/RB/WR/TE, the positions
play-by-play charts individually; OL/DL are never individually tracked.

So "who's actually starting" still has to come from a human, via
config/rosters/{team}.yaml (see _template.yaml in that directory for the
schema). This module's job is: pull the live roster, cross-reference it
against that human-maintained starter list to get authoritative current
weights, and flag loudly (never silently) when:
  - a named starter can't be matched against the live roster (transfer,
    name variant, typo)
  - the starter list itself is stale (>7 days since a human confirmed it,
    per DESIGN.md 4b point 3)

CFBD's position tags are NOT standardized across teams -- confirmed live:
Miami/Wake Forest tag their whole D-line generically as "DL"; Toledo splits
it into "DE"/"DT" and only tags 3 stragglers "DL". OL_POSITION_TAGS /
DL_POSITION_TAGS below are unioned sets for this reason, not single strings.
Same variability hits OL: some teams tag every lineman "OL", others use the
specific "OT"/"OG"/"C" codes (confirmed live: LSU's Weston Davis and Jordan
Seaton both tag "OT", Georgia's Zykie Helton tags "C") -- OL_POSITION_TAGS
covers both, or every specifically-tagged OL starter falsely triggers the
"verify this is the right player" mismatch warning below.
"""

from __future__ import annotations

import datetime as _dt
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import requests
import yaml

from fetch_cfbd import CFBD_BASE_URL, REQUEST_TIMEOUT_SECONDS, get_api_key, CFBDRequestError
from fetch_puntandrally import resolve_any_name_match

ROSTER_ENDPOINT = "/roster"
STALENESS_LIMIT_DAYS = 7

OL_POSITION_TAGS = {"OL", "OT", "OG", "C"}
DL_POSITION_TAGS = {"DL", "DE", "DT", "EDGE"}

ROSTERS_DIR = Path(__file__).resolve().parents[1] / "config" / "rosters"


@dataclass
class StarterWeight:
    name: str
    weight_lbs: Optional[float]
    confidence: str  # "confirmed" (matched live roster) or "estimated" (human-supplied only)
    source: str
    jersey: Optional[str] = None
    class_year: Optional[str] = None  # "FR" | "SO" | "JR" | "SR" | "GR"
    snaps_multi_year: Optional[int] = None  # multi-season sum from fetch_puntandrally, NOT a true career total
    recruit_rating: Optional[int] = None  # 0-100 composite, from fetch_247sports -- display only, see fetch_talent.py for the actual scored differential
    recruit_stars: Optional[int] = None  # 0-5, from fetch_247sports's real star icons
    position_tag: Optional[str] = None  # puntandrally's own "T"|"G"|"C"|"DE"|"DT"|"DL" tag -- used for the chalkboard formation view


@dataclass
class MassInputs:
    team: str
    ol_starters: list[StarterWeight] = field(default_factory=list)
    dl_starters: list[StarterWeight] = field(default_factory=list)
    avg_ol_weight: Optional[float] = None
    avg_dl_weight: Optional[float] = None
    offense_scheme: Optional[str] = None  # ourlads' own label, e.g. "Air Raid" -- see fetch_ourlads.py
    defense_scheme: Optional[str] = None  # e.g. "4-2-5"
    warnings: list[str] = field(default_factory=list)


def fetch_full_roster(team: str, year: int, session: Optional[requests.Session] = None) -> list[dict]:
    """Fetch CFBD's /roster for one team/year -- full roster, not starters."""
    api_key = get_api_key()
    http = session or requests
    try:
        response = http.get(
            f"{CFBD_BASE_URL}{ROSTER_ENDPOINT}",
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

    return response.json() or []


def _full_name(player: dict) -> str:
    return f"{player.get('firstName', '')} {player.get('lastName', '')}".strip()


def _load_starter_config(team: str) -> Optional[dict]:
    """Load config/rosters/{team}.yaml. Returns None if it doesn't exist
    yet -- that's a real, expected state (a human hasn't populated it),
    not an error; callers must surface it as a warning, not crash."""
    path = ROSTERS_DIR / f"{team}.yaml"
    if not path.exists():
        return None
    with open(path) as f:
        return yaml.safe_load(f)


def compute_mass_inputs(
    team: str,
    year: int,
    session: Optional[requests.Session] = None,
    now: Optional[_dt.datetime] = None,
) -> MassInputs:
    inputs = MassInputs(team=team)
    now = now or _dt.datetime.now(_dt.timezone.utc)

    config = _load_starter_config(team)
    if config is not None:
        inputs.offense_scheme = config.get("offense_scheme")
        inputs.defense_scheme = config.get("defense_scheme")
    if config is None:
        inputs.warnings.append(
            f"config/rosters/{team}.yaml does not exist -- no human-confirmed starter "
            "list, Mass cannot be computed for this team. Copy _template.yaml and fill it in."
        )
        return inputs

    updated_at_raw = config.get("updated_by_human_at")
    if updated_at_raw:
        updated_at = _dt.datetime.fromisoformat(str(updated_at_raw)).replace(tzinfo=_dt.timezone.utc)
        age_days = (now - updated_at).days
        if age_days > STALENESS_LIMIT_DAYS:
            inputs.warnings.append(
                f"starter list for {team} is {age_days} days old (>{STALENESS_LIMIT_DAYS}-day "
                "limit) -- depth chart may have changed, confirm before trusting this Mass score"
            )
    else:
        inputs.warnings.append(f"config/rosters/{team}.yaml has no updated_by_human_at -- treat as stale")

    live_roster = fetch_full_roster(team, year, session=session)
    live_by_name = {_full_name(p).lower(): p for p in live_roster}
    live_full_names = [_full_name(p) for p in live_roster]

    starters_cfg = config.get("starters", {})
    for group_key, tag_set, bucket in (
        ("OL", OL_POSITION_TAGS, inputs.ol_starters),
        ("DL", DL_POSITION_TAGS, inputs.dl_starters),
    ):
        for entry in starters_cfg.get(group_key, []):
            name = entry["name"]
            live_player = live_by_name.get(name.lower())
            if live_player is None:
                # The staged starter name (from ourlads/puntandrally) can
                # differ from CFBD's own spelling -- a truncated initial,
                # a dropped generational suffix, or a stripped accent
                # mark -- which would otherwise silently drop a real
                # starter entirely. See fetch_puntandrally.resolve_any_name_match.
                resolved = resolve_any_name_match(name, live_full_names)
                if resolved is not None:
                    live_player = live_by_name[resolved.lower()]
                    inputs.warnings.append(
                        f"{name} ({team}, {group_key}) matched live CFBD roster as {resolved!r} "
                        "-- the two sources spell this player's name differently"
                    )
            if live_player is not None:
                weight = live_player.get("weight")
                if weight is None:
                    inputs.warnings.append(f"{name} ({team}, {group_key}) matched live roster but has no listed weight")
                bucket.append(StarterWeight(
                    name=name, weight_lbs=weight, confidence="confirmed", source="cfbd_roster",
                    jersey=entry.get("jersey"), class_year=entry.get("class_year"),
                    snaps_multi_year=entry.get("snaps_multi_year"),
                    recruit_rating=entry.get("recruit_rating"), recruit_stars=entry.get("recruit_stars"),
                    position_tag=entry.get("position_tag"),
                ))
                if live_player.get("position") not in tag_set:
                    inputs.warnings.append(
                        f"{name} ({team}) listed as starting {group_key} in config, but CFBD "
                        f"tags them position={live_player.get('position')!r} -- verify this is the right player"
                    )
            elif entry.get("weight") is not None:
                inputs.warnings.append(
                    f"{name} ({team}, {group_key}) not found in live CFBD roster -- using "
                    "human-supplied estimated weight, flagged as such"
                )
                bucket.append(StarterWeight(
                    name=name, weight_lbs=entry["weight"], confidence="estimated",
                    source=entry.get("source", "manual"),
                    jersey=entry.get("jersey"), class_year=entry.get("class_year"),
                    snaps_multi_year=entry.get("snaps_multi_year"),
                    recruit_rating=entry.get("recruit_rating"), recruit_stars=entry.get("recruit_stars"),
                    position_tag=entry.get("position_tag"),
                ))
            else:
                inputs.warnings.append(
                    f"{name} ({team}, {group_key}) not found in live CFBD roster and no "
                    "fallback weight supplied in config -- excluded from Mass average"
                )

    if inputs.ol_starters:
        weights = [s.weight_lbs for s in inputs.ol_starters if s.weight_lbs is not None]
        inputs.avg_ol_weight = sum(weights) / len(weights) if weights else None
    if inputs.dl_starters:
        weights = [s.weight_lbs for s in inputs.dl_starters if s.weight_lbs is not None]
        inputs.avg_dl_weight = sum(weights) / len(weights) if weights else None

    return inputs


if __name__ == "__main__":
    import argparse
    import json

    parser = argparse.ArgumentParser(description="Compute Mass (starter weight) inputs for one team.")
    parser.add_argument("team")
    parser.add_argument("--year", type=int, default=2026)
    args = parser.parse_args()

    result = compute_mass_inputs(args.team, args.year)
    print(json.dumps({
        "team": result.team,
        "avg_ol_weight": result.avg_ol_weight,
        "avg_dl_weight": result.avg_dl_weight,
        "ol_starters": [vars(s) for s in result.ol_starters],
        "dl_starters": [vars(s) for s in result.dl_starters],
        "warnings": result.warnings,
    }, indent=2))
