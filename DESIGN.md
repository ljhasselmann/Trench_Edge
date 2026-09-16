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
    teams.yaml                 hand-curated matchup(s) to score/override
    matchups/
      2026-wk03.yaml            this week's discovered + merged matchups
    rosters/
      _template.yaml            schema for a per-team roster file
      {team}.yaml                starters (weekly) + prior_season_starters/
                                  continuity_note (once per season, human-set)
  src/
    fetch_cfbd.py               pulls Tier 1 advanced stats from CFBD
    fetch_roster.py             pulls or reads cached OL/DL starter weights
    fetch_talent.py             pulls Tier 2 talent/returning-production data
    fetch_sp_plus.py            pulls Push (overall SP+ differential)
    fetch_ourlads.py            live depth charts (deterministic roster source)
    fetch_matchups.py           discovers this week's Top-25-involving games
    team_names.py                canonical team-name alias reconciliation
    compute_composite.py        applies the scoring formula, normalizes inputs
    render_widget.py            fetch/combine split; renders one matchup,
                                  one game (both directions), or the index
    run_week.py                  orchestrator: discovery -> rosters -> render
                                  for a whole week, per Section 7
  scripts/
    check_team_name_coverage.py  offline diagnostic, run before scale-out
  templates/
    widget.html.jinja           one trench direction (OL vs DL)
    game.html.jinja              one game, both directions
    index.html.jinja             one week, every game, summary table
  history/
    2026-wk03/
      {matchup-label}.json       one snapshot per game per week, both directions
  output/
    latest.html                 most recent single-matchup ad hoc render
    2026-wk03/
      {matchup-label}.html       one page per game, both directions
      index.html                 the week's summary page
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
(Google Sheet). Fetched live by `src/fetch_sp_plus.py` via Google's
unauthenticated `gviz` query endpoint — see that module's docstring and
README.md's "SP+ source" section for real gotchas this hit (a different,
dynamically-named redirect host on the naive export endpoint; a
nonexistent-tab request returning HTTP 200 with the wrong tab's data
instead of erroring; a name mismatch against CFBD for at least one team).
The sheet's per-week tabs are named `"FBS Week {N}"` — `N` isn't
auto-detected (see above on why guessing it is unsafe), so it's set
explicitly as `week` per matchup in `config/teams.yaml`.

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

- **Scope: every Top-25-involving FBS game, not one hand-picked matchup,
  scored in both trench directions.** `src/fetch_matchups.py` discovers
  the week's slate live (`GET /games` + `GET /rankings`'s "AP Top 25"
  poll) and keeps a game if either team is ranked — live-verified at
  ~22 games/week out of ~75 total FBS games. Every discovered game is
  scored as "four corners": both `team_a`-OL-vs-`team_b`-DL and the
  reverse direction, since a game isn't fully characterized by only one
  side's trench matchup. `config/teams.yaml` entries for the same
  year/week are merged in (a human-pinned entry wins on a label
  collision) rather than replaced — see `src/run_week.py`.
- **Deterministic roster research first, WebSearch only as a bounded
  fallback — not WebSearch-first for every team.** An earlier draft of
  this section had the Routine web-search every team's starters itself
  each week. Superseded: `src/fetch_ourlads.py` fetches live depth charts
  directly from ourlads.com (confirmed live, covers 137 of 138 FBS teams
  under `fetch_ourlads.TEAM_NAME_ALIASES`; only Washington State is
  genuinely absent from ourlads's index, not just misnamed) via plain
  `requests` — reachable that way even though this environment's
  `WebFetch` tool has an independent egress gate that doesn't pick up a
  domain allowlist change. `src/run_week.py` calls this for every unique
  team across the week's matchups (deterministic, ~45 teams/week, a
  0.75s courtesy delay between calls) and writes
  `config/rosters/{team}.yaml`'s `starters` block directly. Only a team
  ourlads can't resolve (name miss, page-structure change) falls back to
  the Routine doing WebSearch/WebFetch research itself — bounded to an
  explicit short list `run_week.py` reports, not agentic judgment across
  every team every week.
  `prior_season_starters` / `continuity_note` are **not** touched by this
  automatic path — those stay a once-per-season human field, per
  `_template.yaml`; weight/`confirmed` status still only ever comes from
  matching CFBD's live `/roster` (Section 4b).
