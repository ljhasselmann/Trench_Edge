import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import render_widget
from render_widget import WidgetContext, render, write_history_snapshot
from fetch_roster import StarterWeight, MassInputs
from fetch_talent import TalentInputs
from fetch_cfbd import TeamAdvancedStats, SideStats


def _sample_context(**overrides):
    defaults = dict(
        matchup_label="2026-wk03-miami-wake",
        team_a="Miami",
        team_b="Wake Forest",
        side="team_a_ol_vs_team_b_dl",
        year=2026,
        generated_at="2026-09-15 12:00 UTC",
        mass={
            "team_a_avg_weight": 320.0,
            "team_b_avg_weight": 290.0,
            "weight_diff_lbs": 30.0,
            "score": 3.0,
            "team_a_starters": [StarterWeight(name="Jacob Hawks", weight_lbs=330, confidence="confirmed", source="cfbd_roster")],
            "team_b_starters": [],
        },
        push={"available": False, "score": None, "sp_plus_gap": None, "raw": {}},
        continuity={
            "team_a_returning": 3, "team_b_returning": 2, "net_returning": 1, "score": 1.0,
            "team_a_driver": None, "team_a_note": None, "team_b_driver": None, "team_b_note": None,
        },
        composite=None,
        warnings=["Composite not computed -- missing: Push"],
    )
    defaults.update(overrides)
    return WidgetContext(**defaults)


def test_render_is_pure_and_produces_html():
    html = render(_sample_context())
    assert "Miami" in html
    assert "Wake Forest" in html
    assert "330 lbs" in html
    assert "Jacob Hawks" in html
    assert "data unavailable" in html  # Push bar
    assert "Not computed this run" in html  # composite section
    assert "Composite not computed -- missing: Push" in html  # caveat


def test_render_shows_push_score_when_available():
    ctx = _sample_context(push={"available": True, "score": 4.4, "sp_plus_gap": 22.2, "raw": {}})
    html = render(ctx)
    assert "data unavailable" not in html
    assert "+4.4" in html


def test_render_shows_composite_when_present():
    ctx = _sample_context(composite={"value": 2.2, "verdict": "slight-to-moderate edge"})
    html = render(ctx)
    assert "+2.2" in html
    assert "slight-to-moderate edge" in html


def test_render_never_double_escapes_team_names_with_special_chars():
    ctx = _sample_context(team_a="Texas A&M")
    html = render(ctx)
    assert "Texas A&amp;M" in html  # autoescape is on -- this is correct, not a bug
    assert "Texas A&M" not in html.replace("Texas A&amp;M", "")


class _FakeMass:
    def __init__(self, avg_ol=None, avg_dl=None, starters=None, warnings=None):
        self.avg_ol_weight = avg_ol
        self.avg_dl_weight = avg_dl
        self.ol_starters = starters or []
        self.dl_starters = starters or []
        self.warnings = warnings or []


class _FakeContinuity:
    def __init__(self, ol=None, dl=None, driver=None, note=None, warnings=None):
        self.returning_ol_starters = ol
        self.returning_dl_starters = dl
        self.continuity_driver = driver
        self.continuity_note = note
        self.warnings = warnings or []


def test_build_context_flags_missing_composite_without_sp_plus_gap(monkeypatch):
    monkeypatch.setattr(render_widget, "compute_mass_inputs", lambda team, year, **kw: _FakeMass(avg_ol=320, avg_dl=290))
    monkeypatch.setattr(render_widget, "compute_continuity_inputs", lambda team, year, **kw: _FakeContinuity(ol=3, dl=2))
    monkeypatch.setattr(render_widget, "fetch_team_trench_stats", lambda team, year, **kw: TeamAdvancedStats(
        team=team, year=year, offense=SideStats(), defense=SideStats(), raw={}
    ))

    ctx = render_widget.build_context({"label": "test", "team_a": "Miami", "team_b": "Wake Forest", "side": "team_a_ol_vs_team_b_dl"}, 2026)

    assert ctx.mass["score"] == 3.0  # (320-290)/10
    assert ctx.continuity["score"] == 1.0  # 3-2
    assert ctx.push["score"] is None
    assert ctx.composite is None
    assert any("sp_plus_gap unavailable" in w for w in ctx.warnings)
    assert any("Composite not computed -- missing: Push" in w for w in ctx.warnings)


