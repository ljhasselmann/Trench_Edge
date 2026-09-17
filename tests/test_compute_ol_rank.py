import csv
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from compute_ol_rank import (
    OLRankResult,
    TeamOLAttributes,
    compute_ol_rank,
    compute_performance_score,
    percentile_rank,
    rank_league,
    write_lineman_stats_csv,
    write_lineman_stats_json,
    write_ol_rank_table_csv,
    write_ol_rank_table_json,
)
from fetch_cfbd import DirectionSplit, RushingDirectionSplits, SideStats
from fetch_roster import StarterWeight

WEIGHTS = {"mass": 0.3, "push": 0.3, "experience": 0.2, "recruiting": 0.2}


def test_percentile_rank_basic():
    population = [100, 200, 300, 400, 500]
    assert percentile_rank(100, population) == 10.0
    assert percentile_rank(500, population) == 90.0
    assert percentile_rank(300, population) == 50.0


def test_percentile_rank_handles_ties_at_midpoint():
    population = [10, 10, 10, 20, 30]
    # 0 below, 3 tied -> (0 + 1.5) / 5 * 100
    assert percentile_rank(10, population) == 30.0


def test_percentile_rank_empty_population_raises():
    with pytest.raises(ValueError):
        percentile_rank(5, [])


def test_compute_performance_score_averages_and_inverts_stuff_rate():
    offense = SideStats(stuff_rate=0.10, line_yards=3.2, power_success=0.80)
    score = compute_performance_score(offense)
    assert score == pytest.approx((0.90 + 3.2 + 0.80) / 3)


def test_compute_performance_score_missing_fields_averages_whats_present():
    offense = SideStats(stuff_rate=0.10, line_yards=None, power_success=None)
    assert compute_performance_score(offense) == pytest.approx(0.90)


def test_compute_performance_score_all_missing_returns_none():
    assert compute_performance_score(SideStats()) is None


def test_compute_performance_score_ignores_direction_splits_entirely():
    # Regression guard: rushDirection data is raw output only (see this
    # module's own docstring on why) -- compute_performance_score takes
    # ONLY a SideStats, it has no way to see direction splits at all, so
    # its result can never change based on them.
    offense = SideStats(stuff_rate=0.10, line_yards=3.2, power_success=0.80)
    assert compute_performance_score(offense) == pytest.approx((0.90 + 3.2 + 0.80) / 3)


def test_compute_performance_score_includes_adjusted_sack_rate_inverted():
    # adjusted_sack_rate is LOWER = better for the offense (inverted before scoring).
    offense = SideStats(stuff_rate=0.10, line_yards=3.2, power_success=0.80, adjusted_sack_rate=0.05)
    score = compute_performance_score(offense)
    # four inputs: (1-0.10) + 3.2 + 0.80 + (1-0.05) = 0.90 + 3.2 + 0.80 + 0.95
    assert score == pytest.approx((0.90 + 3.2 + 0.80 + 0.95) / 4)


def test_compute_performance_score_includes_rushing_ppa():
    # rushing_ppa is signed EPA per rush (positive = good); used directly, not inverted.
    offense = SideStats(stuff_rate=0.10, line_yards=3.2, power_success=0.80, rushing_ppa=0.18)
    score = compute_performance_score(offense)
    assert score == pytest.approx((0.90 + 3.2 + 0.80 + 0.18) / 4)


def test_compute_performance_score_all_five_inputs():
    offense = SideStats(
        stuff_rate=0.15, line_yards=3.0, power_success=0.75,
        adjusted_sack_rate=0.04, rushing_ppa=0.12,
    )
    score = compute_performance_score(offense)
    expected = ((1 - 0.15) + 3.0 + 0.75 + (1 - 0.04) + 0.12) / 5
    assert score == pytest.approx(expected)


def test_compute_performance_score_tfl_rate_allowed_not_scored():
    # tfl_rate_allowed is stored on SideStats but intentionally excluded
    # from the performance score (it's a subset of what stuff_rate captures).
    offense_with = SideStats(stuff_rate=0.15, line_yards=3.0, power_success=0.75, tfl_rate_allowed=0.08)
    offense_without = SideStats(stuff_rate=0.15, line_yards=3.0, power_success=0.75)
    assert compute_performance_score(offense_with) == compute_performance_score(offense_without)


def _team(team, weight=None, snap_pct=None, rating=None, perf=None):
    return TeamOLAttributes(
        team=team, avg_ol_weight=weight, returning_ol_snap_pct=snap_pct,
        avg_ol_rating=rating, performance_score=perf,
    )


