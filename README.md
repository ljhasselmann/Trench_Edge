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

- `src/fetch_roster.py` — Mass. CFBD's `/roster` carries player weight
  directly (confirmed live, ~93-100% coverage across the three teams
  checked). CFBD has **no depth-chart/starter endpoint anywhere** (checked
  the full 84-endpoint API spec, not assumed) and `/player/usage` only
  covers QB/RB/WR/TE, so starters still come from a human via
  `config/rosters/{team}.yaml` (schema: `_template.yaml`); the module
  cross-references that list against the live roster for current weights
  and flags mismatches/staleness (>7 days) loudly. Also found CFBD's
  position tags aren't standardized across teams (Miami/Wake Forest tag
  the whole D-line generically `"DL"`; Toledo splits `"DE"`/`"DT"`) — the
  matcher unions tag sets rather than trusting one string.
- `src/fetch_talent.py` — Tier 2. `/talent` is a single team-wide
  composite (no position-group breakdown exists in CFBD, confirmed live);
  `/player/returning` is offense-only PPA (passing/rushing/receiving),
  nothing for defense or line play. So "returning starters by position
  group" reuses the same human-maintained roster file, now extended with
  a once-per-season `prior_season_starters` block; returning-starter count
  is the name-overlap between that and the current week's `starters`. The
  talent-vs-scheme qualitative flag (Section 4c) is a plain human field
  (`continuity_note`) — DESIGN.md never specifies a numeric discount for
  it, so it's surfaced as a caveat, not silently applied.
- `src/render_widget.py` + `templates/widget.html.jinja` — no reference
  for "the visual style already established" was available in this
  session, so this is a clean original design (dataviz-skill-compliant:
  validated diverging blue/red palette, proper bar-mark spacing, light/dark
  mode), not a match to anything existing. `build_context()` orchestrates
  the three fetch modules into one `render()` call; **live-verified
  end-to-end** (`output/latest.html` in this repo is real output from a
  live run, screenshot-checked in a headless browser).

**Push — resolved.** Not wired to CFBD's Tier 1 stats: naively subtracting
`team_a.offense.stuffRate` from `team_b.defense.stuffRate` doesn't actually
answer who wins the matchup (both are season-long rates against different
schedules; combining them validly needs a league-average baseline CFBD
doesn't provide — inventing one would violate Section 2's own non-goal).
Instead, Push uses **overall SP+ differential** (`team_a.SP+ - team_b.SP+`,
`/5`, capped ±10 — DESIGN.md Section 5 has the full reasoning, including
why the trench-specific Off/Def-SP+ combination was tried and rejected).
Set per-matchup as `sp_plus_gap` in `config/teams.yaml`. **Live-verified**:
Miami 77.7 SP+, Wake Forest 55.5 SP+ → gap 22.2 → Push score +4.4,
screenshot-confirmed rendering as a real diverging bar. The composite now
computes for real once Mass, Push, and Continuity are all present.

### SP+ source

Bill Connelly's own weekly SP+ ratings (Google Sheet, title "2026 SP+",
owned by `billconnelly1@gmail.com`), fileId
`1vwoVl-Dxy0es87Z9I1RTvFzr72Lb1fAkREfbLxbK-eg`. This is **not a REST API** —
it's read via Claude's Google Drive connector
(`mcp__Google_Drive__read_file_content`), so no `fetch_sp_plus.py` script
exists; whoever runs this pipeline reads the sheet and fills in
`sp_plus_gap` by hand (or the weekly Routine does it in its own session —
see DESIGN.md Section 7).

**The sheet has multiple undated snapshot tabs — do not trust tab order or
gid.** Identify the current one by matching each team's win-loss record in
the `Team | 2026 Conference | Record | SP+ | Rk | Off. SP+ | Rk | Def. SP+ | Rk`
table against the actual current week (e.g. confirmed live 2026-09-15: the
correct tab showed Miami/Wake Forest both at 2-0, matching that week's real
CFBD game counts; three other tabs showed stale 1-0/0-0 snapshots).

No team roster files (`config/rosters/{team}.yaml`) are committed — I
don't have real depth-chart knowledge of any team's actual current
starters, and fabricating one would put false information about real
players in the repo.

## Running the tests

```
pip install -r requirements.txt pytest
python3 -m pytest tests/ -v   # 34 tests, all passing
```

## Weekly usage

```
# 1. For each team in this week's matchup, copy the template and fill in
#    real starters from actual depth-chart reporting:
cp config/rosters/_template.yaml "config/rosters/Miami.yaml"

# 2. Set this week's matchup in config/teams.yaml (label/team_a/team_b/side),
#    and set sp_plus_gap from the SP+ sheet (see above).

# 3. Render:
python3 src/render_widget.py 2026-wk03-miami-wake
# -> output/latest.html, plus any caveats printed to stderr
```

## Running the pieces individually

```
# Composite scoring (no network needed):
python3 src/compute_composite.py --weight-diff-lbs 25 --sp-plus-gap 10 --net-returning-starters 2

# Live CFBD fetch (needs api.collegefootballdata.com allowlisted + CFBD_API_KEY set):
python3 src/fetch_cfbd.py Miami --year 2026
python3 src/fetch_roster.py Miami --year 2026
python3 src/fetch_talent.py Miami --year 2026
```