def test_build_context_computes_full_composite_with_sp_plus_gap(monkeypatch):
    monkeypatch.setattr(render_widget, "compute_mass_inputs", lambda team, year, **kw: _FakeMass(avg_ol=320, avg_dl=290))
    monkeypatch.setattr(render_widget, "compute_continuity_inputs", lambda team, year, **kw: _FakeContinuity(ol=3, dl=2))
    monkeypatch.setattr(render_widget, "fetch_team_trench_stats", lambda team, year, **kw: TeamAdvancedStats(
        team=team, year=year, offense=SideStats(), defense=SideStats(), raw={}
    ))

    ctx = render_widget.build_context({
        "label": "test", "team_a": "Miami", "team_b": "Wake Forest",
        "side": "team_a_ol_vs_team_b_dl", "sp_plus_gap": 22.2,
    }, 2026)

    assert round(ctx.push["score"], 4) == 4.44  # 22.2 / 5
    assert ctx.composite is not None
    # weights.yaml: mass .4, push .4, continuity .2 -> .4*3.0 + .4*4.44 + .2*1.0 = 3.176
    assert round(ctx.composite["value"], 3) == 3.176
    assert not any("Composite not computed" in w for w in ctx.warnings)
    assert any("No 'week' set" in w for w in ctx.warnings)  # used the static fallback, not a live fetch


def test_build_context_prefers_live_sp_plus_fetch_when_week_is_set(monkeypatch):
    monkeypatch.setattr(render_widget, "compute_mass_inputs", lambda team, year, **kw: _FakeMass(avg_ol=320, avg_dl=290))
    monkeypatch.setattr(render_widget, "compute_continuity_inputs", lambda team, year, **kw: _FakeContinuity(ol=3, dl=2))
    monkeypatch.setattr(render_widget, "fetch_team_trench_stats", lambda team, year, **kw: TeamAdvancedStats(
        team=team, year=year, offense=SideStats(), defense=SideStats(), raw={}
    ))
    monkeypatch.setattr(render_widget.fetch_sp_plus, "compute_sp_plus_gap", lambda a, b, week, **kw: 30.0)

    ctx = render_widget.build_context({
        "label": "test", "team_a": "Miami", "team_b": "Wake Forest",
        "side": "team_a_ol_vs_team_b_dl", "week": 3, "sp_plus_gap": 22.2,  # stale fallback, should be ignored
    }, 2026)

    assert ctx.push["sp_plus_gap"] == 30.0  # live value used, not the stale config fallback
    assert not any("Live SP+ fetch failed" in w for w in ctx.warnings)


def test_build_context_falls_back_to_config_when_live_sp_plus_fetch_fails(monkeypatch):
    monkeypatch.setattr(render_widget, "compute_mass_inputs", lambda team, year, **kw: _FakeMass(avg_ol=320, avg_dl=290))
    monkeypatch.setattr(render_widget, "compute_continuity_inputs", lambda team, year, **kw: _FakeContinuity(ol=3, dl=2))
    monkeypatch.setattr(render_widget, "fetch_team_trench_stats", lambda team, year, **kw: TeamAdvancedStats(
        team=team, year=year, offense=SideStats(), defense=SideStats(), raw={}
    ))

    def _raise(a, b, week, **kw):
        raise render_widget.fetch_sp_plus.SPPlusFetchError("tab not published yet")
    monkeypatch.setattr(render_widget.fetch_sp_plus, "compute_sp_plus_gap", _raise)

    ctx = render_widget.build_context({
        "label": "test", "team_a": "Miami", "team_b": "Wake Forest",
        "side": "team_a_ol_vs_team_b_dl", "week": 3, "sp_plus_gap": 22.2,
    }, 2026)

    assert ctx.push["sp_plus_gap"] == 22.2  # fell back to config's stored value
    assert any("Live SP+ fetch failed" in w for w in ctx.warnings)