def test_compute_ol_rank_weights_available_attributes_only():
    league = [
        _team("Miami", weight=310, snap_pct=60, rating=90, perf=3.0),
        _team("Wake Forest", weight=295, snap_pct=40, rating=70, perf=2.0),
    ]
    result = compute_ol_rank(league[0], league, WEIGHTS)
    # Two-team league, Miami is the higher value in every attribute: with
    # the tie-handling convention (self counts as "tied"), the top of a
    # 2-team population lands at the 75th percentile, not 100th.
    assert result.mass_pctile == 75.0
    assert result.experience_pctile == 75.0
    assert result.recruiting_pctile == 75.0
    assert result.performance_pctile == 75.0
    assert result.composite_0_100 == pytest.approx(75.0)


def test_compute_ol_rank_missing_attribute_reweights_remaining():
    # Team is missing recruiting data entirely -- composite should be the
    # weighted average of the other three, not None and not zero-filled.
    attrs = _team("Troy", weight=300, snap_pct=50, rating=None, perf=2.5)
    league = [attrs, _team("Buffalo", weight=290, snap_pct=45, rating=80, perf=2.0)]
    result = compute_ol_rank(attrs, league, WEIGHTS)
    assert result.recruiting_pctile is None
    assert result.composite_0_100 is not None
    # mass(.3) + experience(.2) + push/performance(.3), all at the 75th
    # percentile (Troy is the higher value in every available attribute,
    # in a 2-team league -- see the tie-handling convention above)
    assert result.composite_0_100 == pytest.approx(75.0)


def test_compute_ol_rank_all_attributes_missing_returns_none_composite():
    attrs = _team("SMU")
    league = [attrs, _team("Rice", weight=290)]
    result = compute_ol_rank(attrs, league, WEIGHTS)
    assert result.composite_0_100 is None


def test_rank_league_sorts_descending_and_assigns_rank_of():
    results = [
        OLRankResult("A", 50, 50, 50, 50, composite_0_100=60.0),
        OLRankResult("B", 90, 90, 90, 90, composite_0_100=95.0),
        OLRankResult("C", 10, 10, 10, 10, composite_0_100=None),
    ]
    ranked = rank_league(results)
    assert [r.team for r in ranked] == ["B", "A", "C"]
    assert ranked[0].rank == 1 and ranked[0].of == 2
    assert ranked[1].rank == 2 and ranked[1].of == 2
    assert ranked[2].rank is None and ranked[2].of is None


def test_write_ol_rank_table_csv_round_trips(tmp_path):
    attrs = _team("Miami", weight=310, snap_pct=60, rating=90, perf=3.0)
    league = [attrs]
    result = compute_ol_rank(attrs, league, WEIGHTS)
    ranked = rank_league([result])
    out = tmp_path / "ol_rank_table.csv"

    write_ol_rank_table_csv(ranked, {"Miami": attrs}, out)

    with open(out, newline="") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 1
    assert rows[0]["team"] == "Miami"
    assert rows[0]["rank"] == "1"
    assert float(rows[0]["avg_ol_weight"]) == 310


def test_fetch_league_ol_attributes_threads_direction_splits_through(monkeypatch):
    import compute_ol_rank as col
    import fetch_cfbd
    import fetch_roster
    import fetch_talent

    monkeypatch.setattr(fetch_cfbd, "fetch_fbs_teams", lambda year, **kw: [{"school": "Miami"}])
    monkeypatch.setattr(fetch_talent, "fetch_talent_table", lambda year, **kw: {})
    monkeypatch.setattr(fetch_roster, "compute_mass_inputs", lambda team, year, **kw: fetch_roster.MassInputs(team=team))
    monkeypatch.setattr(fetch_talent, "compute_experience_inputs", lambda team, year, **kw: fetch_talent.ExperienceInputs(team=team))
    monkeypatch.setattr(fetch_talent, "compute_recruiting_talent_inputs", lambda team: fetch_talent.RecruitingTalentInputs(team=team))
    monkeypatch.setattr(fetch_cfbd, "fetch_team_trench_stats", lambda team, year, **kw: fetch_cfbd.TeamAdvancedStats(team=team, year=year, offense=SideStats(), defense=SideStats(), raw={}))

    fake_splits = RushingDirectionSplits(team="Miami", left=DirectionSplit(success_rate=0.6, play_count=5))
    monkeypatch.setattr(fetch_cfbd, "fetch_rushing_direction_splits", lambda team, year, **kw: fake_splits)

    attrs_by_team = col.fetch_league_ol_attributes(2026)

    assert attrs_by_team["Miami"].direction_splits is fake_splits
    assert attrs_by_team["Miami"].direction_splits.left.success_rate == 0.6


