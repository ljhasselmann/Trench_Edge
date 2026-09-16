import json
import sys
from contextlib import contextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

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


def _fake_roster_sections(team: str):
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


def _fake_fetch_roster(team, browser_fetch=None):
    if team == "Ohio State":
        raise fetch_puntandrally.PuntAndRallyFetchError(f"mock failure for {team}")
    return _fake_roster_sections(team)


@contextmanager
def _fake_browser_session():
    yield None  # _fake_fetch_roster ignores browser_fetch entirely


def _fake_ctx(label: str, side: str) -> render_widget.WidgetContext:
    return render_widget.WidgetContext(
        matchup_label=label,
        team_a="TeamA",
        team_b="TeamB",
        side=side,
        year=2026,
        generated_at="2026-01-01 00:00 UTC",
        mass={
            "team_a_avg_weight": 300, "team_b_avg_weight": 280, "weight_diff_lbs": 20,
            "score": 1.0, "team_a_starters": [], "team_b_starters": [],
        },
        push={"available": True, "score": 1.0, "sp_plus_gap": 5.0, "raw": {}},
        continuity={
            "team_a_returning": 3, "team_b_returning": 2, "net_returning": 1, "score": 1.0,
            "team_a_driver": None, "team_a_note": None, "team_b_driver": None, "team_b_note": None,
        },
        composite={"value": 1.5, "verdict": "test verdict"},
        warnings=[],
    )


def _fake_build_both_directions(matchup, year, sp_plus_table=None, talent_table=None):
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
    monkeypatch.setattr(run_week.fetch_puntandrally, "browser_session", _fake_browser_session)
    monkeypatch.setattr(run_week.fetch_puntandrally, "fetch_roster", _fake_fetch_roster)
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


def test_run_week_reports_puntandrally_failures_without_aborting(monkeypatch, tmp_path):
    _patch_dirs(monkeypatch, tmp_path)
    _patch_network(monkeypatch)

    result = run_week.run_week(2026, 1, today="2026-09-16")

    failed_teams = {r["team"] for r in result["roster_failures"]}
    assert failed_teams == {"Ohio State"}
    # Rendering still succeeded for the Texas/Ohio State game despite the
    # roster fetch failure -- roster population and rendering are decoupled.
    assert result["rendered_count"] == 2


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

    def _counting_build_both_directions(matchup, year, sp_plus_table=None, talent_table=None):
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

    def _counting_build_both_directions(matchup, year, sp_plus_table=None, talent_table=None):
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
