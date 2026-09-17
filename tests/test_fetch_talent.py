import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import fetch_puntandrally
import fetch_roster
import fetch_talent


class _FakeResponse:
    def __init__(self, status_code, json_body):
        self.status_code = status_code
        self._json_body = json_body
        self.text = str(json_body)

    def json(self):
        return self._json_body


class _FakeSession:
    def __init__(self, response):
        self._response = response

    def get(self, url, params, headers, timeout):
        return self._response


TALENT_ROWS = [
    {"year": 2026, "team": "Georgia", "talent": 1003.67},
    {"year": 2026, "team": "Miami", "talent": 885.94},
]


def _write_config(tmp_path, monkeypatch, config: dict):
    monkeypatch.setattr(fetch_roster, "ROSTERS_DIR", tmp_path)
    path = tmp_path / f"{config['team']}.yaml"
    with open(path, "w") as f:
        yaml.safe_dump(config, f)


def _players(*rows):
    """rows: (name, snap_share_pct) tuples -> list[PlayerSnaps] (tag/snaps unused by the match)."""
    return [fetch_puntandrally.PlayerSnaps(name=name, position_tag="T", snaps=None, snap_share_pct=pct) for name, pct in rows]


def test_fetch_team_talent_returns_value_for_present_team(monkeypatch):
    monkeypatch.setenv("CFBD_API_KEY", "k")
    session = _FakeSession(_FakeResponse(200, TALENT_ROWS))
    assert fetch_talent.fetch_team_talent("Miami", 2026, session=session) == 885.94


def test_fetch_team_talent_returns_none_for_absent_team(monkeypatch):
    monkeypatch.setenv("CFBD_API_KEY", "k")
    session = _FakeSession(_FakeResponse(200, TALENT_ROWS))
    assert fetch_talent.fetch_team_talent("Directional State", 2026, session=session) is None


def test_compute_experience_inputs_full_config_uses_live_snap_share(monkeypatch, tmp_path):
    monkeypatch.setenv("CFBD_API_KEY", "k")
    _write_config(tmp_path, monkeypatch, {
        "team": "Miami",
        "updated_by_human_at": "2026-09-14",
        "starters": {
            "OL": [{"name": "Jacob Hawks"}, {"name": "New Guy"}],
            "DL": [{"name": "Damon Wilson"}],
        },
        "continuity_note": {"driver": "scheme", "note": "Beat reporters cite new DC scheme."},
    })
    session = _FakeSession(_FakeResponse(200, TALENT_ROWS))
    monkeypatch.setattr(
        fetch_talent.fetch_puntandrally, "fetch_roster",
        lambda team, year, browser_fetch=None: (
            fetch_puntandrally.RosterSection(players=_players(("Jacob Hawks", 80.0))),  # "New Guy" not on last year's roster
            fetch_puntandrally.RosterSection(players=_players(("Damon Wilson", 60.0))),
        ),
    )

    result = fetch_talent.compute_experience_inputs("Miami", 2026, session=session)

    assert result.talent_composite == 885.94
    assert result.returning_ol_snap_pct == 40.0  # (80 + 0) / 2 -- "New Guy" contributes 0, not excluded
    assert result.returning_dl_snap_pct == 60.0
    assert result.continuity_driver == "scheme"
    assert result.continuity_note == "Beat reporters cite new DC scheme."
    assert result.warnings == []


def test_compute_experience_inputs_resolves_name_variant_against_prior_year_roster(monkeypatch, tmp_path):
    # config's staged starter names now come from ourlads (since the
    # depth-chart revert) and can be spelled slightly differently than
    # puntandrally's own historical roster (dropped suffix here) -- an
    # exact match would otherwise wrongly count a real returning starter
    # as a non-returning 0%. See fetch_puntandrally.resolve_any_name_match.
    monkeypatch.setenv("CFBD_API_KEY", "k")
    _write_config(tmp_path, monkeypatch, {
        "team": "Miami",
        "updated_by_human_at": "2026-09-14",
        "starters": {"OL": [{"name": "Mike Wallace"}], "DL": []},
    })
    session = _FakeSession(_FakeResponse(200, TALENT_ROWS))
    monkeypatch.setattr(
        fetch_talent.fetch_puntandrally, "fetch_roster",
        lambda team, year, browser_fetch=None: (
            fetch_puntandrally.RosterSection(players=_players(("Mike Wallace Jr.", 75.0))),
            fetch_puntandrally.RosterSection(players=[]),
        ),
    )

    result = fetch_talent.compute_experience_inputs("Miami", 2026, session=session)

    assert result.returning_ol_snap_pct == 75.0


