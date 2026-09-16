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
  Experience normalization, weighting, verdict bands). Pure computation,
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
computes for real once Mass, Push, and Experience are all present.

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

`config/rosters/{team}.yaml` files for a real week's slate are now
populated automatically by `src/run_week.py` (see below) — no
hand-fabricated starters. As of this entry the source was live
ourlads.com depth charts; it's since been replaced by puntandrally.com
(`src/fetch_puntandrally.py`) — see the "Experience replaces Continuity"
entry near the end of this file. A team a source's index genuinely
doesn't cover (e.g. an FCS opponent of a ranked team) still needs a human
or WebSearch fallback, same idea either way.

## Scaling to a full week — `src/run_week.py`

Everything above (`fetch_cfbd.py`, `fetch_roster.py`, `fetch_talent.py`,
`fetch_sp_plus.py`, `render_widget.py`) scores **one matchup, one
direction**. `src/run_week.py` is the deterministic orchestrator that
scales that to **every Top-25-involving game in a week, scored in both
trench directions** ("four corners"):

- `src/fetch_matchups.py` discovers the week's slate live (`GET /games` +
  `GET /rankings`'s "AP Top 25" poll) — live-verified at 22 games/week
  (2026 week 3) out of 75 total FBS games, keeping any game with at least
  one ranked team. Merges with `config/teams.yaml`'s hand-curated entries
  by label (a human-pinned entry wins on a collision).
