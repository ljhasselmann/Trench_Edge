# Trench Edge — Design Doc

Status: draft v0.1
Owner: (you)
Automation target: Claude Code Routine (research preview)

## 1. Purpose

Trench Edge is a weekly, automatically-refreshed composite score comparing the
offensive line of one college football team against the defensive line of
another (and vice versa, once the four-corners version ships). It formalizes
the "mass kicks ass" heuristic into three measurable, individually-labeled
components — Mass, Push, Experience, and Recruiting Talent Differential — instead of a single vibes-based
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
    weights.yaml               Mass/Push/Experience/Recruiting weights, tunable
    teams.yaml                 hand-curated matchup(s) to score/override
    matchups/
      2026-wk03.yaml            this week's discovered + merged matchups
    rosters/
      _template.yaml            schema for a per-team roster file
      {team}.yaml                starters (weekly) + optional prior_season_starters
                                  fallback + continuity_note (once per season, human-set)
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
4. Each starter entry also carries, where the source has it, `jersey`,
   `class_year` (FR/SO/JR/SR/GR), `snaps_multi_year` (a
   `fetch_puntandrally.DEFAULT_SNAP_HISTORY_YEARS`-season sum, NOT a true
   career total — see that module's docstring), and `recruit_rating`/
   `recruit_stars` (from `fetch_247sports.py`, see Section 4c) — all
   populated automatically by `run_week.py` alongside the starter name
   itself, rendered in the widget's starters table.

### 4c. Tier 2 — talent, experience, and recruiting (`fetch_talent.py`, `fetch_247sports.py`)

- **Returning experience, by position group (OL, DL) — automated, not
  human-typed.** `fetch_puntandrally.py`'s `year=` query param gives real,
  accurate full-season snap counts for prior seasons (confirmed live back
  to at least 2022). `compute_experience_inputs` matches this year's
  starters (from `config/rosters/{team}.yaml`, already staged by
  `run_week.py`) against THAT SAME TEAM's snap shares last season: what
  share of this year's starters' snaps, at their own team, were played by
  the same players last year. A transfer-in or true freshman contributes
  0% (never excluded), and a transfer's snaps at their OLD school never
  count, since the lookup only ever fetches one team's own page. The old
  approach — a human hand-typing `prior_season_starters` into YAML once a
  season, then counting bare name-overlap — is kept only as a fallback for
  when the live year-over-year fetch fails for a team (site issue, or a
  team predating puntandrally's reliability floor); it's a coarser,
  non-snap-weighted proxy when used.
- **247/On3 team talent composite or blue-chip ratio, position-group-
  specific — resolved as its own separate Section 5 scoring component,
  Recruiting Talent Differential, not folded into Experience.**
  `fetch_247sports.py` fetches each current starter's real 247Sports
  composite rating (0-100) and star count directly from that team's own
  roster page — confirmed live to sit behind the same class of
  bot-detection puntandrally.com does (plain `requests` 403s; a plain,
  non-stealth headless Playwright launch gets through cleanly, no
  evasion needed) and to need no cross-referencing of recruiting-class
  archives: one roster page per team lists jersey/position/class-year/
  rating for the whole CURRENT roster at once, including transfers. Like
  jersey/class_year/snaps_multi_year, this is staged onto each starter's
  `config/rosters/{team}.yaml` entry by `run_week.py` at populate time
  (`recruit_rating`, `recruit_stars`) — `compute_recruiting_talent_inputs`
  is then pure, no network, just averaging the already-staged rating per
  side. A starter with no rating (unrated walk-on, or unmatched against
  247Sports) is excluded from the average, not counted as 0.
- A qualitative flag, set manually per team per season: is this unit's
  performance level talent-driven or coaching/scheme-driven? (E.g., Wake
  Forest's 2025 defensive turnaround was explicitly coaching-driven per beat
  reporting — that's a real signal about year-over-year stability that a
  bare "returning starters" count won't capture on its own.) This flag has
  no automated equivalent and isn't part of the numeric score — it's
  surfaced as a caveat only, same as before.

## 5. Scoring model

`compute_composite.py` implements:

```
Trench Edge = w_mass * Mass + w_push * Push + w_experience * Experience
              + w_recruiting * Recruiting
```

Each subscore is normalized to a **-10 (favors Team B) to +10 (favors Team A)**
scale before weighting:

| Component | Normalization | Notes |
|---|---|---|
| Mass | 1 point per 10 lbs of average weight differential, capped at ±10 | Simplest, most reliable input — pure roster data, no adjustment needed |
| Push | 1 point per 5 points of **overall** SP+ differential (`team_a.SP+ - team_b.SP+`), capped at ±10 | See below — resolved decision, not the original placeholder |
| Experience | 1 point per 10 percentage-points of returning-snap-share differential, capped at ±10 | Replaced Continuity's bare returning-starter count — see Section 4c. A starting hunch, same spirit as Mass's and Push's constants above, not fitted to anything yet |
| Recruiting | 1 point per 5 points of average 247Sports composite-rating (0-100 scale) differential, capped at ±10 | See Section 4c. A starting hunch — real P4-vs-P4 OL/DL rating gaps seen live so far run roughly 0-15 points |

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
mass: 0.3
push: 0.3
experience: 0.2
recruiting: 0.2
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
  each week. Superseded twice now:
  - First by `src/fetch_ourlads.py` (plain `requests`, covers 137 of 138
    FBS teams — only Washington State is genuinely absent from its index).
  - Then, for **local/manual runs**, by `src/fetch_puntandrally.py` —
    confirmed live to resolve all 138 FBS teams with zero name aliases,
    and it also carries real per-player snap counts ourlads never had
    (see Section 4c's Experience metric). puntandrally.com sits behind a
    Cloudflare JS challenge plain `requests` can't solve, so this module
    drives headless Chromium via Playwright instead — a real browser
    process, not just an HTTP call, and confirmed live to need no
    stealth/anti-detection trickery to get through. `src/run_week.py`
    opens ONE shared browser session for the whole run (roster population
    AND rendering's Experience lookups both use it) rather than launching
    Chromium per team.

  `fetch_ourlads.py` is left in the repo, unused by `run_week.py` now, as
  a fallback path if puntandrally ever becomes unreachable from wherever
  a run executes (see the cloud-Routine caveat under Network access,
  below) — that's a per-failure fallback, never a per-team double-fetch.
  Only a team neither source can resolve falls back to the Routine doing
  WebSearch/WebFetch research itself — bounded to an explicit short list
  `run_week.py` reports, not agentic judgment across every team every week.
  `prior_season_starters` / `continuity_note` are **not** touched by this
  automatic path — those stay a once-per-season human field, per
  `_template.yaml` (`prior_season_starters` is now an optional fallback
  only, used if puntandrally's own live year-over-year snap match fails
  for a team — see Section 4c); weight/`confirmed` status still only ever
  comes from matching CFBD's live `/roster` (Section 4b).
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
  separate setup needed unless a new environment is created later.
  **puntandrally.com is a different story per environment class:**
  confirmed live that a cloud-sandboxed Routine environment's egress proxy
  hard-denies `puntandrally.com:443` (a policy decision, not a code-side
  problem) — so `fetch_puntandrally.py` was built and wired in from a
  local machine session instead, which has no such restriction and can
  run the Playwright/Chromium browser this module needs anyway (a cloud
  sandbox may not have a usable display/browser runtime for that even if
  the network egress were allowed). Until that's resolved for the cloud
  Routine specifically, a Routine-fired run should expect puntandrally
  calls to fail there and fall back to ourlads for roster population — the
  local/manual `run_week.py` invocation this was validated against
  (2026 Week 3, Miami vs Wake Forest) is the one puntandrally actually
  works end-to-end for today. `247sports.com` (`fetch_247sports.py`,
  Recruiting Talent Differential) is very likely the same story — it
  sits behind the same class of bot-detection, solved the same way (a
  plain, non-stealth headless Playwright launch), and hasn't been tried
  from the cloud Routine environment specifically yet; assume it needs
  the same local-machine treatment until checked.
- **Secrets:** `CFBD_API_KEY` lives in the environment, never in the
  Routine's prompt or committed to the repo. `fetch_sp_plus.py`,
  `fetch_ourlads.py`, `fetch_puntandrally.py`, and `fetch_247sports.py`
  need no key at all — all four are unauthenticated (247Sports' core
  roster/rating data is publicly visible, no login needed, confirmed live).
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
- **Backtesting — real ATS data, now built.** `scripts/backfill_game_results.py`
  attaches each game's real final score (CFBD's `/games`, via
  `fetch_matchups.fetch_game_results` — already-fetched data, no new
  endpoint) AND a real closing-line ATS result to its `history/*.json`
  snapshot, run after that week's games are final (`run_week.py` itself
  runs pre-kickoff and can't know scores yet). The ATS line comes from a
  genuine find: the SAME "FBS Week N" Google Sheet tab this repo already
  reads for Push also carries a full per-game schedule with real betting
  spreads and Bill Connelly's own ATS picks (`Game`, `Spread`, `ATS Pick`
  columns to the left of the ratings table `fetch_sp_plus.py` used to stop
  reading at) — confirmed live against the real Week 3 Miami/Wake Forest
  line, `"Miami-FL -22.5"`. `scripts/analyze_ats_correlation.py` then
  reports sign agreement + Pearson r between the composite (and each
  subscore) and `cover_margin_for_team_a` — how many points BETTER than
  the closing line's own expectation a team performed, not raw margin,
  since raw margin conflates "this team is good" (already priced into the
  spread) with "beat what was already expected." Reports only, never
  tunes weights; leads with sample size and an explicit "not
  statistically meaningful yet" flag below 30 data points, which is where
  this repo's real history sits today (2026 Week 3 games weren't final
  yet as of the last live run).
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