def test_compute_experience_inputs_never_counts_a_transfers_snaps_at_their_old_team(monkeypatch, tmp_path):
    # fetch_puntandrally.fetch_roster(team, year, ...) only ever returns
    # THIS team's own page -- a transfer's real snaps happened on a
    # different team's page, which this function is never given to search.
    monkeypatch.setenv("CFBD_API_KEY", "k")
    _write_config(tmp_path, monkeypatch, {
        "team": "Miami",
        "updated_by_human_at": "2026-09-14",
        "starters": {"OL": [{"name": "Transfer Guy"}], "DL": []},
    })
    session = _FakeSession(_FakeResponse(200, TALENT_ROWS))
    # Miami's own 2025 roster never had "Transfer Guy" (he played elsewhere).
    monkeypatch.setattr(
        fetch_talent.fetch_puntandrally, "fetch_roster",
        lambda team, year, browser_fetch=None: (
            fetch_puntandrally.RosterSection(players=_players(("Someone Else", 90.0))),
            fetch_puntandrally.RosterSection(players=[]),
        ),
    )

    result = fetch_talent.compute_experience_inputs("Miami", 2026, session=session)

    assert result.returning_ol_snap_pct == 0.0  # not 90.0 -- his old team's snaps don't count here


def test_compute_experience_inputs_missing_config_flags_and_continues(monkeypatch, tmp_path):
    monkeypatch.setenv("CFBD_API_KEY", "k")
    monkeypatch.setattr(fetch_roster, "ROSTERS_DIR", tmp_path)
    session = _FakeSession(_FakeResponse(200, TALENT_ROWS))

    result = fetch_talent.compute_experience_inputs("Miami", 2026, session=session)

    assert result.talent_composite == 885.94
    assert result.returning_ol_snap_pct is None
    assert any("does not exist" in w for w in result.warnings)


def test_compute_experience_inputs_no_starters_yet_skips_live_fetch(monkeypatch, tmp_path):
    monkeypatch.setenv("CFBD_API_KEY", "k")
    _write_config(tmp_path, monkeypatch, {
        "team": "Miami",
        "updated_by_human_at": "2026-09-14",
        "starters": {"OL": [], "DL": []},
    })
    session = _FakeSession(_FakeResponse(200, TALENT_ROWS))

    class _ExplodingFetch:
        def __call__(self, *a, **k):
            raise AssertionError("should not fetch a prior-year roster when there are no current starters")

    monkeypatch.setattr(fetch_talent.fetch_puntandrally, "fetch_roster", _ExplodingFetch())

    result = fetch_talent.compute_experience_inputs("Miami", 2026, session=session)

    assert result.returning_ol_snap_pct is None
    assert any("no starters yet" in w for w in result.warnings)


def test_compute_experience_inputs_falls_back_to_config_when_live_fetch_fails(monkeypatch, tmp_path):
    monkeypatch.setenv("CFBD_API_KEY", "k")
    _write_config(tmp_path, monkeypatch, {
        "team": "Miami",
        "updated_by_human_at": "2026-09-14",
        "starters": {
            "OL": [{"name": "Jacob Hawks"}, {"name": "New Guy"}],
            "DL": [{"name": "Damon Wilson"}],
        },
        "prior_season_starters": {
            "OL": [{"name": "Jacob Hawks"}, {"name": "Departed Guy"}],
            "DL": [{"name": "Damon Wilson"}],
        },
    })
    session = _FakeSession(_FakeResponse(200, TALENT_ROWS))

    def _raise(team, year, browser_fetch=None):
        raise fetch_puntandrally.PuntAndRallyFetchError("mock puntandrally outage")

    monkeypatch.setattr(fetch_talent.fetch_puntandrally, "fetch_roster", _raise)

    result = fetch_talent.compute_experience_inputs("Miami", 2026, session=session)

    assert result.returning_ol_snap_pct == 50.0  # 1 of 2 current OL names overlaps prior_season_starters
    assert result.returning_dl_snap_pct == 100.0
    assert any("puntandrally failed" in w for w in result.warnings)


def test_compute_experience_inputs_missing_prior_season_block_and_live_fetch_fails(monkeypatch, tmp_path):
    monkeypatch.setenv("CFBD_API_KEY", "k")
    _write_config(tmp_path, monkeypatch, {
        "team": "Miami",
        "updated_by_human_at": "2026-09-14",
        "starters": {"OL": [{"name": "Jacob Hawks"}], "DL": []},
    })
    session = _FakeSession(_FakeResponse(200, TALENT_ROWS))

    def _raise(team, year, browser_fetch=None):
        raise fetch_puntandrally.PuntAndRallyFetchError("mock puntandrally outage")

    monkeypatch.setattr(fetch_talent.fetch_puntandrally, "fetch_roster", _raise)

    result = fetch_talent.compute_experience_inputs("Miami", 2026, session=session)

    assert result.returning_ol_snap_pct is None
    assert any("prior_season_starters fallback either" in w for w in result.warnings)


