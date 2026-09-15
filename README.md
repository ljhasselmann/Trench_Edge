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

**Built and tested:**

- `src/compute_composite.py` — the Section 5 scoring model (Mass/Push/
  Continuity normalization, weighting, verdict bands). Pure computation,
  no network dependency. 7 offline unit tests, all passing
  (`tests/test_compute_composite.py`).
- `src/fetch_cfbd.py` — Tier 1 CFBD advanced-stats fetch (Section 4a).
  Reads `CFBD_API_KEY` from the environment, sends
  `Authorization: Bearer ...`, parses `stuffRate` / `lineYards` /
  `havoc.frontSeven` defensively (missing fields surface as a warning, not
  a crash). 6 unit tests against a mocked HTTP layer, all passing
  (`tests/test_fetch_cfbd.py`) — including one that caught and fixed a real
  bug (an empty-but-present side payload was being treated as "missing
  entirely" instead of generating per-field warnings).
- `config/weights.yaml`, `config/teams.yaml` — scaffolded per Section 3/5.

**Confirmed against the live API key, this session:**

- `CFBD_API_KEY` is present in the environment. ✅
- A live call to `api.collegefootballdata.com` fails with a **403 from this
  session's network egress proxy** ("Tunnel connection failed: 403
  Forbidden"), not a CFBD-side auth or rate-limit error. This is the
  network-policy gap Section 7 of the design doc explicitly calls out as
  "the one non-obvious setup step" — confirmed accurate. The key itself has
  not been validated against CFBD because the host isn't reachable from
  here yet.
  - **To unblock:** add `api.collegefootballdata.com` to this environment's
    network allowlist (environment settings, not something this session can
    change itself), then re-run:
    `python3 src/fetch_cfbd.py Miami --year 2025`
  - Until that's done, `fetch_cfbd.py`'s live parsing logic (field names
    like `stuffRate`, `havoc.frontSeven`) is verified against CFBD's
    *documented* schema only, not a real response. Treat field names as
    provisional until the first live call succeeds.

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
python3 src/fetch_cfbd.py Miami --year 2025
```
