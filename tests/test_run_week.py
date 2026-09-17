import json
import sys
from contextlib import contextmanager
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import fetch_247sports
import fetch_ourlads
import fetch_puntandrally
import render_widget
import run_week


SAMPLE_GAMES = [
    {"id": 1, "homeTeam": "Wake Forest", "awayTeam": "Miami"},
    {"id": 2, "homeTeam": "Ohio State", "awayTeam": "Texas"},
    {"id": 3, "homeTeam": "BadTeamB", "awayTeam": "BadTeamA"},
]

SAMPLE_TOP25 = {"Miami", "Texas", "Ohio State", "BadTeamA"}


def _p(name, tag, snaps):
    return fetch_puntandrally.PlayerSnaps(name=name, position_tag=tag, snaps=snaps, snap_share_pct=None)


def _fake_ourlads_chart(team: str) -> fetch_ourlads.TeamDepthChart:
    """ourlads is now authoritative for who starts + real slot -- names
    kept in the same `{team} {label}N` shape the old puntandrally
    fixtures used, so unrelated test assertions (roster-diff, etc.)
    don't need to change, just the position_tag values."""
    return fetch_ourlads.TeamDepthChart(
        rows={
            "LT": [f"{team} T1"], "LG": [f"{team} G1"], "C": [f"{team} C1"],
            "RG": [f"{team} G2"], "RT": [f"{team} T2"],
            "LDE": [f"{team} DE1"], "LDT": [f"{team} DT1"],
            "RDT": [f"{team} DT2"], "RDE": [f"{team} DE2"],
        },
        offense_scheme="Pro Style", defense_scheme="4-3",
    )


def _fake_fetch_depth_chart(team, session=None, index=None):
    if team == "Ohio State":
        raise fetch_ourlads.OurladsFetchError(f"mock failure for {team}")
    return _fake_ourlads_chart(team)


def _patch_ourlads(monkeypatch, fetch_depth_chart=_fake_fetch_depth_chart):
    monkeypatch.setattr(run_week.fetch_ourlads, "fetch_team_index", lambda **kw: {})
    monkeypatch.setattr(run_week.fetch_ourlads, "fetch_depth_chart", fetch_depth_chart)


def _fake_roster_sections(team: str):
    """puntandrally is now pure by-name ENRICHMENT (jersey/class/snaps),
    matched against ourlads's starter names -- same names as
    _fake_ourlads_chart so the common-case tests exact-match without
    needing the name-variant resolver."""
    ol_section = fetch_puntandrally.RosterSection(players=[
        _p(f"{team} T1", "T", 90), _p(f"{team} T2", "T", 80),
        _p(f"{team} G1", "G", 70), _p(f"{team} G2", "G", 60),
        _p(f"{team} C1", "C", 90),
    ])
    dl_section = fetch_puntandrally.RosterSection(players=[
        _p(f"{team} DT1", "DT", 90), _p(f"{team} DT2", "DT", 80),
        _p(f"{team} DE1", "DE", 70), _p(f"{team} DE2", "DE", 60),
    ])
    return ol_section, dl_section


def _fake_fetch_roster(team, year, browser_fetch=None):
    return _fake_roster_sections(team)


@contextmanager
def _fake_browser_session():
    yield None  # _fake_fetch_roster ignores browser_fetch entirely


def _fake_247sports_fetch_roster(team, year, browser_fetch=None):
    return {}  # no ratings staged in these tests -- 247Sports enrichment isn't under test here


