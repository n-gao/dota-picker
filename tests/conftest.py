import pytest

from dota_picker import db


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    """Every test gets its own empty SQLite database."""
    path = tmp_path / "test.sqlite"
    monkeypatch.setattr(db, "DB_PATH", path)
    return path


def make_scrape(heroes=("axe", "lion"), taken_at=None, window="1m", win_rate=52.0):
    """A minimal scrape result in the shape scraper.scrape_all() returns."""
    ids = {slug: i + 1 for i, slug in enumerate(heroes)}
    return {
        "updatedAt": taken_at or db.now(),
        "window": window,
        "period": "last 30 days",
        "patch": "7.41",
        "heroes": [
            {
                "id": hid,
                "slug": slug,
                "name": slug.title(),
                "winRate": 50.0,
                "pickRate": 10.0,
                "banRate": 1.0,
                "matches": 1000,
                "tier": "A",
            }
            for slug, hid in ids.items()
        ],
        "positions": [
            (hid, rank, pos, 500, win_rate) for hid in ids.values() for rank in ("all", "legend") for pos in (1, 3)
        ],
        "lanes": [(hid, "off", 40.0, 51.0) for hid in ids.values()],
        "matchups": [(a, b, 1.5, 51.0, 10_000) for a in ids.values() for b in ids.values() if a != b],
        "images": {slug: b"jpg" for slug in ids},
        "failed": [],
    }
