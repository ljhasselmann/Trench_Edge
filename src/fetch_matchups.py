"""Matchup discovery -- this week's Top-25-involving FBS games.

Live-verified (2026, week 3): 75 total FBS games, 22 involve at least one
AP Top 25 team, 3 have both teams ranked. Scope decision (confirmed with
the user): score only the Top-25-involving games, not the full slate.
derive_matchups()'s filter is a single boolean predicate -- the only code
that would need to change if that scope decision is ever revisited to
"every FBS game" instead; everything downstream (roster population,
rendering, output) is already scope-agnostic.

/games needs `classification=fbs`, NOT `division=fbs` -- confirmed live
the latter does not actually filter server-side (still returns non-FBS
games mixed in). /rankings returns several named polls per week; "AP Top
25" is the one used here (confirmed live: exactly 25 entries, CFBD's
normal team-name convention, same as /games's homeTeam/awayTeam) --
Coaches Poll and others exist in the same response but aren't used.

team_a = awayTeam, team_b = homeTeam for every discovered matchup --
CFBD's own convention for /games, no guessing needed. This module does
not merge with config/teams.yaml's hand-curated entries or write
anything to disk -- that's run_week.py's job, kept separate so this
module stays pure discovery, testable against a mocked schedule/rankings
fixture with no file I/O.

Real final scores, for backtesting (scripts/backfill_game_results.py):
CFBD's /games response already carries `homePoints`/`awayPoints`/
`completed` on every row -- confirmed live against a real completed 2026
week-1 game (TCU 10, North Carolina 15, `completed: True`) -- but
derive_matchups() only ever reads homeTeam/awayTeam and drops the rest.
extract_game_result()/fetch_game_results() read those same fields from
the same /games call fetch_fbs_schedule() already makes, so backfilling
results needs no new CFBD endpoint.
"""

from __future__ import annotations

import re
from typing import Optional

import requests

from fetch_cfbd import CFBD_BASE_URL, REQUEST_TIMEOUT_SECONDS, get_api_key, CFBDRequestError

GAMES_ENDPOINT = "/games"
RANKINGS_ENDPOINT = "/rankings"
AP_POLL_NAME = "AP Top 25"


def fetch_fbs_schedule(year: int, week: int, session: Optional[requests.Session] = None) -> list[dict]:
    api_key = get_api_key()
    http = session or requests
    try:
        response = http.get(
            f"{CFBD_BASE_URL}{GAMES_ENDPOINT}",
            params={"year": year, "week": week, "seasonType": "regular", "classification": "fbs"},
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        raise CFBDRequestError(f"request to CFBD failed for /games year={year} week={week}: {exc}") from exc

    if response.status_code != 200:
        raise CFBDRequestError(
            f"CFBD returned HTTP {response.status_code} for /games year={year} week={week}: {response.text[:500]}"
        )

    return response.json() or []


def fetch_ap_top25(year: int, week: int, session: Optional[requests.Session] = None) -> set:
    """{school, ...} for every team in that week's AP Top 25. Returns an
    empty set (not an error) if the poll isn't in the response yet (e.g.
    very early preseason) -- derive_matchups then correctly yields zero
    matchups rather than crashing."""
    api_key = get_api_key()
    http = session or requests
    try:
        response = http.get(
            f"{CFBD_BASE_URL}{RANKINGS_ENDPOINT}",
            params={"year": year, "week": week},
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        raise CFBDRequestError(f"request to CFBD failed for /rankings year={year} week={week}: {exc}") from exc

    if response.status_code != 200:
        raise CFBDRequestError(
            f"CFBD returned HTTP {response.status_code} for /rankings year={year} week={week}: {response.text[:500]}"
        )

    data = response.json() or []
    for entry in data:
        for poll in entry.get("polls", []):
            if poll.get("poll") == AP_POLL_NAME:
                return {r["school"] for r in poll.get("ranks", []) if "school" in r}
    return set()


def _slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def derive_matchups(games: list[dict], top25: set, year: int, week: int) -> list[dict]:
    """One dict per game involving a ranked team, shaped exactly like
    config/teams.yaml's matchup entries. `side` defaults to the forward
    direction, but run_week.py scores both directions regardless -- it's
    kept here only for parity with the manually-curated config file
    schema, where a human might want just one."""
    matchups = []
    for game in games:
        home = game.get("homeTeam")
        away = game.get("awayTeam")
        if not home or not away:
            continue  # a malformed/bye entry, not a real game -- skip, don't guess
        if home not in top25 and away not in top25:
            continue

        matchups.append({
            "label": f"{year}-wk{week:02d}-{_slugify(away)}-{_slugify(home)}",
            "team_a": away,
            "team_b": home,
            "side": "team_a_ol_vs_team_b_dl",
            "week": week,
        })
    return matchups


def extract_game_result(game: dict) -> Optional[dict]:
    """{"home_points", "away_points"} for a completed game with real
    points on both sides; None for anything else (not yet played, or a
    malformed row) -- never a fabricated result."""
    if not game.get("completed"):
        return None
    home_points = game.get("homePoints")
    away_points = game.get("awayPoints")
    if home_points is None or away_points is None:
        return None
    return {"home_points": home_points, "away_points": away_points}


def fetch_game_results(year: int, week: int, session: Optional[requests.Session] = None) -> dict:
    """{(awayTeam, homeTeam): {"home_points", "away_points"}} for every
    COMPLETED game that week -- keyed the same way derive_matchups()
    builds a label (team_a=away, team_b=home), so a caller can look up a
    matchup's real result by its own team_a/team_b. Games not yet played
    are simply absent, not included with a null/guessed score."""
    games = fetch_fbs_schedule(year, week, session=session)
    results = {}
    for game in games:
        home, away = game.get("homeTeam"), game.get("awayTeam")
        if not home or not away:
            continue
        result = extract_game_result(game)
        if result is not None:
            results[(away, home)] = result
    return results


def discover_matchups(year: int, week: int, session: Optional[requests.Session] = None) -> list[dict]:
    games = fetch_fbs_schedule(year, week, session=session)
    top25 = fetch_ap_top25(year, week, session=session)
    return derive_matchups(games, top25, year, week)


if __name__ == "__main__":
    import argparse
    import json

    parser = argparse.ArgumentParser(description="Discover this week's Top-25-involving FBS matchups.")
    parser.add_argument("--year", type=int, default=2026)
    parser.add_argument("--week", type=int, required=True)
    args = parser.parse_args()

    matchups = discover_matchups(args.year, args.week)
    print(json.dumps(matchups, indent=2))
    print(f"\n{len(matchups)} matchup(s) found", flush=True)