- `src/fetch_ourlads.py` fetches live depth charts from ourlads.com
  (plain `requests` — reachable that way even though this environment's
  `WebFetch` tool has an independent egress gate that a domain-allowlist
  change doesn't reach) for every unique team across the week, and
  `run_week.py` writes each team's `config/rosters/{team}.yaml` `starters`
  block directly. `prior_season_starters` / `continuity_note` are **not**
  touched — those stay a once-per-season human field.
- `render_widget.build_both_directions()` scores each game both ways
  (`team_a` OL vs `team_b` DL, and the reverse) from one fetch per team,
  not one fetch per direction. **Live-verified** (2026 week 3, Miami vs
  Wake Forest): Push is the exact negation between directions (+4.4 /
  -4.4), Mass and the starter lists differ correctly per direction.
- One bad matchup (unresolvable name, a CFBD error) is caught and
  reported — it never aborts the rest of the week's run. Same for one
  team's ourlads lookup failing.
- Roster changes are detected by diffing the freshly-fetched starter list
  against `config/rosters/{team}.yaml`'s *own current contents*, right
  before overwriting it — no search through `history/*.json` needed.

```
python3 src/run_week.py --year 2026 --week 3
# -> config/matchups/2026-wk03.yaml (discovered + hand-curated, merged)
# -> config/rosters/{team}.yaml updated for every team in the week
# -> output/2026-wk03/{matchup-label}.html (one page per game, both directions)
# -> output/2026-wk03/index.html (all games, one table)
# -> history/2026-wk03/{matchup-label}.json (both directions nested)
# -> printed summary: matchups rendered/failed, teams needing manual
#    roster research, teams with a starter change since last run
```

**Live-verified at full week-3 2026 scale**: 22/22 matchups rendered, 0
failed; 2 teams (both FCS opponents of a ranked team) flagged for manual
roster research; roster-diff correctly caught a real depth-chart change
(Wake Forest's DL order shifted since the prior run). At the time this
entry was written, Composite rendered as `None` with explicit caveats for
every game, since Continuity (the metric Experience has since replaced --
see the "Experience replaces Continuity" entry below) depended on a
once-per-season `prior_season_starters` field no team had filled in yet.

## Running the tests

```
pip install -r requirements.txt pytest
python3 -m pytest tests/ -v   # 118 tests, all passing
```

## Weekly usage

**Scaled (recommended) — every Top-25-involving game, both directions:**

```
python3 src/run_week.py --year 2026 --week 3
```

**Single hand-picked matchup, one direction — the original ad hoc tool:**

```
# 1. Copy the template and fill in real starters from actual depth-chart
#    reporting (run_week.py above does this automatically at scale):
cp config/rosters/_template.yaml "config/rosters/Miami.yaml"

# 2. Set this week's matchup in config/teams.yaml (label/team_a/team_b/side/
#    week). `week` drives a live SP+ fetch automatically; sp_plus_gap is
#    just the fallback if that fetch fails.

# 3. Render:
python3 src/render_widget.py 2026-wk03-miami-wake-forest
# -> output/latest.html, history/{label}.json, plus any caveats printed to stderr
```

## Running the pieces individually

```
# Composite scoring (no network needed):
python3 src/compute_composite.py --weight-diff-lbs 25 --sp-plus-gap 10 --experience-diff-pct 20

# Live CFBD fetch (needs api.collegefootballdata.com allowlisted + CFBD_API_KEY set):
python3 src/fetch_cfbd.py Miami --year 2026
python3 src/fetch_roster.py Miami --year 2026
python3 src/fetch_talent.py Miami --year 2026

# Live SP+ fetch (needs docs.google.com allowlisted, no key required):
python3 src/fetch_sp_plus.py Miami "Wake Forest" --week 3

# Live puntandrally roster + snap-count fetch (drives a real headless
# Chromium browser via Playwright -- see fetch_puntandrally.py's docstring
# for why; `playwright install chromium` needed once):
python3 src/fetch_puntandrally.py Miami --year 2026
```

## puntandrally.com replaces ourlads.com as the roster source

`src/fetch_puntandrally.py` is now what `src/run_week.py` calls for live
roster population, not `src/fetch_ourlads.py` (still in the repo,
unused, as a fallback path). puntandrally is a strict superset — real
per-player snap counts, not just starter names — and its team-name index
resolves all 138 CFBD FBS teams with zero aliases needed (confirmed live,
`scripts/check_team_name_coverage.py`). It sits behind a Cloudflare JS
challenge plain `requests` can't solve, so this module drives headless
Chromium via Playwright instead — confirmed live this needs no
stealth/anti-detection trickery, just a normal `headless=True` launch.
`fetch_puntandrally.browser_session()` shares one browser process across
a whole run (roster population + rendering) rather than relaunching
Chromium per team.

**Cloud-Routine caveat:** confirmed live that a cloud-sandboxed Routine
environment's egress proxy hard-denies `puntandrally.com:443` — this
module was built and validated from a local machine session instead,
which has no such restriction. A Routine-fired cloud run should expect
puntandrally calls to fail there and needs ourlads as its fallback until
that's resolved (see DESIGN.md Section 7).

## Experience replaces Continuity

`src/fetch_talent.py`'s Continuity metric used to be a human hand-typing
`prior_season_starters` into each team's YAML once a season, then a bare
name-overlap count against the current starters. That dependency was a
real gap: the first live end-to-end `run_week.py` run for Miami vs Wake
Forest (2026 Week 3) rendered `composite: None` for both directions
because neither team had that field filled in yet.

puntandrally's `year=` query param (confirmed live to return real,
accurate full-season snap counts for prior seasons, back to at least
2022) made that field obsolete as the primary source: `fetch_talent.py`
now computes **Experience** automatically — what share of this year's
starters' snaps, at their own team, were played by the same players last
season. A transfer-in or true freshman scores 0% (never excluded, so it
correctly drags the average down); a transfer's snaps at their OLD school
are never counted, since the lookup only ever fetches one team's own
page. `prior_season_starters` is kept as an optional fallback only, used
if the live year-over-year fetch fails for a team.

`compute_composite.py`'s formula, `config/weights.yaml`, `render_widget.py`,
and `templates/widget.html.jinja` were all updated to match (`continuity`
→ `experience` throughout) — see DESIGN.md Sections 4c/5 for the exact
formula (1 point per 10 percentage-points of returning-snap-share
differential, capped ±10, a starting hunch like Mass's and Push's own
constants). Live-verified: re-running `run_week.py` for Miami vs Wake
Forest after this change produced real starter lists sourced from
puntandrally and a `roster_source: puntandrally.com` field in both teams'
YAML files.

A related idea from this session — using historical game outcomes (ATS
performance specifically, plus real advanced OL/DL metrics already
pulled from SP+/CFBD as an "expected vs. actual" check) to backtest which
scoring components actually predict anything — was discussed but
deliberately **not built yet**; it needs more design work (a betting-line
data source hasn't been investigated at all) before it's buildable. See
DESIGN.md Section 8.