def _fake_ctx(label: str, side: str) -> render_widget.WidgetContext:
    return render_widget.WidgetContext(
        matchup_label=label,
        team_a="TeamA",
        team_b="TeamB",
        side=side,
        year=2026,
        generated_at="2026-01-01 00:00 UTC",
        team_a_color="#ffd400",
        team_b_color="#1f6feb",
        mass={
            "team_a_avg_weight": 300, "team_b_avg_weight": 280, "weight_diff_lbs": 20,
            "score": 1.0, "team_a_starters": [], "team_b_starters": [],
        },
        push={"available": True, "score": 1.0, "sp_plus_gap": 5.0, "raw": {}},
        experience={
            "team_a_returning_pct": 70.0, "team_b_returning_pct": 60.0, "experience_diff_pct": 10.0, "score": 1.0,
            "team_a_driver": None, "team_a_note": None, "team_b_driver": None, "team_b_note": None,
        },
        recruiting={"team_a_rating": 90.0, "team_b_rating": 80.0, "rating_diff": 10.0, "score": 2.0},
        composite={"value": 1.5, "verdict": "test verdict"},
        warnings=[],
    )


def _fake_build_both_directions(matchup, year, sp_plus_table=None, talent_table=None, browser_fetch=None, team_colors=None):
    if "badteama" in matchup["label"]:
        raise RuntimeError(f"mock render failure for {matchup['label']}")
    label = matchup["label"]
    return _fake_ctx(f"{label}-a", "team_a_ol_vs_team_b_dl"), _fake_ctx(f"{label}-b", "team_b_ol_vs_team_a_dl")


def _patch_dirs(monkeypatch, tmp_path):
    monkeypatch.setattr(run_week, "MATCHUPS_DIR", tmp_path / "config" / "matchups")
    monkeypatch.setattr(run_week, "ROSTERS_DIR", tmp_path / "config" / "rosters")
    monkeypatch.setattr(run_week, "TEAMS_FILE", tmp_path / "config" / "teams.yaml")
    monkeypatch.setattr(run_week, "OUTPUT_DIR", tmp_path / "output")
    monkeypatch.setattr(run_week, "HISTORY_DIR", tmp_path / "history")


def _patch_network(monkeypatch):
    monkeypatch.setattr(run_week.fetch_matchups, "fetch_fbs_schedule", lambda year, week, **kw: SAMPLE_GAMES)
    monkeypatch.setattr(run_week.fetch_matchups, "fetch_ap_top25", lambda year, week, **kw: SAMPLE_TOP25)
    monkeypatch.setattr(
        run_week.fetch_cfbd, "fetch_fbs_teams",
        lambda year, **kw: [{"school": s} for s in ("Miami", "Wake Forest", "Ohio State", "Texas", "BadTeamA", "BadTeamB")],
    )
    _patch_ourlads(monkeypatch)
    monkeypatch.setattr(run_week.fetch_puntandrally, "browser_session", _fake_browser_session)
    monkeypatch.setattr(run_week.fetch_puntandrally, "fetch_roster", _fake_fetch_roster)
    monkeypatch.setattr(run_week.fetch_247sports, "fetch_roster", _fake_247sports_fetch_roster)
    monkeypatch.setattr(run_week.fetch_sp_plus, "fetch_fbs_week_table", lambda week, **kw: {})
    monkeypatch.setattr(run_week.fetch_talent, "fetch_talent_table", lambda year, **kw: {})
    monkeypatch.setattr(run_week.render_widget, "build_both_directions", _fake_build_both_directions)


def test_run_week_discovers_and_writes_matchups_config(monkeypatch, tmp_path):
    _patch_dirs(monkeypatch, tmp_path)
    _patch_network(monkeypatch)

    result = run_week.run_week(2026, 1, today="2026-09-16")

    assert result["matchup_count"] == 3
    matchups_path = tmp_path / "config" / "matchups" / "2026-wk01.yaml"
    assert matchups_path.exists()
    import yaml
    written = yaml.safe_load(matchups_path.read_text())
    labels = {m["label"] for m in written["matchups"]}
    assert labels == {
        "2026-wk01-miami-wake-forest",
        "2026-wk01-texas-ohio-state",
        "2026-wk01-badteama-badteamb",
    }