def test_fetch_league_ol_attributes_threads_browser_fetch_to_experience_lookup(monkeypatch):
    # Confirmed live: leaving browser_fetch=None here meant every team's
    # Experience lookup launched its own fresh Chromium instance -- the
    # actual bottleneck in a full league-wide run. A caller-supplied
    # browser_fetch must reach compute_experience_inputs so all teams can
    # share one browser session (see run_week.py's own use of this
    # pattern).
    import compute_ol_rank as col
    import fetch_cfbd
    import fetch_roster
    import fetch_talent

    monkeypatch.setattr(fetch_cfbd, "fetch_fbs_teams", lambda year, **kw: [{"school": "Miami"}])
    monkeypatch.setattr(fetch_talent, "fetch_talent_table", lambda year, **kw: {})
    monkeypatch.setattr(fetch_roster, "compute_mass_inputs", lambda team, year, **kw: fetch_roster.MassInputs(team=team))
    monkeypatch.setattr(fetch_talent, "compute_recruiting_talent_inputs", lambda team: fetch_talent.RecruitingTalentInputs(team=team))
    monkeypatch.setattr(fetch_cfbd, "fetch_team_trench_stats", lambda team, year, **kw: fetch_cfbd.TeamAdvancedStats(team=team, year=year, offense=SideStats(), defense=SideStats(), raw={}))
    monkeypatch.setattr(fetch_cfbd, "fetch_rushing_direction_splits", lambda team, year, **kw: RushingDirectionSplits(team=team))

    captured = {}

    def _fake_compute_experience_inputs(team, year, **kwargs):
        captured["browser_fetch"] = kwargs.get("browser_fetch")
        return fetch_talent.ExperienceInputs(team=team)

    monkeypatch.setattr(fetch_talent, "compute_experience_inputs", _fake_compute_experience_inputs)

    sentinel = object()
    col.fetch_league_ol_attributes(2026, browser_fetch=sentinel)

    assert captured["browser_fetch"] is sentinel


def test_write_ol_rank_table_csv_includes_direction_splits_as_raw_columns(tmp_path):
    splits = RushingDirectionSplits(
        team="Miami",
        left=DirectionSplit(success_rate=0.5, play_count=10),
        middle=DirectionSplit(success_rate=None, play_count=0),
        right=DirectionSplit(success_rate=0.7, play_count=8),
    )
    attrs = _team("Miami", weight=310, snap_pct=60, rating=90, perf=3.0)
    attrs.direction_splits = splits
    league = [attrs]
    result = compute_ol_rank(attrs, league, WEIGHTS)
    ranked = rank_league([result])
    out = tmp_path / "ol_rank_table.csv"

    write_ol_rank_table_csv(ranked, {"Miami": attrs}, out)

    with open(out, newline="") as f:
        rows = list(csv.DictReader(f))
    assert float(rows[0]["left_success_rate"]) == 0.5
    assert rows[0]["middle_success_rate"] == ""  # None -- no resolved plays
    assert float(rows[0]["right_success_rate"]) == 0.7


def test_write_ol_rank_table_json_matches_csv_data(tmp_path):
    attrs = _team("Miami", weight=310, snap_pct=60, rating=90, perf=3.0)
    league = [attrs]
    result = compute_ol_rank(attrs, league, WEIGHTS)
    ranked = rank_league([result])
    out = tmp_path / "ol_rank_table.json"

    write_ol_rank_table_json(ranked, {"Miami": attrs}, out)

    rows = json.loads(out.read_text())
    assert len(rows) == 1
    assert rows[0]["team"] == "Miami"
    assert rows[0]["rank"] == 1
    assert rows[0]["avg_ol_weight"] == 310
    assert rows[0]["left_success_rate"] is None  # no direction_splits fetched -- None, not 0


def test_write_lineman_stats_csv_one_row_per_starter(tmp_path):
    starter = StarterWeight(
        name="Jacob Hawks", weight_lbs=330, confidence="confirmed", source="cfbd_roster",
        jersey="78", class_year="SR", snaps_multi_year=900, recruit_rating=88, recruit_stars=4, position_tag="T",
    )
    attrs = TeamOLAttributes(team="Miami", ol_starters=[starter])
    out = tmp_path / "lineman_stats.csv"

    write_lineman_stats_csv({"Miami": attrs}, out)

    with open(out, newline="") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 1
    assert rows[0]["name"] == "Jacob Hawks"
    assert rows[0]["team"] == "Miami"
    assert rows[0]["jersey"] == "78"
    assert int(rows[0]["snaps_multi_year"]) == 900


def test_write_lineman_stats_json_matches_csv_data(tmp_path):
    starter = StarterWeight(
        name="Jacob Hawks", weight_lbs=330, confidence="confirmed", source="cfbd_roster",
        jersey="78", class_year="SR", snaps_multi_year=900, recruit_rating=88, recruit_stars=4, position_tag="T",
    )
    attrs = TeamOLAttributes(team="Miami", ol_starters=[starter])
    out = tmp_path / "lineman_stats.json"

    write_lineman_stats_json({"Miami": attrs}, out)

    rows = json.loads(out.read_text())
    assert len(rows) == 1
    assert rows[0]["name"] == "Jacob Hawks"
    assert rows[0]["jersey"] == "78"
    assert rows[0]["snaps_multi_year"] == 900
