import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import fetch_matchups


class _FakeResponse:
    def __init__(self, status_code, json_body, text=""):
        self.status_code = status_code
        self._json_body = json_body
        self.text = text or str(json_body)

    def json(self):
        return self._json_body


class _FakeSession:
    """Stands in for requests.Session; routes by URL suffix since this
    module hits two different endpoints."""

    def __init__(self, games_response=None, rankings_response=None):
        self._games_response = games_response
        self._rankings_response = rankings_response
        self.calls = []

    def get(self, url, params, headers, timeout):
        self.calls.append({"url": url, "params": params})
        if url.endswith("/games"):
            return self._games_response
        if url.endswith("/rankings"):
            return self._rankings_response
        raise AssertionError(f"unexpected URL: {url}")


SAMPLE_GAMES = [
    {"id": 1, "homeTeam": "Wake Forest", "awayTeam": "Miami"},  # Miami ranked
    {"id": 2, "homeTeam": "Georgia", "awayTeam": "Arkansas"},  # Georgia ranked
    {"id": 3, "homeTeam": "Vanderbilt", "awayTeam": "Kentucky"},  # neither ranked
    {"id": 4, "homeTeam": "Texas", "awayTeam": "Ohio State"},  # both ranked
    {"id": 5, "homeTeam": None, "awayTeam": "Some FCS Team"},  # malformed/bye -- skip
]

SAMPLE_RANKINGS = [
    {
        "season": 2026, "week": 3,
        "polls": [
            {
                "poll": "Coaches Poll",
                "ranks": [{"rank": 1, "school": "Texas"}, {"rank": 2, "school": "Georgia"}],
            },
            {
                "poll": "AP Top 25",
                "ranks": [
                    {"rank": 1, "school": "Texas"},
                    {"rank": 2, "school": "Georgia"},
                    {"rank": 5, "school": "Miami"},
                    {"rank": 6, "school": "Ohio State"},
                ],
            },
        ],
    }
]


def test_fetch_fbs_schedule_uses_classification_not_division(monkeypatch):
    monkeypatch.setenv("CFBD_API_KEY", "k")
    session = _FakeSession(games_response=_FakeResponse(200, SAMPLE_GAMES))
    fetch_matchups.fetch_fbs_schedule(2026, 3, session=session)
    assert session.calls[0]["params"]["classification"] == "fbs"
    assert "division" not in session.calls[0]["params"]


def test_fetch_ap_top25_extracts_only_the_ap_poll(monkeypatch):
    monkeypatch.setenv("CFBD_API_KEY", "k")
    session = _FakeSession(rankings_response=_FakeResponse(200, SAMPLE_RANKINGS))
    top25 = fetch_matchups.fetch_ap_top25(2026, 3, session=session)
    assert top25 == {"Texas", "Georgia", "Miami", "Ohio State"}


def test_fetch_ap_top25_returns_empty_set_when_poll_absent(monkeypatch):
    monkeypatch.setenv("CFBD_API_KEY", "k")
    session = _FakeSession(rankings_response=_FakeResponse(200, [{"season": 2026, "week": 0, "polls": []}]))
    top25 = fetch_matchups.fetch_ap_top25(2026, 0, session=session)
    assert top25 == set()


def test_derive_matchups_filters_to_top25_involving_games():
    top25 = {"Texas", "Georgia", "Miami", "Ohio State"}
    matchups = fetch_matchups.derive_matchups(SAMPLE_GAMES, top25, 2026, 3)

    labels = {m["label"] for m in matchups}
    assert len(matchups) == 3  # games 1, 2, 4 -- not game 3 (unranked), not game 5 (malformed)
    assert any("miami" in l for l in labels)
    assert any("georgia" in l or "arkansas" in l for l in labels)


def test_derive_matchups_team_a_is_away_team_b_is_home():
    top25 = {"Miami"}
    matchups = fetch_matchups.derive_matchups(SAMPLE_GAMES, top25, 2026, 3)
    game1 = next(m for m in matchups if m["team_a"] == "Miami" or m["team_b"] == "Miami")
    assert game1["team_a"] == "Miami"  # awayTeam in SAMPLE_GAMES
    assert game1["team_b"] == "Wake Forest"  # homeTeam


def test_derive_matchups_sets_side_and_week():
    matchups = fetch_matchups.derive_matchups(SAMPLE_GAMES, {"Miami"}, 2026, 3)
    m = matchups[0]
    assert m["side"] == "team_a_ol_vs_team_b_dl"
    assert m["week"] == 3


def test_derive_matchups_skips_malformed_games_without_crashing():
    matchups = fetch_matchups.derive_matchups(SAMPLE_GAMES, {"Some FCS Team"}, 2026, 3)
    assert matchups == []  # game 5 has homeTeam=None, must not crash or be included


def test_derive_matchups_excludes_unranked_games():
    top25 = {"Miami"}
    matchups = fetch_matchups.derive_matchups(SAMPLE_GAMES, top25, 2026, 3)
    labels = {m["label"] for m in matchups}
    assert not any("vanderbilt" in l or "kentucky" in l for l in labels)


def test_derive_matchups_both_ranked_game_included_once():
    top25 = {"Texas", "Ohio State"}
    matchups = fetch_matchups.derive_matchups(SAMPLE_GAMES, top25, 2026, 3)
    matching = [m for m in matchups if {m["team_a"], m["team_b"]} == {"Texas", "Ohio State"}]
    assert len(matching) == 1


def test_discover_matchups_composes_schedule_and_rankings(monkeypatch):
    monkeypatch.setenv("CFBD_API_KEY", "k")
    session = _FakeSession(
        games_response=_FakeResponse(200, SAMPLE_GAMES),
        rankings_response=_FakeResponse(200, SAMPLE_RANKINGS),
    )
    matchups = fetch_matchups.discover_matchups(2026, 3, session=session)
    assert len(matchups) == 3
    assert all(m["week"] == 3 for m in matchups)


def test_slugify_handles_special_characters():
    assert fetch_matchups._slugify("Miami (OH)") == "miami-oh"
    assert fetch_matchups._slugify("Texas A&M") == "texas-a-m"
    assert fetch_matchups._slugify("Wake Forest") == "wake-forest"