def test_run_week_with_ol_rank_writes_csv_and_json_data_products(monkeypatch, tmp_path):
    # The OL Rank / lineman player-card export is a standalone data
    # product (CSV + JSON) -- deliberately NOT threaded into matchup
    # rendering (render_widget.build_both_directions is untouched here,
    # unlike an earlier version of this wiring), since the widget's
    # output format is expected to change once a front end reads these
    # files directly.
    _patch_dirs(monkeypatch, tmp_path)
    _patch_network(monkeypatch)

    fake_attrs = {
        "Miami": run_week.compute_ol_rank.TeamOLAttributes(team="Miami", avg_ol_weight=320, returning_ol_snap_pct=70, avg_ol_rating=90, performance_score=3.0),
        "Wake Forest": run_week.compute_ol_rank.TeamOLAttributes(team="Wake Forest", avg_ol_weight=290, returning_ol_snap_pct=50, avg_ol_rating=70, performance_score=2.0),
    }
    monkeypatch.setattr(run_week.compute_ol_rank, "fetch_league_ol_attributes", lambda year, **kw: fake_attrs)

    result = run_week.run_week(2026, 1, today="2026-09-16", compute_ol_rank_table=True)

    assert result["rendered_count"] == 2  # badteama fails to render, same as the existing full-run test

    hist_dir = tmp_path / "history" / "2026-wk01"
    assert (hist_dir / "ol_rank_table.csv").exists()
    assert (hist_dir / "ol_rank_table.json").exists()
    assert (hist_dir / "lineman_stats.csv").exists()
    assert (hist_dir / "lineman_stats.json").exists()

    ol_rank_rows = json.loads((hist_dir / "ol_rank_table.json").read_text())
    assert any(row["team"] == "Miami" and row["rank"] == 1 for row in ol_rank_rows)

    import json as _json
    ol_rank_rows = _json.loads((hist_dir / "ol_rank_table.json").read_text())
    assert any(row["team"] == "Miami" for row in ol_rank_rows)


def test_run_week_excludes_a_game_against_an_fcs_opponent(monkeypatch, tmp_path):
    # /games' classification=fbs filters by the QUERIED team's own
    # classification, not both teams' -- an FBS team's "buy game" against
    # an FCS opponent still comes back. None of this pipeline's sources
    # cover FCS programs, so it should never reach config/matchups.
    _patch_dirs(monkeypatch, tmp_path)
    _patch_network(monkeypatch)
    games_with_fcs_buy_game = SAMPLE_GAMES + [{"id": 4, "homeTeam": "Miami", "awayTeam": "Northern Iowa"}]
    monkeypatch.setattr(run_week.fetch_matchups, "fetch_fbs_schedule", lambda year, week, **kw: games_with_fcs_buy_game)
    # fetch_fbs_teams (patched in _patch_network) deliberately omits
    # "Northern Iowa" -- it's FCS.

    result = run_week.run_week(2026, 1, today="2026-09-16")

    assert result["matchup_count"] == 3  # unchanged -- the buy game never counted
    matchups_path = tmp_path / "config" / "matchups" / "2026-wk01.yaml"
    written = yaml.safe_load(matchups_path.read_text())
    labels = {m["label"] for m in written["matchups"]}
    assert not any("northern-iowa" in l for l in labels)


def test_run_week_one_bad_matchup_does_not_abort_the_rest(monkeypatch, tmp_path):
    _patch_dirs(monkeypatch, tmp_path)
    _patch_network(monkeypatch)

    result = run_week.run_week(2026, 1, today="2026-09-16")

    assert result["rendered_count"] == 2
    assert result["failed_count"] == 1
    assert "badteama" in result["failures"][0]["label"]


