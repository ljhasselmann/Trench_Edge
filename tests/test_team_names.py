import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from team_names import build_canonical_alias_map


SAMPLE_TEAMS = [
    {"school": "Miami", "alternateNames": ["Miami (FL)", "MIA", "Miami"]},
    {"school": "Miami (OH)", "alternateNames": ["M-OH", "Miami OH"]},
]


def test_build_canonical_alias_map_includes_school_itself():
    alias_map = build_canonical_alias_map(SAMPLE_TEAMS)
    assert alias_map["miami"] == "Miami"
    assert alias_map["miami (oh)"] == "Miami (OH)"


def test_build_canonical_alias_map_includes_alternate_names():
    alias_map = build_canonical_alias_map(SAMPLE_TEAMS)
    assert alias_map["miami (fl)"] == "Miami"
    assert alias_map["mia"] == "Miami"
    assert alias_map["m-oh"] == "Miami (OH)"
    assert alias_map["miami oh"] == "Miami (OH)"


def test_build_canonical_alias_map_handles_missing_alternate_names_key():
    alias_map = build_canonical_alias_map([{"school": "Georgia"}])
    assert alias_map == {"georgia": "Georgia"}
