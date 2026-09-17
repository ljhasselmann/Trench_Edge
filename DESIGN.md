# Trench Edge — Design Doc

Status: draft v0.1
Owner: (you)
Automation target: Claude Code Routine (research preview)

## 1. Purpose

Trench Edge is a weekly, automatically-refreshed composite score comparing the
offensive line of one college football team against the defensive line of
another (and vice versa, once the four-corners version ships). It formalizes
the "mass kicks ass" heuristic into four measurable, individually-labeled
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
    fetch_ourlads.py            live depth charts (authoritative roster + real slot/scheme source)
    fetch_puntandrally.py       by-name enrichment: jersey/class/multi-year snaps, Experience's snap-share
    fetch_247sports.py          by-name enrichment: real recruiting rating/stars
    fetch_matchups.py           discovers this week's Top-25-involving games
    team_names.py                canonical team-name alias reconciliation
    compute_composite.py        applies the matchup-differential scoring formula
    compute_ol_rank.py           TrenchEdge Offense Ranking -- absolute,
                                  league-wide OL score (Section 5b), plus
                                  the per-lineman player-card export
    render_widget.py            fetch/combine split; renders one matchup,
                                  one game (both directions), or the index
    run_week.py                  orchestrator: discovery -> rosters -> render
                                  for a whole week, per Section 7
  scripts/
    check_team_name_coverage.py  offline diagnostic, run before scale-out
    populate_all_fbs_rosters.py  backfills config/rosters/*.yaml league-wide
                                  (prerequisite for the OL Rank -- Section 5b)
    backfill_game_results.py     attaches real final scores + ATS results
                                  to history/*.json after games are final
    analyze_ats_correlation.py   reports composite-vs-ATS correlation (Section 8)
    analyze_ol_rank_correlation.py reports OL-Rank-vs-offensive-output correlation
    _stats.py                     shared pearson_correlation, used by both
                                  analyze_*_correlation.py scripts
  templates/
    widget.html.jinja           one trench direction (OL vs DL)
    game.html.jinja              one game, both directions
    index.html.jinja             one week, every game, summary table
  history/
    2026-wk03/
      {matchup-label}.json       one snapshot per game per week, both directions
      ol_rank_table.csv/.json    league-wide Offense Ranking (opt-in, Section 5b)
      lineman_stats.csv/.json    per-OL-starter player-card export (Section 5b)
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
- Power success rate (short-yardage run conversion — used by
  `compute_ol_rank.py`'s Performance attribute, Section 5b; not scored
  anywhere in the matchup-differential composite above)
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

**Depth chart source: ourlads.com (`fetch_ourlads.py`), not puntandrally.**
This reverted back to ourlads (2026-09-16) after puntandrally's
top-N-by-snaps ranking was confirmed live to misrepresent an actual
starter: Miami's Jackson Cantwell was puntandrally's highest-snap "T"
(tackle), but ourlads' real depth chart — and Miami's actual usage —
has him at LG (left guard) this week. puntandrally's usage-derived list
is a proxy for "who starts," not an actual depth chart; ourlads publishes
the real, human-maintained one, with genuine left/right slots
(`LT`/`LG`/`C`/`RG`/`RT`, `LDE`/`RDE`/`DT`/`NT`/... — position row labels
vary by team, per that module's own docstring) and each side's actual
scheme name (e.g. "Air Raid" / "4-2-5", also confirmed live) — see
`fetch_ourlads.TeamDepthChart`. `starters_with_labels()` returns each
starter already ordered left-to-right by that real slot (`OL_ROW_ORDER`/
`DL_ROW_ORDER`), so the front size a team actually plays (e.g. a real
3-4's three down linemen) comes through as-is — no more hardcoded
"assume 4 down linemen" assumption.

puntandrally and 247Sports are now pure **by-name enrichment** sources
on top of ourlads' starter list, matched via
`fetch_puntandrally.resolve_any_name_match` (the two sites don't always
spell a name identically — a truncated initial, a dropped generational
suffix, or a stripped accent mark, confirmed live for all three cases;
never guesses when a match is ambiguous):

1. CFBD's `/roster` endpoint supplies weight, cross-referenced against
   the ourlads-sourced starter by name — `confirmed` when matched,
   `estimated` when a human-supplied fallback weight exists in config
   instead, excluded from the Mass average otherwise (never guessed).
2. Depth charts change (injuries, suspensions, true freshmen beating out
   incumbents mid-season, as happened with Cantwell/McCoy). `run_week.py`
   flags a starter-list change week over week; a roster snapshot older
   than 7 days is flagged as stale rather than silently trusted.
3. Each starter entry also carries `position_tag` (ourlads' own real
   slot label) plus, where puntandrally/247Sports match by name,
   `jersey`, `class_year` (FR/SO/JR/SR/GR), `snaps_multi_year` (a
   `fetch_puntandrally.DEFAULT_SNAP_HISTORY_YEARS`-season sum, NOT a true
   career total — see that module's docstring), and `recruit_rating`/
   `recruit_stars` (from `fetch_247sports.py`, see Section 4c). The
   team-wide `offense_scheme`/`defense_scheme` (ourlads' own labels) are
   staged at the top of `config/rosters/{team}.yaml`. All of this is
   populated automatically by `run_week.py` at populate time.

### 4c. Tier 2 — talent, experience, and recruiting (`fetch_talent.py`, `fetch_247sports.py`)

- **Returning experience, by position group (OL, DL) — automated, not
  human-typed.** `fetch_puntandrally.py`'s `year=` query param gives real,
  accurate full-season snap counts for prior seasons (confirmed live back
  to at least 2022). `compute_experience_inputs` matches this year's
  starters (from `config/rosters/{team}.yaml`, already staged by
  `run_week.py` from ourlads' depth chart — see Section 4b) against THAT
  SAME TEAM's snap shares last season, via
  `fetch_puntandrally.resolve_any_name_match` when the two sites spell a
  name slightly differently: what
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

### 5b. TrenchEdge OL Rank (absolute, non-matchup)

Everything above is a **matchup differential** — one team's OL vs. one
specific opponent's DL, never an absolute "how good is this team's OL"
number. `src/compute_ol_rank.py` builds that absolute number instead,
using the same four attribute families, but each **percentile-ranked
against the whole league** rather than diffed against one opponent:

| Attribute | Source (already fetched elsewhere in the pipeline) |
|---|---|
| Mass | `fetch_roster.compute_mass_inputs().avg_ol_weight` |
| Experience | `fetch_talent.compute_experience_inputs().returning_ol_snap_pct` |
| Recruiting | `fetch_talent.compute_recruiting_talent_inputs().avg_ol_rating` |
| Performance | `compute_ol_rank.compute_performance_score()` — average of offense-side `stuffRate` (inverted), `lineYards`, `powerSuccess` from `fetch_cfbd.fetch_team_trench_stats()`. Replaces Push's whole-team SP+ gap for this absolute-rank purpose (SP+ is inherently opponent-relative and can't produce a standalone team number); the matchup-level Push component above is unchanged. |

Percentile (`compute_ol_rank.percentile_rank`) was chosen over a z-score:
the output is literally a "rank," a percentile reads directly ("74th
percentile"), and it doesn't assume any of these attributes are normally
distributed (recruiting/performance are skewed; weight roughly is not, but
there's no reason to require that of every input). Ties are handled at
the midpoint (a value tied with N others lands at the center of that tied
group), and a team's own value is always included in the population it's
ranked against.

Reuses `config/weights.yaml`'s existing mass/push/experience/recruiting
weights directly (applying the `push` weight to the Performance
percentile) — no separate weights file. A team missing one or more
attributes gets its composite re-weighted over whatever's available,
matching `compute_composite`'s own posture; a team missing every
attribute (no `config/rosters/{team}.yaml` at all) gets no composite and
sorts last in `rank_league`, not a misleading rank.

**League-wide roster coverage is a prerequisite.** Mass/Experience/
Recruiting all read `config/rosters/{team}.yaml`, and `run_week.py` only
ever populates the teams in that week's matchups — as of this feature's
first build, only ~43 of ~138 FBS teams had a populated roster file.
`scripts/populate_all_fbs_rosters.py` backfills the rest by calling
`run_week.populate_all_rosters()` (unchanged) across every FBS team.

**No individual Lineman Ranking.** An early direction for this feature
was a companion per-lineman score, ranking individual OL players against
each other. Dropped after live-verifying CFBD has no gap-level run data
anywhere (A/B/C gap, off-tackle — that's PFF-style charting, Tier
3/paywalled, not CFBD) and its one directional signal, `rushDirection`
(`GET /rushing/plays` — real, documented, but CFBD parsing raw play text,
not an official stat), is only `left`/`middle`/`right` — too coarse to
attribute to one specific lineman, and inconsistently populated (a real
2025 Miami/Notre Dame game had it `null` on every play). Two data
products instead:

1. **TrenchEdge Offense Ranking** — the team-level percentile composite
   described above.
2. **Lineman player-card export** — plain per-player reference data
   (name, team, jersey, position, weight, class, snaps, recruiting
   rating/stars), no computed score — everything a player-card UI needs,
   nothing ranked.

**Output**: `run_week.py`'s `run_week(..., compute_ol_rank_table=True)`
(opt-in — see its docstring for why: it fetches every FBS team's
attributes, not just this week's ~40-90, so it multiplies the run's
network/browser cost several times over) writes both data products as
CSV **and** JSON, computed once and serialized twice — CSV for
spreadsheet/analysis use, JSON for a front-end page to `fetch()` directly:
`history/{week}/ol_rank_table.csv`/`.json` (one row per team: attributes,
percentiles, composite, rank) and `history/{week}/lineman_stats.csv`/`.json`
(one row per individual OL starter) via stdlib `csv`/`json` — flat,
tabular, league-wide data is a better fit for these formats than this
repo's nested per-game JSON snapshots (`history/{week}/{label}.json`),
which stay as they are. Not currently surfaced in the rendered matchup
widget itself — deliberately left standalone, since the widget's output
format is expected to change once a front end reads these files directly
instead of Jinja-rendered HTML.

**Rushing direction splits — raw data only, not scored.**
`fetch_cfbd.fetch_rushing_direction_splits()` (`GET /rushing/plays`)
computes each team's real run-success-rate by direction (left/middle/
right), live-verified against real 2025 teams (e.g. Miami: left 45.2%,
middle 42.9%, right 49.3%, n=73/191/71). It rides along on
`TeamOLAttributes.direction_splits` and is written as three extra columns
on the Offense Ranking export (`left_success_rate`/`middle_success_rate`/
`right_success_rate`) purely for a future chart — `compute_performance_score`
does not take it as an input and `composite_0_100`/`performance_pctile`
are unaffected. Whether to eventually fold it into the scored Performance
number is an open decision, not made here.

## 6. Output

`render_widget.py` produces a self-contained HTML fragment for one trench
direction: a header (team names, real scheme names, generated timestamp),
a "Formation" chalkboard (below) as the SOLE player-info
display, four diverging bars for the subscores (Mass/Push/Experience/
Recruiting), and a final composite bar with a plain-language verdict band
(negligible / slight-to-moderate / significant / dominant edge, keyed off
`|composite|` thresholds — e.g. 0-2 / 2-5 / 5-8 / 8-10) styled as an
outlined pill in the winning team's color.

**Visual identity (2026-09-16, 2nd/final rewrite):** a light, neutral
"data-journalism meets prediction-market" identity — a bold near-black/
cream "tabloid scoreboard" direction (Big Shoulders Display + Archivo +
DM Mono via Google Fonts) was tried first and explicitly rejected
("doesn't work... think fivethirtyeight meets polymarket," "not dark
mode"). The identity that actually shipped (`templates/widget.html.jinja`):
white/near-white panels (`--te-bg`/`--te-panel`), a **system font stack
only** (`-apple-system, "Segoe UI", system-ui, Helvetica, Arial,
sans-serif` for text; `ui-monospace, "SF Mono", "DM Mono", "Roboto Mono",
monospace` for numbers) — a deliberate choice to drop Google Fonts
entirely so the widget never depends on network access to render
correctly wherever it's embedded. Explicitly light-only, not
dark-mode-adaptive (no `prefers-color-scheme`/`data-theme` handling) — a
single committed look, not a host-page-following theme. Each team's REAL
color (`WidgetContext.team_a_color`/`team_b_color`, from CFBD's `/teams`
`color` field via `fetch_cfbd.team_colors_from_fbs_teams()` — confirmed
live, e.g. Miami `#f47321`, Wake Forest `#ceb888`) is set as CSS custom
properties (`--te-ol-color`/`--te-dl-color`) on the widget root and used
sparingly — thin bar fills, small swatches, colored numerals, formation
card top-borders — never a big color block; a team CFBD has no color for
falls back to the widget's own brand yellow/blue
(`DEFAULT_OL_COLOR`/`DEFAULT_DL_COLOR` in `render_widget.py`), never a
fabricated color. Star ratings (247Sports rating + star count) render as
inline SVG via the `star_rating` Jinja macro — never emoji or dingbat
glyphs.

Every rendered number must be traceable: the widget (or an adjacent data
file) should carry the source and fetch timestamp for each input, not just
the final score. If Section 4b falls back to a cached/estimated roster
weight, that should visibly propagate as a caveat in the rendered output,
not get silently absorbed into a clean-looking number.

**Formation chalkboard is the ONLY player-info display** — the earlier
plain starter tables (with a separate `confirmed`/`estimated` confidence
badge column) were removed entirely per explicit direction ("remove the
confidence... get the player stats into the formation view"), not kept
alongside it. It renders both starting fronts (as many OL/DL boxes as
ourlads' real depth chart actually lists — no hardcoded front size — with
a line-of-scrimmage divider between them), each player card showing a
colored jersey-number badge, position tag, name, class year, weight,
multi-year snaps, and star rating, plus each side's real scheme name
(e.g. "Air Raid" / "4-2-5") under its label. Each side renders
independently — a team with no formation data on one side doesn't hide
the other side's real data. Card layout uses
`grid-template-columns: repeat(auto-fit, minmax(100px, 1fr))` (lets the
row drop to fewer columns at narrow widths rather than forcing an exact
column count too tight for real player names — an earlier fixed-column
attempt broke names mid-word at narrow viewports) with `overflow-wrap:
break-word` as a last-resort safety net. Ordering uses ourlads' own real
left/right slot label (`LT`/`LG`/`C`/`RG`/`RT` for OL, `LDE`/`LDT`/`NT`/
`DT`/`RDT`/`RDE` for DL, staged onto each starter entry as `position_tag`
by `run_week.py`) via `render_widget._ol_formation_order`/
`_dl_formation_order`, sorted by `fetch_ourlads.OL_ROW_ORDER`/
`DL_ROW_ORDER` — genuine positional order, not a guess, since ourlads'
label already says which side each starter plays. A stale/unrecognized
tag (e.g. an entry from before this reverted to ourlads) keeps its
original relative position rather than being dropped or guessed at
(`sorted()`'s stability).

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
- **FCS "buy games" are excluded, not just left blank.** `/games`'s
  `classification=fbs` filters by the QUERIED team's own classification,
  not both teams' — confirmed live: an FBS Top-25 team's game against an
  FCS opponent (2026 week 3: Iowa-Northern Iowa, Oregon-Portland State)
  still comes back from that call, with the FCS opponent's name in
  home/awayTeam. None of this pipeline's sources (CFBD roster/talent,
  puntandrally, 247Sports, the SP+ sheet) cover FCS programs, so every
  score would render unavailable for these anyway — `derive_matchups()`
  drops them at discovery instead (`fbs_teams` from
  `fetch_cfbd.fetch_fbs_teams`, both `run_week.py` and `discover_matchups`
  pass it), so an unscoreable game never reaches `config/matchups/` or
  rendering.
- **Deterministic roster research first, WebSearch only as a bounded
  fallback — not WebSearch-first for every team.** An earlier draft of
  this section had the Routine web-search every team's starters itself
  each week. Superseded, and superseded again:
  - First by `src/fetch_ourlads.py` (plain `requests`, covers 137 of 138
    FBS teams — only Washington State is genuinely absent from its index).
  - Then by `src/fetch_puntandrally.py` — confirmed live to resolve all
    138 FBS teams with zero name aliases, and to carry real per-player
    snap counts ourlads never had — which briefly became the primary
    depth-chart source itself (its top-N-by-snaps ranking standing in
    for "who starts").
  - **Reverted back to ourlads as the depth-chart source (2026-09-16)**:
    puntandrally's usage-derived ranking was confirmed live to
    misrepresent a real starter's actual slot (Section 4b's Cantwell
    example) — it's a proxy for who plays, not an actual depth chart.
    ourlads' real, human-maintained chart (with genuine left/right slots
    and each side's real scheme name) is authoritative for "who starts
    and where" again; puntandrally (jersey/class_year/snaps_multi_year,
    the Experience metric's snap-share lookup) and `fetch_247sports.py`
    (recruiting rating) are now pure by-name enrichment on top of it,
    matched via `fetch_puntandrally.resolve_any_name_match` since the
    sites don't always spell a name identically. puntandrally.com sits
    behind a Cloudflare JS challenge plain `requests` can't solve, so
    that module drives headless Chromium via Playwright instead — a real
    browser process, not just an HTTP call, and confirmed live to need
    no stealth/anti-detection trickery to get through. `src/run_week.py`
    opens ONE shared browser session for the whole run (roster
    enrichment AND rendering's Experience lookups both use it) rather
    than launching Chromium per team.

  Only a team neither ourlads nor its enrichment sources can resolve
  falls back to the Routine doing WebSearch/WebFetch research itself —
  bounded to an explicit short list `run_week.py` reports, not agentic
  judgment across every team every week. `prior_season_starters` /
  `continuity_note` are **not** touched by this automatic path — those
  stay a once-per-season human field, per `_template.yaml`
  (`prior_season_starters` is now an optional fallback only, used if
  puntandrally's own live year-over-year snap match fails for a team —
  see Section 4c); weight/`confirmed` status still only ever comes from
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
  separate setup needed unless a new environment is created later.
  **puntandrally.com and 247sports.com are a different story per
  environment class:** confirmed live that a cloud-sandboxed Routine
  environment's egress proxy hard-denies `puntandrally.com:443` (a policy
  decision, not a code-side problem) — `fetch_puntandrally.py` and
  `fetch_247sports.py` (both Playwright-driven, see above) were built and
  validated from a local machine session instead, which has no such
  restriction and can run the Playwright/Chromium browser these modules
  need anyway (a cloud sandbox may not have a usable display/browser
  runtime for that even if the network egress were allowed). Because
  ourlads is the depth-chart source again (not puntandrally), a
  cloud-Routine run where puntandrally/247Sports are unreachable still
  gets a correct, real starter list and formation — it just degrades to
  missing jersey/class_year/snaps_multi_year/recruit_rating enrichment,
  with each staged-but-unmatched field surfaced as a warning rather than
  failing the run. Until cloud egress is resolved for these two hosts
  specifically, a Routine-fired run should expect that degraded (but
  still correct) mode there — the local/manual `run_week.py` invocation
  this was validated against (2026 Week 3, Miami vs Wake Forest) is the
  one that gets full enrichment end-to-end today. `247sports.com`
  (`fetch_247sports.py`, Recruiting Talent Differential) is very likely
  the same story — it sits behind the same class of bot-detection, solved
  the same way (a plain, non-stealth headless Playwright launch), and
  hasn't been tried from the cloud Routine environment specifically yet;
  assume it needs
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
- **OL Rank vs. offensive-output correlation — built, awaiting full-league
  data.** `scripts/analyze_ol_rank_correlation.py` reports Pearson r
  between the TrenchEdge OL Rank (Section 5b — an absolute, league-wide
  score) and a team's own real offensive output (CFBD's offensive SP+
  rating, `TeamSPPlus.off_sp_plus`, a standalone per-team value, not a
  differential). Same reporting-only posture and `MIN_SAMPLE_FOR_MEANING
  = 30` guard as the ATS correlation script above (factored into a shared
  `scripts/_stats.py` module rather than duplicated a second time). This
  analysis is honestly limited today by the same roster-coverage gap
  Section 5b describes — a real report needs `scripts/
  populate_all_fbs_rosters.py` run first so most of the league has a
  scoreable OL Rank, not just the ~43 teams already populated.
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
