# Trench Edge — Design Doc

Status: draft v0.1
Owner: (you)
Automation target: Claude Code Routine (research preview)

## 1. Purpose

Trench Edge is a weekly, automatically-refreshed composite score comparing the
offensive line of one college football team against the defensive line of
another (and vice versa, once the four-corners version ships). It formalizes
the "mass kicks ass" heuristic into three measurable, individually-labeled
components — Mass, Push, and Continuity — instead of a single vibes-based
gut call.

It is explicitly **not** a standalone betting signal. It's a supplementary
lens for evaluating one specific phase of a matchup (the trenches), meant to
sit alongside — not replace — market-derived signals (spread, SP+ diffs)
covered elsewhere in this project's broader workflow. Composite scores here
should never be the sole input to a betting decision.

## 2. Non-goals

- Not a full-game prediction model. It scores one phase of the game.
- Not a replacement for SP+, FPI, or market lines — a cross-check.
- Not claiming precision beyond what the inputs support. Every number in the
  output should be traceable to a source or flagged as estimated.
- Not fully autonomous decision-making. The routine produces a report; a
  human reads it before doing anything with it.

## 3. Repo layout

```
trench-edge/
  .env                        CFBD_API_KEY=... (gitignored, never committed)
  .gitignore                  .env, /history/*.json (or keep history, TBD)
  config/
    weights.yaml               Mass/Push/Continuity weights, tunable
    teams.yaml                 this week's matchup(s) to score
  src/
    fetch_cfbd.py               pulls Tier 1 advanced stats from CFBD
    fetch_roster.py             pulls or reads cached OL/DL starter weights
    fetch_talent.py             pulls Tier 2 talent/returning-production data
    compute_composite.py        applies the scoring formula, normalizes inputs
    render_widget.py            generates the HTML output from a template
  templates/
    widget.html.jinja           matches the visual style already established
  history/
    2026-wk03-miami-wake.json   one snapshot per matchup per week
  output/
    latest.html                 most recent rendered widget
  DESIGN.md                     this file
  README.md                     quickstart for a human picking this up cold
```

## 4. Data pipeline

Three fetch stages, run in sequence, each producing a JSON blob that gets
merged before scoring:

### 4a. Tier 1 — CFBD advanced stats (`fetch_cfbd.py`)

Endpoint: `/stats/season/advanced?year={year}&team={team}`

Pulls, per team, per side of the ball:
- Stuff rate (run stopped at or behind LOS)
- Line yards / opportunity rate
- Front-seven havoc rate (TFL + forced fumbles, front seven only — explicitly
  excludes DB havoc, which is a different skill and shouldn't leak into a
  trenches score)
- Adjusted sack rate (sacks per dropback, not raw counts)

Auth via `Authorization: Bearer $CFBD_API_KEY`, key read from environment,
never hardcoded or logged.

**Known limitation:** CFBD's advanced stats are season-cumulative, not
opponent-specific. Early season (weeks 1-3), these numbers still carry real
small-sample noise and garbage-time contamination from lopsided games,
same caveat that applied to the SP+ diffs earlier in this project. Weight
this component's confidence down accordingly until ~5-6 games of data exist.

### 4b. Mass — roster weights (`fetch_roster.py`)

This is the piece that's currently hand-scraped (see the Miami/Wake Forest
worked example — it took a dozen manual searches). Options, in order of
preference:

1. Check if CFBD's `/roster` endpoint returns listed weights — if yes, this
   whole stage becomes one API call instead of manual research.
2. If not, maintain a small cached YAML per team (`config/rosters/{team}.yaml`)
   that a human updates once per week from depth-chart reporting, with a
   `source` and `confidence` field per player (`confirmed` vs `estimated`).
   Carry forward the exact convention used in the worked example: unlisted
   weights get flagged with an asterisk and a note, never silently guessed
   into a "confirmed" number.
3. Depth charts change (injuries, suspensions, true freshmen beating out
   incumbents mid-season, as happened with Cantwell/McCoy). The routine
   should flag when it's using a roster snapshot older than 7 days rather
   than silently using stale starters.