def test_run_week_output_and_history_land_in_week_namespaced_paths(monkeypatch, tmp_path):
    _patch_dirs(monkeypatch, tmp_path)
    _patch_network(monkeypatch)

    run_week.run_week(2026, 1, today="2026-09-16")

    out_dir = tmp_path / "output" / "2026-wk01"
    hist_dir = tmp_path / "history" / "2026-wk01"
    assert (out_dir / "2026-wk01-miami-wake-forest.html").exists()
    assert (out_dir / "2026-wk01-texas-ohio-state.html").exists()
    assert not (out_dir / "2026-wk01-badteama-badteamb.html").exists()  # failed, nothing written
    assert (hist_dir / "2026-wk01-miami-wake-forest.json").exists()
    assert (out_dir / "index.html").exists()

    index_html = (out_dir / "index.html").read_text()
    assert "2026-wk01-miami-wake-forest.html" in index_html
    assert "2026-wk01-texas-ohio-state.html" in index_html


def test_run_week_reports_ourlads_failures_without_aborting(monkeypatch, tmp_path):
    _patch_dirs(monkeypatch, tmp_path)
    _patch_network(monkeypatch)

    result = run_week.run_week(2026, 1, today="2026-09-16")

    failed_teams = {r["team"] for r in result["roster_failures"]}
    assert failed_teams == {"Ohio State"}
    # Rendering still succeeded for the Texas/Ohio State game despite the
    # roster fetch failure -- roster population and rendering are decoupled.
    assert result["rendered_count"] == 2


def test_populate_roster_puntandrally_enrichment_failure_does_not_fail_roster_population(monkeypatch, tmp_path):
    # puntandrally is now pure by-name enrichment -- its own failure must
    # degrade to a warning, not the "failed" status ourlads itself
    # failing produces (see test_run_week_reports_ourlads_failures_without_aborting).
    monkeypatch.setattr(run_week, "ROSTERS_DIR", tmp_path)
    _patch_ourlads(monkeypatch)
    monkeypatch.setattr(run_week.fetch_247sports, "fetch_roster", _fake_247sports_fetch_roster)

    def _raise(team, year, browser_fetch=None):
        raise fetch_puntandrally.PuntAndRallyFetchError(f"mock puntandrally outage for {team}")
    monkeypatch.setattr(run_week.fetch_puntandrally, "fetch_roster", _raise)

    result = run_week.populate_roster("Miami", 2026, "2026-09-16")

    assert result["status"] == "ok"
    written = yaml.safe_load((tmp_path / "Miami.yaml").read_text())
    assert written["starters"]["OL"][0]["name"] == "Miami T1"  # ourlads' starter list is unaffected
    assert "jersey" not in written["starters"]["OL"][0]  # no enrichment data available
    assert any("puntandrally enrichment fetch failed" in w for w in result["warnings"])


def test_run_week_roster_diff_compares_against_teams_own_prior_config(monkeypatch, tmp_path):
    _patch_dirs(monkeypatch, tmp_path)
    _patch_network(monkeypatch)

    rosters_dir = tmp_path / "config" / "rosters"
    rosters_dir.mkdir(parents=True)
    (rosters_dir / "Miami.yaml").write_text(
        "team: Miami\nupdated_by_human_at: '2026-09-01'\n"
        "starters:\n  OL:\n    - name: 'Old Starter'\n  DL: []\n"
    )

    result = run_week.run_week(2026, 1, today="2026-09-16")

    miami_change = next(r for r in result["roster_changes"] if r["team"] == "Miami")
    assert any("Old Starter" in c and "Miami T1" in c for c in miami_change["changes"])

    written = (rosters_dir / "Miami.yaml").read_text()
    assert "Miami T1" in written
    assert "2026-09-16" in written


