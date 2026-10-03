"""SQLite storage for the scraped Dotabuff data.

Every successful scrape is kept as a snapshot; the newest one is what the app serves.
Old snapshots are thinned out (daily for 30 days, then weekly) and used for trends.
"""

from __future__ import annotations

import os
import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path

DB_PATH = Path(os.environ.get("DOTA_PICKER_DB", "data/dota.sqlite")).resolve()
RANKS = ["herald", "guardian", "crusader", "archon", "legend", "ancient", "divine", "immortal"]
KEEP_DAILY_DAYS = 30

SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    taken_at   TEXT NOT NULL,
    window     TEXT NOT NULL,             -- Dotabuff date filter used, e.g. '1m' or '7.42'
    label      TEXT NOT NULL,             -- human readable, e.g. 'last 30 days'
    patch      TEXT
);
CREATE TABLE IF NOT EXISTS heroes (
    snapshot_id INTEGER NOT NULL REFERENCES snapshots(id) ON DELETE CASCADE,
    id          INTEGER NOT NULL,
    slug        TEXT NOT NULL,
    name        TEXT NOT NULL,
    win_rate    REAL,
    pick_rate   REAL,
    ban_rate    REAL,
    matches     INTEGER,
    tier        TEXT,
    PRIMARY KEY (snapshot_id, id)
);
CREATE TABLE IF NOT EXISTS hero_positions (
    snapshot_id INTEGER NOT NULL REFERENCES snapshots(id) ON DELETE CASCADE,
    hero_id     INTEGER NOT NULL,
    rank        TEXT NOT NULL,            -- 'all' or a rank tier (herald..immortal)
    position    INTEGER NOT NULL,         -- 1..5
    matches     INTEGER NOT NULL,
    win_rate    REAL NOT NULL,
    PRIMARY KEY (snapshot_id, hero_id, rank, position)
);
CREATE TABLE IF NOT EXISTS hero_lanes (
    snapshot_id INTEGER NOT NULL REFERENCES snapshots(id) ON DELETE CASCADE,
    hero_id     INTEGER NOT NULL,
    lane        TEXT NOT NULL,            -- safe, mid, off, jungle, roaming
    presence    REAL NOT NULL,
    win_rate    REAL NOT NULL,
    PRIMARY KEY (snapshot_id, hero_id, lane)
);
-- advantage/win_rate are from hero_id's point of view against opponent_id
CREATE TABLE IF NOT EXISTS matchups (
    snapshot_id INTEGER NOT NULL REFERENCES snapshots(id) ON DELETE CASCADE,
    hero_id     INTEGER NOT NULL,
    opponent_id INTEGER NOT NULL,
    advantage   REAL NOT NULL,
    win_rate    REAL NOT NULL,
    matches     INTEGER NOT NULL,
    PRIMARY KEY (snapshot_id, hero_id, opponent_id)
);
CREATE TABLE IF NOT EXISTS hero_images (
    slug  TEXT PRIMARY KEY,
    image BLOB NOT NULL
);
-- player hero pools (from OpenDota; Dotabuff's player hero stats are Plus-only)
CREATE TABLE IF NOT EXISTS players (
    account_id INTEGER PRIMARY KEY,
    name       TEXT,
    avatar     TEXT,
    fetched_at TEXT NOT NULL,
    rank_tier  INTEGER
);
CREATE TABLE IF NOT EXISTS player_heroes (
    account_id  INTEGER NOT NULL REFERENCES players(account_id),
    period      TEXT NOT NULL,            -- 'all' (all time) or 'recent' (last 365 days)
    hero_id     INTEGER NOT NULL,
    games       INTEGER NOT NULL,
    wins        INTEGER NOT NULL,
    last_played INTEGER,
    PRIMARY KEY (account_id, period, hero_id)
);
CREATE TABLE IF NOT EXISTS scrape_runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    status      TEXT NOT NULL,            -- running, ok, error
    detail      TEXT
);
"""


def _migrate(conn: sqlite3.Connection) -> None:
    """Upgrade databases created before snapshots existed (their data is simply re-scraped)."""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(heroes)")}
    if cols and "snapshot_id" not in cols:
        for table in ("matchups", "hero_lanes", "hero_positions", "heroes", "meta"):
            conn.execute(f"DROP TABLE IF EXISTS {table}")
    ph_cols = {r[1] for r in conn.execute("PRAGMA table_info(player_heroes)")}
    if ph_cols and "period" not in ph_cols:  # pre-period schema: drop cached players, they get refetched
        conn.execute("DROP TABLE player_heroes")
        conn.execute("DROP TABLE IF EXISTS players")
    player_cols = {r[1] for r in conn.execute("PRAGMA table_info(players)")}
    if player_cols and "rank_tier" not in player_cols:
        conn.execute("ALTER TABLE players ADD COLUMN rank_tier INTEGER")


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    _migrate(conn)
    conn.executescript(SCHEMA)
    return conn


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


# ---------- scrape runs ----------


def start_run() -> int:
    with closing(connect()) as conn, conn:
        return conn.execute("INSERT INTO scrape_runs (started_at, status) VALUES (?, 'running')", (now(),)).lastrowid


def finish_run(run_id: int, status: str, detail: str = "") -> None:
    with closing(connect()) as conn, conn:
        conn.execute(
            "UPDATE scrape_runs SET finished_at = ?, status = ?, detail = ? WHERE id = ?",
            (now(), status, detail, run_id),
        )


def last_run() -> dict | None:
    with closing(connect()) as conn:
        row = conn.execute("SELECT * FROM scrape_runs ORDER BY id DESC LIMIT 1").fetchone()
        return dict(row) if row else None


# ---------- snapshots ----------


def latest_snapshot(conn: sqlite3.Connection | None = None) -> dict | None:
    def q(c):
        row = c.execute("SELECT * FROM snapshots ORDER BY id DESC LIMIT 1").fetchone()
        return dict(row) if row else None

    if conn is not None:
        return q(conn)
    with closing(connect()) as c:
        return q(c)


def snapshot_counts(conn: sqlite3.Connection, snapshot_id: int) -> dict:
    return {
        t: conn.execute(f"SELECT COUNT(*) FROM {t} WHERE snapshot_id = ?", (snapshot_id,)).fetchone()[0]
        for t in ("heroes", "hero_positions", "matchups")
    }


def check_against_previous(data: dict) -> None:
    """Refuse a scrape that looks broken compared to the last good one (e.g. Dotabuff changed its markup)."""
    with closing(connect()) as conn:
        prev = latest_snapshot(conn)
        if not prev:
            return
        before = snapshot_counts(conn, prev["id"])
    after = {"heroes": len(data["heroes"]), "hero_positions": len(data["positions"]), "matchups": len(data["matchups"])}
    for table, n in after.items():
        if n < 0.9 * before[table]:
            raise RuntimeError(f"Sanity check failed: {table} has {n} rows, previous snapshot had {before[table]}")


def save(data: dict) -> int:
    """Store a fresh scrape as a new snapshot, atomically. Returns the snapshot id."""
    with closing(connect()) as conn, conn:
        sid = conn.execute(
            "INSERT INTO snapshots (taken_at, window, label, patch) VALUES (?, ?, ?, ?)",
            (data["updatedAt"], data["window"], data["period"], data.get("patch")),
        ).lastrowid
        conn.executemany(
            "INSERT INTO heroes VALUES (:sid, :id, :slug, :name, :winRate, :pickRate, :banRate, :matches, :tier)",
            [{**h, "sid": sid} for h in data["heroes"]],
        )
        conn.executemany("INSERT INTO hero_positions VALUES (?, ?, ?, ?, ?, ?)", [(sid, *r) for r in data["positions"]])
        conn.executemany("INSERT INTO hero_lanes VALUES (?, ?, ?, ?, ?)", [(sid, *r) for r in data["lanes"]])
        conn.executemany("INSERT INTO matchups VALUES (?, ?, ?, ?, ?, ?)", [(sid, *r) for r in data["matchups"]])
        conn.executemany("INSERT OR REPLACE INTO hero_images VALUES (?, ?)", data["images"].items())
        prune(conn)
        return sid


def prune(conn: sqlite3.Connection) -> None:
    """Keep every snapshot from the last KEEP_DAILY_DAYS days, and the first of each week before that."""
    cutoff = datetime.now(UTC) - timedelta(days=KEEP_DAILY_DAYS)
    seen_weeks = set()
    for row in conn.execute("SELECT id, taken_at FROM snapshots ORDER BY id").fetchall():
        taken = datetime.fromisoformat(row["taken_at"])
        if taken >= cutoff:
            continue
        week = taken.isocalendar()[:2]
        if week in seen_weeks:
            conn.execute("DELETE FROM snapshots WHERE id = ?", (row["id"],))
        seen_weeks.add(week)


def updated_at() -> str | None:
    snap = latest_snapshot()
    return snap["taken_at"] if snap else None


# ---------- images ----------


def image_slugs() -> set[str]:
    with closing(connect()) as conn:
        return {r[0] for r in conn.execute("SELECT slug FROM hero_images")}


def get_image(slug: str) -> bytes | None:
    with closing(connect()) as conn:
        row = conn.execute("SELECT image FROM hero_images WHERE slug = ?", (slug,)).fetchone()
        return row[0] if row else None


# ---------- export for the web app ----------


def _trend_snapshot(conn: sqlite3.Connection, current: dict) -> dict | None:
    """The snapshot closest to a week before `current`, using the same date window."""
    target = datetime.fromisoformat(current["taken_at"]) - timedelta(days=7)
    rows = conn.execute(
        "SELECT * FROM snapshots WHERE id != ? AND window = ? AND taken_at <= ?",
        (current["id"], current["window"], (target + timedelta(days=1)).isoformat()),
    ).fetchall()
    if not rows:
        return None
    return dict(min(rows, key=lambda r: abs(datetime.fromisoformat(r["taken_at"]) - target)))


def export() -> dict | None:
    """Everything the web app needs, as one compact JSON-able dict."""
    with closing(connect()) as conn:
        snap = latest_snapshot(conn)
        if not snap:
            return None
        sid = snap["id"]
        heroes = {r["id"]: dict(r) for r in conn.execute("SELECT * FROM heroes WHERE snapshot_id = ?", (sid,))}
        slug = {i: h["slug"] for i, h in heroes.items()}
        out = {
            h["slug"]: {
                "id": h["id"],
                "slug": h["slug"],
                "name": h["name"],
                "tier": h["tier"],
                "winRate": h["win_rate"],
                "pickRate": h["pick_rate"],
                "banRate": h["ban_rate"],
                "matches": h["matches"],
                "positions": {},
                "lanes": {},
                "trend": {},
            }
            for h in heroes.values()
        }
        # positions[rank][pos] = [matches, winRate]
        for r in conn.execute("SELECT * FROM hero_positions WHERE snapshot_id = ?", (sid,)):
            if r["hero_id"] in slug:
                out[slug[r["hero_id"]]]["positions"].setdefault(r["rank"], {})[str(r["position"])] = [
                    r["matches"],
                    r["win_rate"],
                ]
        for r in conn.execute("SELECT * FROM hero_lanes WHERE snapshot_id = ?", (sid,)):
            if r["hero_id"] in slug:
                out[slug[r["hero_id"]]]["lanes"][r["lane"]] = {"presence": r["presence"], "winRate": r["win_rate"]}

        trend_snap = _trend_snapshot(conn, snap)
        if trend_snap:
            for r in conn.execute(
                "SELECT hero_id, position, win_rate FROM hero_positions WHERE snapshot_id = ? AND rank = 'all'",
                (trend_snap["id"],),
            ):
                h = out.get(slug.get(r["hero_id"], ""))
                cur = h and h["positions"].get("all", {}).get(str(r["position"]))
                if cur:
                    h["trend"][str(r["position"])] = round(cur[1] - r["win_rate"], 3)

        matchups: dict[str, dict] = {}
        for r in conn.execute("SELECT * FROM matchups WHERE snapshot_id = ?", (sid,)):
            if r["hero_id"] in slug and r["opponent_id"] in slug:
                matchups.setdefault(slug[r["hero_id"]], {})[slug[r["opponent_id"]]] = [
                    r["advantage"],
                    r["win_rate"],
                    r["matches"],
                ]
        return {
            "updatedAt": snap["taken_at"],
            "period": snap["label"],
            "patch": snap["patch"],
            "ranks": RANKS,
            "trendSince": trend_snap["taken_at"] if trend_snap else None,
            "snapshots": conn.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0],
            "heroes": out,
            "matchups": matchups,
        }


# ---------- players ----------


def save_player(player: dict) -> None:
    with closing(connect()) as conn, conn:
        conn.execute(
            "INSERT OR REPLACE INTO players (account_id, name, avatar, fetched_at, rank_tier) VALUES (?, ?, ?, ?, ?)",
            (player["accountId"], player["name"], player["avatar"], now(), player.get("rankTier")),
        )
        conn.execute("DELETE FROM player_heroes WHERE account_id = ?", (player["accountId"],))
        conn.executemany(
            "INSERT INTO player_heroes VALUES (?, ?, ?, ?, ?, ?)",
            [
                (player["accountId"], period, h["heroId"], h["games"], h["wins"], h["lastPlayed"])
                for period, heroes in player["heroes"].items()
                for h in heroes
            ],
        )


def load_player(account_id: int) -> dict | None:
    with closing(connect()) as conn:
        p = conn.execute("SELECT * FROM players WHERE account_id = ?", (account_id,)).fetchone()
        if not p:
            return None
        snap = latest_snapshot(conn)
        heroes = {"all": {}, "recent": {}}  # period -> slug -> stats
        if snap:
            for r in conn.execute(
                """SELECT ph.period, h.slug, ph.games, ph.wins, ph.last_played FROM player_heroes ph
                   JOIN heroes h ON h.id = ph.hero_id AND h.snapshot_id = ?
                   WHERE ph.account_id = ?""",
                (snap["id"], account_id),
            ):
                heroes.setdefault(r["period"], {})[r["slug"]] = {
                    "games": r["games"],
                    "wins": r["wins"],
                    "lastPlayed": r["last_played"],
                }
        rank_tier = p["rank_tier"]
        return {
            "accountId": p["account_id"],
            "name": p["name"],
            "avatar": p["avatar"],
            "fetchedAt": p["fetched_at"],
            "heroes": heroes,
            "rankTier": rank_tier,
            "rank": RANKS[rank_tier // 10 - 1] if rank_tier and 1 <= rank_tier // 10 <= len(RANKS) else None,
        }
