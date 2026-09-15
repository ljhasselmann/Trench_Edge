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

**Push — resolved, now a real fetch script.** Not wired to CFBD's Tier 1
stats: naively subtracting `team_a.offense.stuffRate` from
`team_b.defense.stuffRate` doesn't actually answer who wins the matchup
(both are season-long rates against different schedules; combining them
validly needs a league-average baseline CFBD doesn't provide — inventing
one would violate Section 2's own non-goal). Instead, Push uses **overall
SP+ differential** (`team_a.SP+ - team_b.SP+`, `/5`, capped ±10 —
DESIGN.md Section 5 has the full reasoning, including why the
trench-specific Off/Def-SP+ combination was tried and rejected).

`src/fetch_sp_plus.py` fetches this live, no Drive connector needed —
once `docs.google.com` was added to this environment's network allowlist,
a plain REST fetch became possible via Google's `gviz` query endpoint
(see the module docstring for two real gotchas this hit: the plain
`/export?format=csv` endpoint redirects to a *different*, dynamically-named
`*.googleusercontent.com` host that isn't allowlisted, and requesting a
tab name that doesn't exist returns HTTP 200 with the *wrong* tab's data
instead of an error — both confirmed live, both handled). Set `week` on a
matchup in `config/teams.yaml` and `render_widget.py` fetches the current
gap automatically; `sp_plus_gap` in the same file is now only a fallback,
used if the live fetch fails. **Live-verified**: Miami 25.6 SP+, Wake
Forest 3.4 SP+ (FBS Week 3 tab) → gap 22.2 → Push score +4.4,
screenshot-confirmed rendering as a real diverging bar. The composite now
computes for real once Mass, Push, and Continuity are all present.

### SP+ source

Bill Connelly's own weekly SP+ ratings (Google Sheet, title "2026 SP+",
owned by `billconnelly1@gmail.com`), fileId
`1vwoVl-Dxy0es87Z9I1RTvFzr72Lb1fAkREfbLxbK-eg`.

**Tab naming, not gid, is the reliable way to find the right week.** The
workbook has parallel per-week tabs, e.g. `"FBS Week 3"`, `"TOP 772 WEEK 3"`
(plus FCS/D2/D3/NAIA variants) — `fetch_sp_plus.py` uses `"FBS Week {N}"`.
**`week` is not auto-detected** — requesting a tab that doesn't exist
doesn't error, it silently returns the workbook's first (unrelated) tab
with HTTP 200, so guessing the week and trusting whatever comes back would
risk parsing the wrong table as current data. `fetch_sp_plus.py` instead
validates the response's header shape and raises clearly if it doesn't
match, but the week number itself still has to be set explicitly.

**`"TOP 772 WEEK N"` and `"FBS Week N"` are the same underlying ratings,
offset by a constant** — confirmed live across 7 teams: SP+ differs by
~+52.1, Off SP+ by ~+26.1, Def SP+ by ~-26.1, every time. `"TOP 772"`
covers all divisions (FBS down to NAIA) on one unified scale for
cross-division ranking; it is *not* the commonly-published SP+ despite the
identical column names — `"FBS Week N"`'s absolute values (e.g. Miami
25.6) match the standard public SP+ scale, `"TOP 772"`'s (77.7) don't.
Since Push only uses a *difference*, either tab gives the same gap, but
`fetch_sp_plus.py` uses `"FBS Week N"` so a spot-checked number matches
what a human would see cross-referencing another SP+ source.

**Team names don't always match CFBD's.** Confirmed live: CFBD's `"Miami"`
is `"Miami-FL"` in this sheet. `TEAM_NAME_ALIASES` in `fetch_sp_plus.py`
maps repo-wide names to the sheet's; a lookup miss raises with the closest
sheet names found, specifically so a new mismatch is easy to diagnose and
add rather than silently guessed at.

No team roster files (`config/rosters/{team}.yaml`) are committed — I
don't have real depth-chart knowledge of any team's actual current
starters, and fabricating one would put false information about real
players in the repo.

## Running the tests

```
pip install -r requirements.txt pytest
python3 -m pytest tests/ -v   # 44 tests, all passing
```

## Weekly usage

```
# 1. For each team in this week's matchup, copy the template and fill in
#    real starters from actual depth-chart reporting:
cp config/rosters/_template.yaml "config/rosters/Miami.yaml"

# 2. Set this week's matchup in config/teams.yaml (label/team_a/team_b/side/
#    week). `week` drives a live SP+ fetch automatically; sp_plus_gap is
#    just the fallback if that fetch fails.

# 3. Render:
python3 src/render_widget.py 2026-wk03-miami-wake
# -> output/latest.html, history/{label}.json, plus any caveats printed to stderr
```

## Running the pieces individually

```
# Composite scoring (no network needed):
python3 src/compute_composite.py --weight-diff-lbs 25 --sp-plus-gap 10 --net-returning-starters 2

# Live CFBD fetch (needs api.collegefootballdata.com allowlisted + CFBD_API_KEY set):
python3 src/fetch_cfbd.py Miami --year 2026
python3 src/fetch_roster.py Miami --year 2026
python3 src/fetch_talent.py Miami --year 2026

# Live SP+ fetch (needs docs.google.com allowlisted, no key required):
python3 src/fetch_sp_plus.py Miami "Wake Forest" --week 3
```