def test_fetch_talent_table_returns_all_teams_in_one_call(monkeypatch):
    monkeypatch.setenv("CFBD_API_KEY", "k")
    session = _FakeSession(_FakeResponse(200, TALENT_ROWS))
    table = fetch_talent.fetch_talent_table(2026, session=session)
    assert table == {"Georgia": 1003.67, "Miami": 885.94}


def test_fetch_team_talent_reuses_prefetched_table_without_refetching(monkeypatch):
    monkeypatch.setenv("CFBD_API_KEY", "k")
    table = {"Miami": 885.94}

    class _ExplodingSession:
        def get(self, *a, **k):
            raise AssertionError("should not fetch again -- a table was already provided")

    assert fetch_talent.fetch_team_talent("Miami", 2026, table=table, session=_ExplodingSession()) == 885.94


def test_compute_experience_inputs_reuses_prefetched_talent_table(monkeypatch, tmp_path):
    monkeypatch.setenv("CFBD_API_KEY", "k")
    _write_config(tmp_path, monkeypatch, {
        "team": "Miami",
        "updated_by_human_at": "2026-09-14",
        "starters": {"OL": [], "DL": []},
    })

    class _ExplodingSession:
        def get(self, *a, **k):
            raise AssertionError("should not fetch again -- a table was already provided")

    result = fetch_talent.compute_experience_inputs(
        "Miami", 2026, talent_table={"Miami": 885.94}, session=_ExplodingSession()
    )
    assert result.talent_composite == 885.94


def test_compute_recruiting_talent_inputs_averages_staged_ratings(tmp_path, monkeypatch):
    _write_config(tmp_path, monkeypatch, {
        "team": "Miami",
        "updated_by_human_at": "2026-09-14",
        "starters": {
            "OL": [
                {"name": "Matthew McCoy", "recruit_rating": 86, "recruit_stars": 3},
                {"name": "Samson Okunlola", "recruit_rating": 98, "recruit_stars": 5},
            ],
            "DL": [
                {"name": "Marquise Lightfoot", "recruit_rating": 97, "recruit_stars": 4},
                {"name": "Ahmad Moten Sr.", "recruit_rating": 85, "recruit_stars": 3},
            ],
        },
    })

    result = fetch_talent.compute_recruiting_talent_inputs("Miami")

    assert result.avg_ol_rating == 92.0  # (86 + 98) / 2
    assert result.avg_dl_rating == 91.0  # (97 + 85) / 2
    assert result.warnings == []


def test_compute_recruiting_talent_inputs_excludes_unrated_starters_not_zero(tmp_path, monkeypatch):
    _write_config(tmp_path, monkeypatch, {
        "team": "Miami",
        "updated_by_human_at": "2026-09-14",
        "starters": {
            "OL": [
                {"name": "Matthew McCoy", "recruit_rating": 86},
                {"name": "Walk On Guy"},  # no recruit_rating staged (unrated, or unmatched against 247Sports)
            ],
            "DL": [],
        },
    })

    result = fetch_talent.compute_recruiting_talent_inputs("Miami")

    assert result.avg_ol_rating == 86.0  # only the rated starter counts -- unrated is excluded, not averaged in as 0
    assert result.avg_dl_rating is None


def test_compute_recruiting_talent_inputs_warns_when_no_starter_has_a_rating(tmp_path, monkeypatch):
    # DL has real starter entries, but none carries a recruit_rating --
    # this is the "247Sports fetch never matched anyone" case, distinct
    # from an empty DL list (which is silently skipped, not warned about).
    _write_config(tmp_path, monkeypatch, {
        "team": "Miami",
        "updated_by_human_at": "2026-09-14",
        "starters": {
            "OL": [],
            "DL": [{"name": "Marquise Lightfoot"}, {"name": "Ahmad Moten Sr."}],
        },
    })

    result = fetch_talent.compute_recruiting_talent_inputs("Miami")

    assert result.avg_dl_rating is None
    assert any("no DL starter" in w and "recruit_rating" in w for w in result.warnings)


def test_compute_recruiting_talent_inputs_missing_config_flags_and_continues(tmp_path, monkeypatch):
    monkeypatch.setattr(fetch_roster, "ROSTERS_DIR", tmp_path)
    result = fetch_talent.compute_recruiting_talent_inputs("Nonexistent Team")
    assert result.avg_ol_rating is None
    assert result.avg_dl_rating is None
    assert any("does not exist" in w for w in result.warnings)
