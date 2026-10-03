from contextlib import closing
from datetime import UTC, datetime, timedelta

import pytest
from conftest import make_scrape

from dota_picker import db


def test_export_empty_db():
    assert db.export() is None


def test_save_and_export_roundtrip():
    db.save(make_scrape())
    data = db.export()
    assert data["period"] == "last 30 days"
    assert data["patch"] == "7.41"
    assert set(data["heroes"]) == {"axe", "lion"}
    axe = data["heroes"]["axe"]
    assert axe["positions"]["all"]["3"] == [500, 52.0]
    assert axe["positions"]["legend"]["1"] == [500, 52.0]
    assert axe["lanes"]["off"] == {"presence": 40.0, "winRate": 51.0}
    assert data["matchups"]["axe"]["lion"] == [1.5, 51.0, 10_000]
    assert db.get_image("axe") == b"jpg"


def test_latest_snapshot_is_served():
    db.save(make_scrape(win_rate=50.0))
    db.save(make_scrape(win_rate=55.0))
    assert db.export()["heroes"]["axe"]["positions"]["all"]["1"][1] == 55.0


def test_sanity_check_rejects_shrunken_scrape():
    db.save(make_scrape(heroes=("axe", "lion", "pudge", "sven", "zeus")))
    with pytest.raises(RuntimeError, match="Sanity check failed"):
        db.check_against_previous(make_scrape(heroes=("axe",)))
    db.check_against_previous(make_scrape(heroes=("axe", "lion", "pudge", "sven", "zeus")))


def test_trend_against_week_old_snapshot():
    week_ago = (datetime.now(UTC) - timedelta(days=7)).isoformat(timespec="seconds")
    db.save(make_scrape(taken_at=week_ago, win_rate=50.0))
    db.save(make_scrape(win_rate=51.5))
    data = db.export()
    assert data["trendSince"] == week_ago
    assert data["heroes"]["axe"]["trend"]["1"] == pytest.approx(1.5)


def test_prune_keeps_one_snapshot_per_old_week():
    old_monday = datetime(2026, 1, 5, tzinfo=UTC)
    for day in range(3):  # three snapshots in the same, long-gone week
        db.save(make_scrape(taken_at=(old_monday + timedelta(days=day)).isoformat()))
    db.save(make_scrape())
    with closing(db.connect()) as conn:
        taken = [r[0] for r in conn.execute("SELECT taken_at FROM snapshots ORDER BY taken_at")]
    assert taken[0] == old_monday.isoformat()
    assert len(taken) == 2


def test_player_roundtrip():
    db.save(make_scrape())
    db.save_player(
        {
            "accountId": 42,
            "name": "tester",
            "avatar": None,
            "rankTier": 54,
            "heroes": {
                "all": [{"heroId": 1, "games": 10, "wins": 6, "lastPlayed": 0}],
                "recent": [{"heroId": 2, "games": 3, "wins": 1, "lastPlayed": 0}],
            },
        }
    )
    p = db.load_player(42)
    assert p["rank"] == "legend"
    assert p["heroes"]["all"] == {"axe": {"games": 10, "wins": 6, "lastPlayed": 0}}
    assert p["heroes"]["recent"] == {"lion": {"games": 3, "wins": 1, "lastPlayed": 0}}