def test_build_context_handles_tier1_fetch_failure_without_crashing(monkeypatch):
    monkeypatch.setattr(render_widget, "compute_mass_inputs", lambda team, year, **kw: _FakeMass())
    monkeypatch.setattr(render_widget, "compute_continuity_inputs", lambda team, year, **kw: _FakeContinuity())

    def _raise(team, year, **kw):
        raise RuntimeError("network down")
    monkeypatch.setattr(render_widget, "fetch_team_trench_stats", _raise)

    ctx = render_widget.build_context({"label": "test", "team_a": "Miami", "team_b": "Wake Forest", "side": "team_a_ol_vs_team_b_dl"}, 2026)

    assert any("Tier1] fetch failed" in w for w in ctx.warnings)
    assert ctx.mass["score"] is None


def test_write_history_snapshot_produces_readable_json_with_starter_detail(tmp_path):
    ctx = _sample_context()
    path = write_history_snapshot(ctx, tmp_path)

    assert path == tmp_path / "2026-wk03-miami-wake.json"
    data = json.loads(path.read_text())

    assert data["team_a"] == "Miami"
    assert data["team_b"] == "Wake Forest"
    assert data["mass"]["team_a_starters"] == [
        {"name": "Jacob Hawks", "weight_lbs": 330, "confidence": "confirmed", "source": "cfbd_roster"}
    ]
    assert data["mass"]["score"] == 3.0
    assert data["composite"] is None


def test_write_history_snapshot_creates_history_dir_if_missing(tmp_path):
    ctx = _sample_context()
    history_dir = tmp_path / "nested" / "history"
    path = write_history_snapshot(ctx, history_dir)
    assert path.exists()


def test_build_both_directions_fetches_each_team_exactly_once(monkeypatch):
    call_counts = {"Miami": 0, "Wake Forest": 0}

    def _fake_mass(team, year, **kw):
        call_counts[team] += 1
        return _FakeMass(avg_ol=320, avg_dl=290)

    monkeypatch.setattr(render_widget, "compute_mass_inputs", _fake_mass)
    monkeypatch.setattr(render_widget, "compute_continuity_inputs", lambda team, year, **kw: _FakeContinuity(ol=3, dl=2))
    monkeypatch.setattr(render_widget, "fetch_team_trench_stats", lambda team, year, **kw: TeamAdvancedStats(
        team=team, year=year, offense=SideStats(), defense=SideStats(), raw={}
    ))
    monkeypatch.setattr(render_widget.fetch_sp_plus, "compute_sp_plus_gap", lambda a, b, week, **kw: 22.2)

    matchup = {
        "label": "test-game", "team_a": "Miami", "team_b": "Wake Forest",
        "side": "team_a_ol_vs_team_b_dl", "week": 3,
    }
    render_widget.build_both_directions(matchup, 2026)

    # Once per team, not once per direction (would be 2 each if fetch_team_data
    # were called separately per direction instead of reused).
    assert call_counts == {"Miami": 1, "Wake Forest": 1}


def test_build_both_directions_reverse_push_is_negation_and_teams_swap(monkeypatch):
    monkeypatch.setattr(render_widget, "compute_mass_inputs", lambda team, year, **kw: _FakeMass(avg_ol=320, avg_dl=290))
    monkeypatch.setattr(render_widget, "compute_continuity_inputs", lambda team, year, **kw: _FakeContinuity(ol=3, dl=2))
    monkeypatch.setattr(render_widget, "fetch_team_trench_stats", lambda team, year, **kw: TeamAdvancedStats(
        team=team, year=year, offense=SideStats(), defense=SideStats(), raw={}
    ))
    monkeypatch.setattr(render_widget.fetch_sp_plus, "compute_sp_plus_gap", lambda a, b, week, **kw: 22.2)

    matchup = {
        "label": "test-game", "team_a": "Miami", "team_b": "Wake Forest",
        "side": "team_a_ol_vs_team_b_dl", "week": 3,
    }
    ctx_a, ctx_b = render_widget.build_both_directions(matchup, 2026)

    # Direction A: Miami OL vs Wake Forest DL (matches the matchup's own side).
    assert ctx_a.team_a == "Miami" and ctx_a.team_b == "Wake Forest"
    assert ctx_a.side == "team_a_ol_vs_team_b_dl"
    assert round(ctx_a.push["sp_plus_gap"], 4) == 22.2

    # Direction B: Wake Forest OL vs Miami DL -- teams swap, push negates.
    assert ctx_b.team_a == "Wake Forest" and ctx_b.team_b == "Miami"
    assert ctx_b.side == "team_b_ol_vs_team_a_dl"
    assert round(ctx_b.push["sp_plus_gap"], 4) == -22.2
    assert round(ctx_b.push["score"], 4) == -round(ctx_a.push["score"], 4)

    assert ctx_a.matchup_label == "test-game-a"
    assert ctx_b.matchup_label == "test-game-b"


