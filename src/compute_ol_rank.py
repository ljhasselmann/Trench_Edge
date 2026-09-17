"""TrenchEdge OL Rank -- an absolute, league-wide OL score (DESIGN.md
Section 5b), as opposed to compute_composite.py's per-matchup
DIFFERENTIAL (this team's OL vs. one specific opponent's DL).

The four underlying attribute families are the same ones compute_composite
already uses (Mass, Experience, Recruiting, and a run-blocking Performance
attribute replacing Push's whole-team SP+ gap for this absolute-rank
purpose) -- but here each is percentile-ranked against the WHOLE LEAGUE's
distribution for that attribute instead of hand-picked-divisor-normalized
against one opponent's value. Percentile was chosen over a z-score: the
output is literally a "rank," a percentile reads directly ("74th
percentile"), and it doesn't assume any of these attributes are normally
distributed (recruiting ratings and performance stats are skewed; playing
weight roughly is, but there's no reason to require that of every input).

Reuses config/weights.yaml's existing mass/push/experience/recruiting
weights directly (the "push" weight is applied to the Performance
percentile here) -- per this session's decision, there's no separate
weights file for the absolute rank. If the correlation analysis in
scripts/analyze_ol_rank_correlation.py ever suggests these need to diverge
from the matchup-differential weights, that's a deliberate future change,
not something this module should silently assume.

This module is pure computation given already-fetched per-team attribute
values (see fetch_league_ol_attributes for the one function that actually
touches the network) -- percentile_rank/compute_ol_rank themselves take no
network calls, so they're fully testable offline, same spirit as
compute_composite.py.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import fetch_cfbd
import fetch_roster
import fetch_talent


@dataclass
class TeamOLAttributes:
    team: str
    avg_ol_weight: Optional[float] = None
    returning_ol_snap_pct: Optional[float] = None
    avg_ol_rating: Optional[float] = None
    performance_score: Optional[float] = None
    direction_splits: Optional["fetch_cfbd.RushingDirectionSplits"] = None  # raw data only -- see compute_performance_score, never folded into the score
    ol_starters: list = field(default_factory=list)  # StarterWeight, for lineman_stats.csv
    warnings: list[str] = field(default_factory=list)


def compute_performance_score(offense: "fetch_cfbd.SideStats") -> Optional[float]:
    """Average of five OL-specific signals from CFBD, offense side only
    (this is an OL rank, not a matchup):

    Run blocking (from /stats/season/advanced):
      - stuff_rate inverted (1 - rate): LOWER is better; stopped runs penalize
      - line_yards: avg yards attributable to OL blocking (0-5 yd zone)
      - power_success: short-yardage conversion rate (3rd/4th and short)

    Pass protection (from /stats/season):
      - adjusted_sack_rate inverted (1 - rate): sacks allowed per dropback;
        LOWER is better; covers pass blocking, absent from the original three

    Opponent-adjusted run efficiency (from /ppa/teams):
      - rushing_ppa: predicted points added per rush play, opponent-adjusted;
        positive = scoring-favorable; the only signal here that accounts for
        who the team faced

    secondLevelYards/openFieldYards excluded: those describe yards AFTER the
    OL's zone (5+ yards), which is more a running back's doing.
    tfl_rate_allowed excluded: TFLs are a subset of stuffed runs already
    captured by stuff_rate; scoring both double-counts the same phenomenon.
    rushDirection splits excluded: CFBD's play-text parsing leaves ~40% of
    plays with no resolved direction; inconsistent coverage would introduce
    noise into a scored metric.

    Inputs are on different native scales (a 0-1 rate, a yards figure, a
    signed EPA value) -- this function does not normalize; percentile_rank
    in compute_ol_rank handles cross-team comparability.
    """
    values = []
    if offense.stuff_rate is not None:
        values.append(1.0 - offense.stuff_rate)
    if offense.line_yards is not None:
        values.append(offense.line_yards)
    if offense.power_success is not None:
        values.append(offense.power_success)
    if offense.adjusted_sack_rate is not None:
        values.append(1.0 - offense.adjusted_sack_rate)
    if offense.rushing_ppa is not None:
        values.append(offense.rushing_ppa)
    if not values:
        return None
    return sum(values) / len(values)


def percentile_rank(value: float, population: list[float]) -> float:
    """Percentile (0-100) of `value` within `population`, using the
    fraction of the population strictly below it plus half the fraction
    exactly equal to it (a standard tie-handling convention) -- so a value
    tied with several others lands at the midpoint of that tied group
    rather than arbitrarily at the bottom or top of it. `population`
    should include `value` itself (i.e. every team's own attribute value,
    this team's included) for a rank that's interpretable as "this team's
    real position among everyone being ranked."
    """
    if not population:
        raise ValueError("population must not be empty")
    below = sum(1 for v in population if v < value)
    tied = sum(1 for v in population if v == value)
    return 100.0 * (below + 0.5 * tied) / len(population)


@dataclass
class OLRankResult:
    team: str
    mass_pctile: Optional[float]
    experience_pctile: Optional[float]
    recruiting_pctile: Optional[float]
    performance_pctile: Optional[float]
    composite_0_100: Optional[float]
    rank: Optional[int] = None
    of: Optional[int] = None


def _weighted_composite(pctiles: dict, weights: dict) -> Optional[float]:
    """Weighted average of whichever percentiles are actually available --
    matches compute_composite.py's own "score what you can, warn about the
    rest" posture rather than requiring all four inputs to be present."""
    total_weight = 0.0
    total = 0.0
    for key, pctile in pctiles.items():
        if pctile is None:
            continue
        w = weights[key]
        total += w * pctile
        total_weight += w
    if total_weight == 0.0:
        return None
    return total / total_weight


def compute_ol_rank(
    attrs: TeamOLAttributes, league: list[TeamOLAttributes], weights: dict
) -> OLRankResult:
    """`league` must include `attrs` itself -- percentile_rank ranks a
    team's own value among the full population, self included."""
    mass_pop = [a.avg_ol_weight for a in league if a.avg_ol_weight is not None]
    exp_pop = [a.returning_ol_snap_pct for a in league if a.returning_ol_snap_pct is not None]
    rec_pop = [a.avg_ol_rating for a in league if a.avg_ol_rating is not None]
    perf_pop = [a.performance_score for a in league if a.performance_score is not None]

    mass_pctile = percentile_rank(attrs.avg_ol_weight, mass_pop) if attrs.avg_ol_weight is not None and mass_pop else None
    exp_pctile = percentile_rank(attrs.returning_ol_snap_pct, exp_pop) if attrs.returning_ol_snap_pct is not None and exp_pop else None
    rec_pctile = percentile_rank(attrs.avg_ol_rating, rec_pop) if attrs.avg_ol_rating is not None and rec_pop else None
    perf_pctile = percentile_rank(attrs.performance_score, perf_pop) if attrs.performance_score is not None and perf_pop else None

    composite = _weighted_composite(
        {"mass": mass_pctile, "experience": exp_pctile, "recruiting": rec_pctile, "push": perf_pctile},
        weights,
    )

    return OLRankResult(
        team=attrs.team,
        mass_pctile=mass_pctile,
        experience_pctile=exp_pctile,
        recruiting_pctile=rec_pctile,
        performance_pctile=perf_pctile,
        composite_0_100=composite,
    )


def rank_league(results: list[OLRankResult]) -> list[OLRankResult]:
    """Assigns rank/of in place (and returns the same list, sorted
    descending by composite_0_100) -- teams with no composite (missing
    every attribute) sort last and get no rank at all, rather than a
    misleading rank among teams that couldn't actually be scored."""
    ranked = [r for r in results if r.composite_0_100 is not None]
    unranked = [r for r in results if r.composite_0_100 is None]
    ranked.sort(key=lambda r: r.composite_0_100, reverse=True)
    for i, r in enumerate(ranked, start=1):
        r.rank = i
        r.of = len(ranked)
    return ranked + unranked


def fetch_league_ol_attributes(
    year: int,
    session=None,
    browser_fetch=None,
) -> dict[str, TeamOLAttributes]:
    """One team-attribute fetch per FBS team -- Mass/Experience/Recruiting/
    Performance, whatever's available. A team missing config/rosters/
    {team}.yaml entirely (see fetch_roster.compute_mass_inputs /
    fetch_talent.compute_*_inputs -- each already warns rather than
    raising in that case) simply comes back with every field None and a
    stack of warnings, not a crashed run; rank_league sorts it last.

    `browser_fetch` is fetch_puntandrally's pluggable
    `(url, wait_for_selector=...) -> html` callable, passed through to
    each team's compute_experience_inputs() call for its year-over-year
    snap-share lookup -- pass a fetch_puntandrally.browser_session() fetch
    (see run_week.py's own use of this pattern) so all ~138 teams share
    one Chromium instance instead of each launching its own. Confirmed
    live this was the actual bottleneck: leaving this None (the previous
    behavior) meant one fresh browser launch per team for this one lookup
    alone.
    """
    fbs_teams = [t["school"] for t in fetch_cfbd.fetch_fbs_teams(year, session=session)]
    talent_table = fetch_talent.fetch_talent_table(year, session=session)

    attrs_by_team: dict[str, TeamOLAttributes] = {}
    for team in fbs_teams:
        attrs = TeamOLAttributes(team=team)

        mass = fetch_roster.compute_mass_inputs(team, year, session=session)
        attrs.avg_ol_weight = mass.avg_ol_weight
        attrs.ol_starters = mass.ol_starters
        attrs.warnings.extend(mass.warnings)

        experience = fetch_talent.compute_experience_inputs(team, year, talent_table=talent_table, session=session, browser_fetch=browser_fetch)
        attrs.returning_ol_snap_pct = experience.returning_ol_snap_pct
        attrs.warnings.extend(experience.warnings)

        recruiting = fetch_talent.compute_recruiting_talent_inputs(team)
        attrs.avg_ol_rating = recruiting.avg_ol_rating
        attrs.warnings.extend(recruiting.warnings)

        try:
            trench_stats = fetch_cfbd.fetch_team_trench_stats(team, year, session=session)
            attrs.performance_score = compute_performance_score(trench_stats.offense)
            attrs.warnings.extend(f"[performance] {w}" for w in trench_stats.offense.warnings)
        except fetch_cfbd.CFBDRequestError as exc:
            attrs.warnings.append(f"performance fetch failed: {exc}")

        try:
            attrs.direction_splits = fetch_cfbd.fetch_rushing_direction_splits(team, year, session=session)
            attrs.warnings.extend(f"[direction] {w}" for w in attrs.direction_splits.warnings)
        except fetch_cfbd.CFBDRequestError as exc:
            attrs.warnings.append(f"rushing direction splits fetch failed: {exc}")

        attrs_by_team[team] = attrs

    return attrs_by_team


OL_RANK_TABLE_FIELDNAMES = [
    "rank", "of", "team", "composite_0_100",
    "avg_ol_weight", "mass_pctile",
    "returning_ol_snap_pct", "experience_pctile",
    "avg_ol_rating", "recruiting_pctile",
    "performance_score", "performance_pctile",
    "left_success_rate", "middle_success_rate", "right_success_rate",
]

LINEMAN_STATS_FIELDNAMES = [
    "team", "name", "jersey", "position_tag", "weight_lbs", "class_year",
    "snaps_multi_year", "recruit_rating", "recruit_stars", "confidence",
]


def _direction_success_rate(splits: Optional["fetch_cfbd.RushingDirectionSplits"], direction: str) -> Optional[float]:
    """Raw, unscored data only -- see compute_performance_score's
    docstring for why this never feeds composite_0_100/performance_pctile.
    A team with no `direction_splits` fetched at all, or no resolved plays
    for this specific direction, comes back None, never a fabricated 0."""
    if splits is None:
        return None
    return getattr(splits, direction).success_rate


def ol_rank_table_rows(results: list[OLRankResult], attrs_by_team: dict[str, TeamOLAttributes]) -> list[dict]:
    rows = []
    for r in results:
        attrs = attrs_by_team[r.team]
        rows.append({
            "rank": r.rank, "of": r.of, "team": r.team, "composite_0_100": r.composite_0_100,
            "avg_ol_weight": attrs.avg_ol_weight, "mass_pctile": r.mass_pctile,
            "returning_ol_snap_pct": attrs.returning_ol_snap_pct, "experience_pctile": r.experience_pctile,
            "avg_ol_rating": attrs.avg_ol_rating, "recruiting_pctile": r.recruiting_pctile,
            "performance_score": attrs.performance_score, "performance_pctile": r.performance_pctile,
            "left_success_rate": _direction_success_rate(attrs.direction_splits, "left"),
            "middle_success_rate": _direction_success_rate(attrs.direction_splits, "middle"),
            "right_success_rate": _direction_success_rate(attrs.direction_splits, "right"),
        })
    return rows


def lineman_stats_rows(attrs_by_team: dict[str, TeamOLAttributes]) -> list[dict]:
    rows = []
    for team, attrs in attrs_by_team.items():
        for s in attrs.ol_starters:
            rows.append({
                "team": team, "name": s.name, "jersey": s.jersey, "position_tag": s.position_tag,
                "weight_lbs": s.weight_lbs, "class_year": s.class_year,
                "snaps_multi_year": s.snaps_multi_year, "recruit_rating": s.recruit_rating,
                "recruit_stars": s.recruit_stars, "confidence": s.confidence,
            })
    return rows


def write_ol_rank_table_csv(results: list[OLRankResult], attrs_by_team: dict[str, TeamOLAttributes], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=OL_RANK_TABLE_FIELDNAMES)
        writer.writeheader()
        writer.writerows(ol_rank_table_rows(results, attrs_by_team))


def write_ol_rank_table_json(results: list[OLRankResult], attrs_by_team: dict[str, TeamOLAttributes], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(ol_rank_table_rows(results, attrs_by_team), indent=2))


def write_lineman_stats_csv(attrs_by_team: dict[str, TeamOLAttributes], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=LINEMAN_STATS_FIELDNAMES)
        writer.writeheader()
        writer.writerows(lineman_stats_rows(attrs_by_team))


def write_lineman_stats_json(attrs_by_team: dict[str, TeamOLAttributes], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(lineman_stats_rows(attrs_by_team), indent=2))


if __name__ == "__main__":
    import argparse
    import yaml

    import fetch_puntandrally

    parser = argparse.ArgumentParser(description="Compute the league-wide TrenchEdge OL Rank.")
    parser.add_argument("--year", type=int, default=2026)
    parser.add_argument("--weights-file", default="config/weights.yaml")
    parser.add_argument("--out-dir", default=None, help="If set, writes ol_rank_table.csv/.json and lineman_stats.csv/.json here")
    args = parser.parse_args()

    with open(args.weights_file) as f:
        weights = yaml.safe_load(f)

    with fetch_puntandrally.browser_session() as browser_fetch:
        attrs_by_team = fetch_league_ol_attributes(args.year, browser_fetch=browser_fetch)
    league = list(attrs_by_team.values())
    results = [compute_ol_rank(a, league, weights) for a in league]
    ranked = rank_league(results)

    for r in ranked[:25]:
        print(f"#{r.rank:>3}/{r.of}  {r.team:<28} {r.composite_0_100:5.1f}")

    if args.out_dir:
        out = Path(args.out_dir)
        write_ol_rank_table_csv(ranked, attrs_by_team, out / "ol_rank_table.csv")
        write_ol_rank_table_json(ranked, attrs_by_team, out / "ol_rank_table.json")
        write_lineman_stats_csv(attrs_by_team, out / "lineman_stats.csv")
        write_lineman_stats_json(attrs_by_team, out / "lineman_stats.json")
        print(f"\nWrote ol_rank_table.csv/.json and lineman_stats.csv/.json to {out}")
