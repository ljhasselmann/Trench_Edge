import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import render_widget
from render_widget import WidgetContext, render
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
    monkeypatch.setattr(render_widget, "compute_mass_inputs", lambda team, year: _FakeMass(avg_ol=320, avg_dl=290))
    monkeypatch.setattr(render_widget, "compute_continuity_inputs", lambda team, year: _FakeContinuity(ol=3, dl=2))
    monkeypatch.setattr(render_widget, "fetch_team_trench_stats", lambda team, year: TeamAdvancedStats(
        team=team, year=year, offense=SideStats(), defense=SideStats(), raw={}
    ))

    ctx = render_widget.build_context({"label": "test", "team_a": "Miami", "team_b": "Wake Forest", "side": "team_a_ol_vs_team_b_dl"}, 2026)

    assert ctx.mass["score"] == 3.0  # (320-290)/10
    assert ctx.continuity["score"] == 1.0  # 3-2
    assert ctx.push["score"] is None
    assert ctx.composite is None
    assert any("sp_plus_gap not set" in w for w in ctx.warnings)
    assert any("Composite not computed -- missing: Push" in w for w in ctx.warnings)


def test_build_context_computes_full_composite_with_sp_plus_gap(monkeypatch):
    monkeypatch.setattr(render_widget, "compute_mass_inputs", lambda team, year: _FakeMass(avg_ol=320, avg_dl=290))
    monkeypatch.setattr(render_widget, "compute_continuity_inputs", lambda team, year: _FakeContinuity(ol=3, dl=2))
    monkeypatch.setattr(render_widget, "fetch_team_trench_stats", lambda team, year: TeamAdvancedStats(
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


def test_build_context_handles_tier1_fetch_failure_without_crashing(monkeypatch):
    monkeypatch.setattr(render_widget, "compute_mass_inputs", lambda team, year: _FakeMass())
    monkeypatch.setattr(render_widget, "compute_continuity_inputs", lambda team, year: _FakeContinuity())

    def _raise(team, year):
        raise RuntimeError("network down")
    monkeypatch.setattr(render_widget, "fetch_team_trench_stats", _raise)

    ctx = render_widget.build_context({"label": "test", "team_a": "Miami", "team_b": "Wake Forest", "side": "team_a_ol_vs_team_b_dl"}, 2026)

    assert any("Tier 1 fetch failed" in w for w in ctx.warnings)
    assert ctx.mass["score"] is None