- **Roster-diff simplification: compare against the team's own current
  config file, not a `history/*.json` search.** The original design
  described diffing against "the most recent prior snapshot in
  `history/*.json`, matched by team name" — an expensive, fragile search
  once there are dozens of teams and files across many weeks.
  `run_week.py` instead diffs the freshly-fetched starter list against
  `config/rosters/{team}.yaml`'s *current* contents, right before
  overwriting it — O(1) per team, no search needed, and reported in the
  run summary.
- **Push (SP+) is a live fetch too, not a manual paste.** Originally
  thought to need Claude's Drive connector, which this org can't grant to
  a Routine at all (`create_trigger`'s `connectors` parameter is rejected
  org-wide, confirmed) — the intended workaround was having the Routine
  ask a human to read the sheet and paste the number each week. Superseded
  again once `docs.google.com` was added to this environment's network
  allowlist: `fetch_sp_plus.py` reads the sheet as a plain REST call (see
  Section 5 and its own docstring for the real gotchas that took — a
  different, dynamically-named redirect host, and a nonexistent-tab
  request that silently returns the wrong tab's data instead of erroring).
  `run_week.py` fetches the whole week's SP+ table and CFBD's talent list
  **once per run**, not once per matchup, and passes them to every
  matchup's scoring call.
- **What's left for a human, then:** effectively nothing on a normal
  week. `run_week.py`'s printed summary — matchups discovered/rendered/
  failed, teams needing manual roster research, teams with a starter
  change since last run — is what the Routine reads and folds into its
  commit message; a human (or the Routine's WebSearch fallback) only
  steps in for the specific teams/matchups that summary flags.
- **Repository:** this repo, on the environment's already-checked-out
  working copy — not a fresh clone per firing, since the Routine is
  session-bound rather than fresh-session. `output/{year}-wk{week:02d}/`
  and `history/{year}-wk{week:02d}/` (one file per game, both directions
  nested) still get committed every run regardless, so the record doesn't
  depend on session persistence either way.
- **Network access:** confirmed resolved for this project's environment —
  `api.collegefootballdata.com`, `docs.google.com`, and `ourlads.com` are
  all allowlisted for plain `requests` calls, `CFBD_API_KEY` is set. A
  Routine fired with no explicit `environment_id` inherits the calling
  session's environment, so it reuses this config automatically; no
  separate setup needed unless a new environment is created later. (A
  third candidate roster source, puntandrally.com, was checked live and
  is **not** currently reachable — a hard proxy-level policy denial, not
  a code-side problem — so it isn't wired in; ourlads alone is the
  roster source until/unless that's resolved in a future environment.)
- **Secrets:** `CFBD_API_KEY` lives in the environment, never in the
  Routine's prompt or committed to the repo. `fetch_sp_plus.py` and
  `fetch_ourlads.py` need no key at all — both are unauthenticated.
- **Routine prompt (current, not a draft):** "Run
  `python3 src/run_week.py --year <current season> --week <this week's
  number>`. It discovers every Top-25-involving FBS game, populates
  `config/rosters/{team}.yaml` for every team from live ourlads.com depth
  charts, scores each game in both trench directions, and writes
  `output/{year}-wk{week:02d}/` (one page per game plus an index) and
  `history/{year}-wk{week:02d}/`. Read its printed summary: for any team
  it reports as needing manual roster research (ourlads lookup failed),
  web-search for that team's current starting OL/DL yourself and update
  `config/rosters/{team}.yaml` with sourced names only (never fabricated),
  then re-run. Commit and push `output/`, `history/`, and any
  `config/rosters/`/`config/matchups/` changes, calling out in the commit
  message the run summary's counts, any team that needed manual research,
  and any starter change it flagged. Stop and ask rather than push a
  broken or empty report if CFBD is unreachable or the week has zero
  discovered matchups."

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