### 4c. Tier 2 — talent and continuity (`fetch_talent.py`)

- Returning production / returning starts, by position group (OL, DL)
- 247/On3 team talent composite or blue-chip ratio, position-group-specific
  where available, team-wide as fallback
- A qualitative flag, set manually per team per season: is this unit's
  performance level talent-driven or coaching/scheme-driven? (E.g., Wake
  Forest's 2025 defensive turnaround was explicitly coaching-driven per beat
  reporting — that's a real signal about year-over-year stability that a
  bare "returning starters" count won't capture on its own.)

## 5. Scoring model

`compute_composite.py` implements:

```
Trench Edge = w_mass * Mass + w_push * Push + w_continuity * Continuity
```

Each subscore is normalized to a **-10 (favors Team B) to +10 (favors Team A)**
scale before weighting:

| Component | Normalization | Notes |
|---|---|---|
| Mass | 1 point per 10 lbs of average weight differential, capped at ±10 | Simplest, most reliable input — pure roster data, no adjustment needed |
| Push | 1 point per 5 points of **overall** SP+ differential (`team_a.SP+ - team_b.SP+`), capped at ±10 | See below — resolved decision, not the original placeholder |
| Continuity | 1 point per net returning-starter differential | Discount further if either side's improvement looks scheme-driven per the qualitative flag above |

**Push, resolved:** an earlier draft of this formula tried an "off-vs-def"
combination — `team_a`'s Off. SP+ plus `team_b`'s Def. SP+ (their Def. SP+
is already negative-signed when good, so this adds correctly) — to keep it
trench-specific. Rejected: those combined values run ~40-70 for almost any
real matchup, so `/5` saturates the ±10 cap on nearly every game and the
component stops discriminating. **Overall SP+ diff instead** — plain
`team_a.SP+ - team_b.SP+` — stays inside the scale the `/5` divisor was
actually calibrated against (the typical range analysts already discuss as
an SP+-implied point spread), at the cost of not being trench-specific (it's
whole-team, same tradeoff CFBD's Tier 1 stats were brought in to fix for
Mass — Push just isn't there yet). Replacing this with a real Stuff
Rate/Line Yards-based formula remains open (needs a league-average baseline
to regress each team's effect against; CFBD doesn't provide one, and
guessing at a formula without it would misrepresent precision the data
doesn't support — see Section 2).

**SP+ source:** Bill Connelly's own weekly-refreshed SP+ spreadsheet
(Google Sheet, not a REST API — see Section 7's routine notes). The sheet
keeps multiple undated snapshot tabs; identify the current one by matching
each team's win-loss record in the table against the actual current week,
not by tab position or gid.

Default weights (`config/weights.yaml`), tunable, starting point:

```yaml
mass: 0.4
push: 0.4
continuity: 0.2
```

These are a starting hunch, not a fitted model. See Section 8 for the
backtesting plan that should eventually replace hand-picked weights with
something empirically justified.

## 6. Output

`render_widget.py` produces a self-contained HTML fragment matching the
style already established: metric cards for average weight, a player-level
weight comparison list with confirmed/estimated flags, three diverging bars
for the subscores, and a final composite bar with a plain-language verdict
band (negligible / slight-to-moderate / significant / dominant edge, keyed
off `|composite|` thresholds — e.g. 0-2 / 2-5 / 5-8 / 8-10).

Every rendered number must be traceable: the widget (or an adjacent data
file) should carry the source and fetch timestamp for each input, not just
the final score. If Section 4b falls back to a cached/estimated roster
weight, that should visibly propagate as a caveat in the rendered output,
not get silently absorbed into a clean-looking number.

## 7. Automation — Claude Code Routine

Real mechanism, not the "research preview" placeholder this section
originally assumed: a **Routine** (`create_trigger`), a cron-scheduled
trigger that can either resume a persistent session or spawn a fresh one
per firing.

- **Trigger:** weekly, **Thursday** (not Tuesday as originally drafted —
  depth charts publish later than assumed), 8am US Eastern. Self-bound to
  a persistent session rather than a fresh one per firing, specifically
  *because* the run needs a human confirmation step (see below) — a fresh
  session can't pause mid-run for a reply.
- **Confirm-then-run, one Routine, not two.** Earlier drafts of this
  section considered a separate Sunday reminder plus a fresh-session
  Tuesday run. Rejected: Mass and Continuity both depend on a human-updated
  starter list (`config/rosters/{team}.yaml`, Section 4b/4c), which can't
  be confirmed by an unattended fresh session anyway, so splitting the
  steps added a second moving part for no gain. One Routine: check
  matchup/roster-config staleness, confirm updates with the human, then run
  the full pipeline in the same session.
- **Repository:** this repo, on the environment's already-checked-out
  working copy — not a fresh clone per firing, since the Routine is
  session-bound rather than fresh-session. `history/` and `output/` still
  get committed every run regardless, so the record doesn't depend on
  session persistence either way.
- **Network access:** confirmed resolved for this project's environment —
  `api.collegefootballdata.com` is allowlisted and `CFBD_API_KEY` is set.
  A Routine fired with no explicit `environment_id` inherits the calling
  session's environment, so it reuses this config automatically; no
  separate setup needed unless a new environment is created later.
- **Secrets:** `CFBD_API_KEY` lives in the environment, never in the
  Routine's prompt or committed to the repo.
- **Push (SP+) needs the Google Drive connector, not a REST call.** Unlike
  CFBD, the SP+ source (Section 5) is a Google Sheet, reachable only
  through Claude's own Drive connector — a plain script (`fetch_*.py`)
  can't call it directly the way `fetch_cfbd.py` calls CFBD. The Routine
  must be created with `connectors: ["Google Drive"]`, and its prompt must
  include the read-and-extract step explicitly (identify the current tab
  by matching this week's actual win-loss records against the sheet, per
  Section 5 — the sheet doesn't label which tab is current).
- **Routine prompt (draft, updated):** "Check `config/rosters/*.yaml` for
  matchups in `config/teams.yaml` — flag any starter list older than 7
  days or any team missing a roster file entirely, and confirm updates
  with the user before proceeding. Once confirmed: fetch Tier 1
  (`fetch_cfbd.py`) and Tier 2 (`fetch_talent.py`) data, read this week's
  SP+ gap from the Google Sheet (fileId in README.md) for Push, run
  `render_widget.py` to produce `output/latest.html`, commit a snapshot to
  `history/`, and flag in the commit message any input that fell back to a
  cached or estimated value."

## 8. Open questions / future work

- **Four corners.** Current worked example only did Team A's OL vs Team B's
  DL. The reverse side (Team B's OL vs Team A's DL) needs the same treatment
  before any matchup is "fully scored."
- **Backtesting.** Once a few weeks of history accumulate, check whether the
  composite (or any single component) actually correlates with something —
  ATS results, rushing success rate in the actual game, sacks allowed. If it
  doesn't beat a naive baseline, that's a real finding, not a failure —
  report it honestly rather than tuning weights until it looks predictive.
- **CFBD roster weights — resolved.** `/roster` does carry listed weight
  directly, confirmed live (~93-100% coverage across three teams checked).
  Section 4b's manual-research stage for raw weights is gone; a human still
  has to say *who's starting* (CFBD has no depth-chart data anywhere in its
  API), but that's a much smaller weekly task than sourcing every weight.
- **PFF grades.** Tier 3, paywalled, inconsistent public availability. Not
  wired into v1. Revisit if a subscription or a reliably-quoted public proxy
  turns up.
- **Alerting — resolved, this assumption was wrong.** Routines fired as a
  fresh session per firing *do* support push and email completion
  notifications directly (`notifications: {push, email}` on
  `create_trigger`). Not relevant to this project's Routine as designed
  (self-bound to a persistent session for the confirm step, which doesn't
  take that parameter), but the underlying capability exists now, contrary
  to what this section originally assumed.
