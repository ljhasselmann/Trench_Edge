# Trench Edge

See `DESIGN.md` for the full design. This README is the quickstart plus a
running record of what has actually been built and tested vs. what's still
a design-doc placeholder.

## Setup

```
pip install -r requirements.txt
export CFBD_API_KEY=...   # or put it in a local .env (gitignored)
```

## Status (as of this session)

**Built and live-tested against the real CFBD API key:**

- `src/compute_composite.py` — the Section 5 scoring model (Mass/Push/
  Continuity normalization, weighting, verdict bands). Pure computation,
  no network dependency. Unit-tested (`tests/test_compute_composite.py`).
- `src/fetch_cfbd.py` — full Section 4a fetch: stuff rate, line yards,
  front-seven-only havoc, and adjusted sack rate. Call
  `fetch_team_trench_stats(team, year)`.
  - `/stats/season/advanced` (stuffRate, lineYards, havoc.total/frontSeven/db)
    verified live against Miami and Wake Forest, both the completed 2025
    season and the in-progress 2026 season. Zero parsing warnings on any
    of the four live calls.
  - **`/stats/season/advanced` has no sack-rate field at all** — confirmed
    by inspecting a live response, not assumed from docs. Adjusted sack
    rate is instead derived from `/stats/season`'s raw counting stats
    (`sacks`, `sacksOpponent`, `passAttempts`, `passAttemptsOpponent`),
    using CFBD's offense/defense naming convention: a bare stat name is
    the team's own offensive production, the `...Opponent` variant is
    defense (verified live: Miami's `rushingYards` 2428 vs
    `rushingYardsOpponent` 1429, consistent with that convention). See
    the module docstring in `src/fetch_cfbd.py` for the exact formula.
  - Unit-tested against a mocked HTTP layer (`tests/test_fetch_cfbd.py`) —
    including one test that caught and fixed a real bug (an
    empty-but-present side payload was being treated as "missing
    entirely" instead of generating per-field warnings).
  - 19 tests total, all passing: `python3 -m pytest tests/ -v`.
- `config/weights.yaml`, `config/teams.yaml` — scaffolded per Section 3/5.
  `teams.yaml` defaults to **year 2026**, not 2025 — confirmed live that
  today (2026-09-15) falls in the 2026 season (2 games played so far,
  right in the weeks-1-3 small-sample window Section 4a warns about); 2025
  is already a completed 16-game season and would be the wrong default.

**Confirmed working end-to-end for real data:**

```
$ python3 src/fetch_cfbd.py Miami --year 2026
{
  "offense": {"stuff_rate": 0.104, "line_yards": 4.06, "havoc_front_seven": 0.035, "adjusted_sack_rate": 0.0, "warnings": []},
  "defense": {"stuff_rate": 0.268, "line_yards": 2.30, "havoc_front_seven": 0.062, "adjusted_sack_rate": 0.042, "warnings": []}
}
```

(This session's network egress initially blocked `api.collegefootballdata.com`
with a 403 — exactly the allowlist gap Section 7 flags as "the one
non-obvious setup step." That's since been resolved for this environment.)

**Not yet built (design-doc-only, Section 4b/4c/6):**

- `src/fetch_roster.py` (Mass/roster weights)
- `src/fetch_talent.py` (Tier 2 talent/continuity)
- `src/render_widget.py` + `templates/widget.html.jinja` — the design doc
  says the template should "match the visual style already established"
  elsewhere in the broader project; that reference wasn't available in this
  session, so building a template now would mean guessing at style rather
  than matching it.
- `config/rosters/{team}.yaml` cache files.

## Running the tests

```
pip install -r requirements.txt pytest
python3 -m pytest tests/ -v
```

## Running the pieces that exist

```
# Composite scoring (no network needed):
python3 src/compute_composite.py --weight-diff-lbs 25 --sp-plus-gap 10 --net-returning-starters 2

# Live CFBD fetch (needs api.collegefootballdata.com allowlisted + CFBD_API_KEY set):
python3 src/fetch_cfbd.py Miami --year 2026
```