def test_render_game_includes_both_directions():
    ctx_a = _sample_context(team_a="Miami", team_b="Wake Forest", matchup_label="g-a")
    ctx_b = _sample_context(team_a="Wake Forest", team_b="Miami", matchup_label="g-b")
    html = render_widget.render_game("test-game", "Miami", "Wake Forest", ctx_a, ctx_b)
    assert html.count("starting OL") == 2  # both directions' widget partials rendered
    assert "Miami OL vs Wake Forest DL" in html
    assert "Wake Forest OL vs Miami DL" in html


def test_game_to_history_dict_nests_both_directions():
    ctx_a = _sample_context(team_a="Miami", team_b="Wake Forest", matchup_label="g-a")
    ctx_b = _sample_context(team_a="Wake Forest", team_b="Miami", matchup_label="g-b")
    data = render_widget.game_to_history_dict("test-game", "Miami", "Wake Forest", 2026, ctx_a, ctx_b)

    assert data["matchup_label"] == "test-game"
    assert data["team_a"] == "Miami"
    assert data["team_b"] == "Wake Forest"
    assert data["direction_a"]["matchup_label"] == "g-a"
    assert data["direction_b"]["matchup_label"] == "g-b"


def test_write_game_history_snapshot(tmp_path):
    ctx_a = _sample_context(matchup_label="g-a")
    ctx_b = _sample_context(matchup_label="g-b")
    path = render_widget.write_game_history_snapshot("test-game", "Miami", "Wake Forest", 2026, ctx_a, ctx_b, tmp_path)
    assert path == tmp_path / "test-game.json"
    data = json.loads(path.read_text())
    assert data["direction_a"]["team_a"] == "Miami"


def test_game_index_entry_counts_warnings_from_both_directions():
    ctx_a = _sample_context(warnings=["a warning"])
    ctx_b = _sample_context(warnings=["b warning 1", "b warning 2"])
    entry = render_widget.game_index_entry(
        "test-game", "Miami", "Wake Forest", "test-game.html", {"Miami"}, ctx_a, ctx_b
    )
    assert entry["warning_count"] == 3
    assert entry["top25_teams"] == {"Miami"}
    assert entry["href"] == "test-game.html"


def test_render_index_links_and_badges_ranked_teams():
    ctx_a = _sample_context(composite={"value": 2.2, "verdict": "slight-to-moderate edge"})
    ctx_b = _sample_context(composite=None, warnings=["Composite not computed -- missing: Mass"])
    entry = render_widget.game_index_entry(
        "2026-wk03-miami-wake-forest", "Miami", "Wake Forest", "2026-wk03-miami-wake-forest.html", {"Miami"}, ctx_a, ctx_b
    )
    html = render_widget.render_index("2026, Week 3", [entry])

    assert "Miami" in html and "Wake Forest" in html
    assert "(Top 25)" in html
    assert '<a href="2026-wk03-miami-wake-forest.html">' in html
    assert "+2.2" in html and "slight-to-moderate edge" in html
    assert "not computed" in html  # direction_b has no composite


def test_render_index_handles_empty_week():
    html = render_widget.render_index("2026, Week 0", [])
    assert "0 game(s)" in html