def test_run_week_stages_position_tag_for_chalkboard_formation(monkeypatch, tmp_path):
    _patch_dirs(monkeypatch, tmp_path)
    _patch_network(monkeypatch)

    run_week.run_week(2026, 1, today="2026-09-16")

    written = yaml.safe_load((tmp_path / "config" / "rosters" / "Miami.yaml").read_text())
    ol_tags = {s["name"]: s["position_tag"] for s in written["starters"]["OL"]}
    assert ol_tags == {"Miami T1": "LT", "Miami T2": "RT", "Miami G1": "LG", "Miami G2": "RG", "Miami C1": "C"}
    dl_tags = {s["name"]: s["position_tag"] for s in written["starters"]["DL"]}
    assert dl_tags == {"Miami DT1": "LDT", "Miami DT2": "RDT", "Miami DE1": "LDE", "Miami DE2": "RDE"}
    assert written["offense_scheme"] == "Pro Style"
    assert written["defense_scheme"] == "4-3"


def test_populate_roster_stages_247sports_rating_when_matched(monkeypatch, tmp_path):
    monkeypatch.setattr(run_week, "ROSTERS_DIR", tmp_path)
    _patch_ourlads(monkeypatch)
    monkeypatch.setattr(run_week.fetch_puntandrally, "fetch_roster", _fake_fetch_roster)
    monkeypatch.setattr(
        run_week.fetch_247sports, "fetch_roster",
        lambda team, year, browser_fetch=None: {
            f"{team.lower()} t1": fetch_247sports.RecruitRating(
                name=f"{team} T1", position="OL", class_year="SR", high_school="Test HS", rating=93, stars=4,
            ),
        },
    )

    result = run_week.populate_roster("Miami", 2026, "2026-09-16")

    assert result["status"] == "ok"
    written = yaml.safe_load((tmp_path / "Miami.yaml").read_text())
    t1_entry = next(e for e in written["starters"]["OL"] if e["name"] == "Miami T1")
    assert t1_entry["recruit_rating"] == 93
    assert t1_entry["recruit_stars"] == 4
    # T2 has no match in the fake 247Sports response -- staged without a rating, and flagged.
    t2_entry = next(e for e in written["starters"]["OL"] if e["name"] == "Miami T2")
    assert "recruit_rating" not in t2_entry
    assert any("Miami T2" in w and "not found on 247Sports" in w for w in result["warnings"])


def test_populate_roster_resolves_ourlads_name_variant_against_247sports(monkeypatch, tmp_path):
    # A starter's name can be spelled slightly differently across sites
    # (truncated initial, dropped suffix, stripped accent) -- an exact
    # match against 247Sports's own full name would otherwise silently
    # fail and lose the rating. See fetch_puntandrally.resolve_any_name_match.
    def _chart_with_truncated_name(team, session=None, index=None):
        return fetch_ourlads.TeamDepthChart(rows={"LT": ["M. Alcorn-Crowder"]}, offense_scheme=None, defense_scheme=None)

    monkeypatch.setattr(run_week, "ROSTERS_DIR", tmp_path)
    _patch_ourlads(monkeypatch, fetch_depth_chart=_chart_with_truncated_name)
    monkeypatch.setattr(run_week.fetch_puntandrally, "fetch_roster", lambda team, year, browser_fetch=None: (
        fetch_puntandrally.RosterSection(players=[]), fetch_puntandrally.RosterSection(players=[]),
    ))
    monkeypatch.setattr(
        run_week.fetch_247sports, "fetch_roster",
        lambda team, year, browser_fetch=None: {
            "malcolm alcorn-crowder": fetch_247sports.RecruitRating(
                name="Malcolm Alcorn-Crowder", position="OL", class_year="SR", high_school="Test HS", rating=78, stars=2,
            ),
        },
    )

    result = run_week.populate_roster("Miami", 2026, "2026-09-16")

    written = yaml.safe_load((tmp_path / "Miami.yaml").read_text())
    entry = written["starters"]["OL"][0]
    assert entry["recruit_rating"] == 78
    assert entry["recruit_stars"] == 2
    assert not any("not found on 247Sports" in w for w in result["warnings"])


def test_populate_roster_continues_without_ratings_when_247sports_fetch_fails(monkeypatch, tmp_path):
    monkeypatch.setattr(run_week, "ROSTERS_DIR", tmp_path)
    _patch_ourlads(monkeypatch)
    monkeypatch.setattr(run_week.fetch_puntandrally, "fetch_roster", _fake_fetch_roster)

    def _raise(team, year, browser_fetch=None):
        raise fetch_247sports.TwoFortySevenFetchError("mock 247Sports outage")
    monkeypatch.setattr(run_week.fetch_247sports, "fetch_roster", _raise)

    result = run_week.populate_roster("Miami", 2026, "2026-09-16")

    assert result["status"] == "ok"  # 247Sports failing never fails roster population
    written = yaml.safe_load((tmp_path / "Miami.yaml").read_text())
    assert "recruit_rating" not in written["starters"]["OL"][0]
    assert any("247Sports recruiting-rating fetch failed" in w for w in result["warnings"])


def test_populate_roster_preserves_prior_rating_when_247sports_fetch_fails(monkeypatch, tmp_path):
    # A transient 247Sports outage (site down, timeout) shouldn't erase a
    # rating this file already had from an earlier, successful run --
    # confirmed real regression: a live full-week run lost Miami's ratings
    # entirely this way. Only a genuine per-player "not found" miss (site
    # fetch itself succeeded) should ever clear a previously-staged rating.
    monkeypatch.setattr(run_week, "ROSTERS_DIR", tmp_path)
    (tmp_path / "Miami.yaml").write_text(yaml.safe_dump({
        "team": "Miami",
        "updated_by_human_at": "2026-09-01",
        "starters": {"OL": [{"name": "Miami T1", "recruit_rating": 86, "recruit_stars": 3}], "DL": []},
    }))
    _patch_ourlads(monkeypatch)
    monkeypatch.setattr(run_week.fetch_puntandrally, "fetch_roster", _fake_fetch_roster)

    def _raise(team, year, browser_fetch=None):
        raise fetch_247sports.TwoFortySevenFetchError("mock 247Sports outage")
    monkeypatch.setattr(run_week.fetch_247sports, "fetch_roster", _raise)

    result = run_week.populate_roster("Miami", 2026, "2026-09-16")

    written = yaml.safe_load((tmp_path / "Miami.yaml").read_text())
    t1_entry = next(e for e in written["starters"]["OL"] if e["name"] == "Miami T1")
    assert t1_entry["recruit_rating"] == 86
    assert t1_entry["recruit_stars"] == 3
    assert any("kept its previously-staged recruit_rating" in w for w in result["warnings"])


def test_run_week_skip_roster_leaves_existing_files_untouched(monkeypatch, tmp_path):
    _patch_dirs(monkeypatch, tmp_path)
    _patch_network(monkeypatch)

    rosters_dir = tmp_path / "config" / "rosters"
    rosters_dir.mkdir(parents=True)
    existing_text = "team: Miami\nupdated_by_human_at: '2026-09-01'\nstarters:\n  OL:\n    - name: 'Kept Starter'\n  DL: []\n"
    (rosters_dir / "Miami.yaml").write_text(existing_text)

    result = run_week.run_week(2026, 1, today="2026-09-16", skip_roster=True)

    assert result["roster_results"] == []
    assert (rosters_dir / "Miami.yaml").read_text() == existing_text


def test_active_labels_none_when_no_filter_given():
    matchups = [{"label": "a", "team_a": "X", "team_b": "Y"}]
    assert run_week._active_labels(matchups, None, None) is None
    assert run_week._active_labels(matchups, [], []) is None


def test_active_labels_matches_by_team_or_label():
    matchups = [
        {"label": "2026-wk01-miami-wake-forest", "team_a": "Miami", "team_b": "Wake Forest"},
        {"label": "2026-wk01-texas-ohio-state", "team_a": "Texas", "team_b": "Ohio State"},
        {"label": "2026-wk01-badteama-badteamb", "team_a": "BadTeamA", "team_b": "BadTeamB"},
    ]
    by_team = run_week._active_labels(matchups, ["Ohio State"], None)
    assert by_team == {"2026-wk01-texas-ohio-state"}

    by_label = run_week._active_labels(matchups, None, ["2026-wk01-badteama-badteamb"])
    assert by_label == {"2026-wk01-badteama-badteamb"}


def test_run_week_teams_filter_only_renders_filtered_matchups_on_second_pass(monkeypatch, tmp_path):
    _patch_dirs(monkeypatch, tmp_path)
    _patch_network(monkeypatch)

    call_labels = []

    def _counting_build_both_directions(matchup, year, sp_plus_table=None, talent_table=None, browser_fetch=None, team_colors=None):
        call_labels.append(matchup["label"])
        return _fake_build_both_directions(matchup, year, sp_plus_table=sp_plus_table, talent_table=talent_table)

    monkeypatch.setattr(run_week.render_widget, "build_both_directions", _counting_build_both_directions)

    # First pass: full run, no filter -- seeds history/*.json for every matchup.
    first = run_week.run_week(2026, 1, today="2026-09-16")
    assert first["rendered_count"] == 2  # badteama fails to render, same as the existing full-run test
    assert sorted(call_labels) == [
        "2026-wk01-badteama-badteamb",  # attempted, then raises inside -- caught as a failure
        "2026-wk01-miami-wake-forest",
        "2026-wk01-texas-ohio-state",
    ]

    # Second pass: filtered to just Miami's game -- Texas/Ohio State must NOT
    # be re-rendered (reused from the history snapshot the first pass wrote),
    # but must still appear in the index. badteama has no history to reuse
    # (it always fails), so it's always re-attempted regardless of filter --
    # that's the intended fallback, not a filter leak.
    call_labels.clear()
    second = run_week.run_week(2026, 1, today="2026-09-17", only_teams=["Miami"])

    assert sorted(call_labels) == ["2026-wk01-badteama-badteamb", "2026-wk01-miami-wake-forest"]
    assert "2026-wk01-texas-ohio-state" not in call_labels  # reused, not re-rendered
    assert second["rendered_count"] == 2  # index still has both surviving games (one fresh, one reused)


def test_run_week_teams_filter_only_populates_rosters_for_filtered_teams(monkeypatch, tmp_path):
    _patch_dirs(monkeypatch, tmp_path)
    _patch_network(monkeypatch)

    # Seed history so the second pass has something to reuse.
    run_week.run_week(2026, 1, today="2026-09-16")

    result = run_week.run_week(2026, 1, today="2026-09-17", only_teams=["Miami"])

    roster_teams = {r["team"] for r in result["roster_results"]}
    assert roster_teams == {"Miami", "Wake Forest"}  # only Miami's own matchup's teams
    assert "Texas" not in roster_teams
    assert "Ohio State" not in roster_teams


def test_run_week_teams_filter_falls_back_to_fresh_render_without_prior_history(monkeypatch, tmp_path):
    _patch_dirs(monkeypatch, tmp_path)
    _patch_network(monkeypatch)

    call_labels = []

    def _counting_build_both_directions(matchup, year, sp_plus_table=None, talent_table=None, browser_fetch=None, team_colors=None):
        call_labels.append(matchup["label"])
        return _fake_build_both_directions(matchup, year, sp_plus_table=sp_plus_table, talent_table=talent_table)

    monkeypatch.setattr(run_week.render_widget, "build_both_directions", _counting_build_both_directions)

    # No prior run at all -- a --teams filter on a cold history/ directory
    # must still render every matchup that has no snapshot to reuse,
    # rather than silently dropping it from the index.
    result = run_week.run_week(2026, 1, today="2026-09-16", only_teams=["Miami"])

    assert sorted(call_labels) == [
        "2026-wk01-badteama-badteamb",
        "2026-wk01-miami-wake-forest",
        "2026-wk01-texas-ohio-state",
    ]
    assert result["rendered_count"] == 2
