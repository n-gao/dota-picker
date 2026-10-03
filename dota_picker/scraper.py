"""Scrape hero, position, lane and matchup data from Dotabuff into the SQLite cache."""

from __future__ import annotations

import base64
import html
import json
import logging
import re
import threading
import time
import zlib
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime, timedelta

from bs4 import BeautifulSoup
from curl_cffi import requests

from . import db

BASE = "https://www.dotabuff.com"
MAX_AGE_SECONDS = 24 * 3600

# Dotabuff position slugs (the "Position" filter on /heroes).
POSITIONS = {
    1: "core-safe",
    2: "core-mid",
    3: "core-off",
    4: "support-off",
    5: "support-safe",
}
LANES = ["safe", "mid", "off", "jungle", "roaming"]
WORKERS = 4
# Right after a patch, "last 30 days" mixes in the old patch, so use patch-only data
# once the patch has a couple of days of games, until it is older than the 30-day window.
PATCH_MIN_AGE = timedelta(days=2)
PATCH_MAX_AGE = timedelta(days=30)

log = logging.getLogger("scraper")
_local = threading.local()


def _session() -> requests.Session:
    if not hasattr(_local, "session"):
        _local.session = requests.Session(impersonate="chrome")
    return _local.session


def fetch(path: str, retries: int = 4) -> str:
    url = BASE + path
    for attempt in range(1, retries + 1):
        try:
            r = _session().get(url, timeout=30)
            if r.status_code == 200 and "Just a moment" not in r.text[:2000]:
                return r.text
            log.warning("GET %s -> %s (attempt %d)", path, r.status_code, attempt)
        except Exception as e:  # network errors
            log.warning("GET %s failed: %s (attempt %d)", path, e, attempt)
        time.sleep(2 * attempt)
    raise RuntimeError(f"Could not fetch {url}")


def component_context(page: str) -> dict:
    """Decode the zlib+base64 JSON blob Dotabuff's React pages embed."""
    m = re.search(r'data-component-context="([^"]*)"', page)
    if not m:
        raise ValueError("No data-component-context on page")
    return json.loads(zlib.decompress(base64.b64decode(html.unescape(m.group(1)))))


def hero_entries(page: str) -> list[dict]:
    return component_context(page)["loaderData"]["heroesData"]["entries"]


class Window:
    """Which Dotabuff date filter to scrape with (the React and Rails pages name them differently)."""

    def __init__(self, patch: str | None = None, label: str = "last 30 days", current_patch: str | None = None):
        self.patch = patch
        self.label = label
        self.current_patch = current_patch
        self.heroes_date = patch or "1m"  # /heroes?date=
        self.legacy_date = f"patch_{patch}" if patch else "month"  # /heroes/<h>/counters, /heroes/lanes
        self.key = patch or "1m"


def current_patch() -> tuple[str | None, datetime | None]:
    """Latest patch version and its release date.

    The release date only lives in Dotabuff's JS bundle (hero page filter options), so follow
    the chunk imports to it. Falls back to the counters page's patch list (no date).
    """
    try:
        page = fetch("/heroes")
        client = fetch(re.search(r'src="(/static/client-[^"]+\.js)"', page).group(1))
        heroes_chunk = fetch("/static/" + re.search(r'"(?:\./)?(HeroesPage-[^"]+\.js)"', client).group(1))
        stats_chunk = fetch("/static/" + re.search(r'"\./(stats-[^"]+\.js)"', heroes_chunk).group(1))
        m = re.search(r'version:"([0-9][^"]*)",released_at:"([^"]+)"', stats_chunk)
        released = datetime.fromisoformat(m.group(2).replace(" ", "T")).replace(tzinfo=UTC)
        return m.group(1), released
    except Exception as e:
        log.warning("Could not read patch dates from Dotabuff JS (%s); falling back", e)
    try:
        m = re.search(r'value="patch_([^"]+)"', fetch("/heroes/axe/counters"))
        return (m.group(1) if m else None), None
    except Exception:
        return None, None


def choose_window() -> Window:
    version, released = current_patch()
    if version and released:
        age = datetime.now(UTC) - released
        log.info("Current patch %s, released %s (%d days ago)", version, released.date(), age.days)
        if PATCH_MIN_AGE <= age < PATCH_MAX_AGE:
            return Window(version, f"patch {version} only", version)
    return Window(None, "last 30 days", version)


def scrape_heroes(win: Window) -> dict[int, dict]:
    """Hero id -> {slug, name, overall stats}.

    The JSON only has hero ids; the server-rendered table lists the same heroes
    sorted by metaScore, so we zip the two and verify with the win rate text.
    """
    page = fetch(f"/heroes?date={win.heroes_date}")
    entries = sorted(hero_entries(page), key=lambda e: -e["metaScore"])
    rows = BeautifulSoup(page, "html.parser").select("table tbody tr")
    if len(rows) != len(entries):
        raise ValueError(f"Hero table has {len(rows)} rows but JSON has {len(entries)} entries")

    heroes: dict[int, dict] = {}
    for entry, row in zip(entries, rows, strict=False):
        link = row.find("a", href=re.compile(r"^/heroes/[a-z0-9-]+$"))
        cells = [td.get_text(" ", strip=True) for td in row.find_all("td")]
        shown_wr = float(cells[2].replace("%", "").strip())
        if abs(shown_wr - entry["winRate"] * 100) > 0.011:
            raise ValueError(f"Hero mapping mismatch on {cells[0]}")
        heroes[entry["heroId"]] = {
            "id": entry["heroId"],
            "slug": link["href"].rsplit("/", 1)[1],
            "name": cells[0],
            "winRate": entry["winRate"] * 100,
            "pickRate": entry["pickRate"] * 100,
            "banRate": entry.get("banRate", 0) * 100,
            "matches": entry["matches"],
            "tier": cells[1],
        }
    return heroes


def scrape_position(win: Window, pos: int, rank: str) -> dict[int, dict]:
    rank_q = "" if rank == "all" else f"&rankTier={rank}"
    page = fetch(f"/heroes?date={win.heroes_date}&position={POSITIONS[pos]}{rank_q}")
    return {e["heroId"]: {"matches": e["matches"], "winRate": e["winRate"] * 100} for e in hero_entries(page)}


def _num(td) -> float:
    return float(td["data-value"])


def scrape_lane(win: Window, lane: str) -> dict[str, dict]:
    soup = BeautifulSoup(fetch(f"/heroes/lanes?lane={lane}&date={win.legacy_date}"), "html.parser")
    out = {}
    for row in soup.select("table.sortable tbody tr"):
        tds = row.find_all("td")
        slug = tds[1].find("a")["href"].rsplit("/", 1)[1]
        out[slug] = {"presence": _num(tds[2]), "winRate": _num(tds[3])}
    return out


def scrape_counters(win: Window, slug: str) -> dict[str, list[float]]:
    """Opponent slug -> [advantage, winRate, matches] from this hero's point of view.

    Dotabuff lists the hero's *disadvantage* vs each opponent, so advantage = -disadvantage.
    """
    soup = BeautifulSoup(fetch(f"/heroes/{slug}/counters?date={win.legacy_date}"), "html.parser")
    table = next(t for t in soup.select("table.sortable") if any("Matches" in th.get_text() for th in t.find_all("th")))
    out = {}
    for row in table.select("tbody tr"):
        tds = row.find_all("td")
        opp = tds[1].find("a")["href"].rsplit("/", 1)[1]
        out[opp] = [round(-_num(tds[2]), 3), round(_num(tds[3]), 2), int(_num(tds[4]))]
    return out


def download_image(slug: str) -> bytes | None:
    r = _session().get(f"{BASE}/assets/heroes/{slug}.jpg", timeout=30)
    if r.status_code == 200 and r.headers.get("content-type", "").startswith("image"):
        return r.content
    log.warning("Image for %s -> %s", slug, r.status_code)
    return None


def scrape_all() -> dict:
    t0 = time.time()
    win = choose_window()
    log.info("Scraping %s", win.label)
    heroes = scrape_heroes(win)
    slug_to_id = {h["slug"]: hid for hid, h in heroes.items()}
    log.info("%d heroes", len(heroes))

    jobs = {}
    with ThreadPoolExecutor(WORKERS) as pool:
        for rank in ["all", *db.RANKS]:
            for pos in POSITIONS:
                jobs[pool.submit(scrape_position, win, pos, rank)] = ("position", (rank, pos))
        for lane in LANES:
            jobs[pool.submit(scrape_lane, win, lane)] = ("lane", lane)
        for h in heroes.values():
            jobs[pool.submit(scrape_counters, win, h["slug"])] = ("counters", h["slug"])

        positions, lanes, counters, failed = {}, {}, {}, []
        for i, fut in enumerate(as_completed(jobs), 1):
            kind, key = jobs[fut]
            try:
                result = fut.result()
            except Exception as e:
                log.error("%s %s failed: %s", kind, key, e)
                failed.append(f"{kind}:{key}")
                continue
            {"position": positions, "lane": lanes, "counters": counters}[kind][key] = result
            if i % 40 == 0:
                log.info("%d/%d pages", i, len(jobs))

    if any(("all", p) not in positions for p in POSITIONS) or len(counters) < 0.9 * len(heroes):
        raise RuntimeError(f"Too many failures, keeping old data: {failed}")

    # Portraits rarely change; only fetch ones we don't have yet.
    have = db.image_slugs()
    missing = [h["slug"] for h in heroes.values() if h["slug"] not in have]
    with ThreadPoolExecutor(WORKERS) as pool:
        images = {slug: img for slug, img in zip(missing, pool.map(download_image, missing), strict=False) if img}

    data = {
        "updatedAt": db.now(),
        "window": win.key,
        "period": win.label,
        "patch": win.current_patch,
        "heroes": list(heroes.values()),
        "positions": [
            (hid, rank, pos, s["matches"], round(s["winRate"], 3))
            for (rank, pos), table in positions.items()
            for hid, s in table.items()
            if hid in heroes
        ],
        "lanes": [
            (slug_to_id[slug], lane, s["presence"], s["winRate"])
            for lane, table in lanes.items()
            for slug, s in table.items()
            if slug in slug_to_id
        ],
        "matchups": [
            (slug_to_id[slug], slug_to_id[opp], *vals)
            for slug, m in counters.items()
            for opp, vals in m.items()
            if opp in slug_to_id
        ],
        "images": images,
        "failed": failed,
    }
    log.info("Scrape finished in %.0fs (%d failures)", time.time() - t0, len(failed))
    return data


def cache_age() -> float | None:
    updated = db.updated_at()
    if not updated:
        return None
    return (datetime.now(UTC) - datetime.fromisoformat(updated)).total_seconds()


def refresh(force: bool = False) -> bool:
    """Refresh the cache if it's stale. Returns True if new data was written."""
    age = cache_age()
    if not force and age is not None and age < MAX_AGE_SECONDS:
        log.info("Cache is %.1fh old, skipping", age / 3600)
        return False
    run_id = db.start_run()
    try:
        data = scrape_all()
        db.check_against_previous(data)
        sid = db.save(data)
    except Exception as e:
        db.finish_run(run_id, "error", str(e))
        raise
    detail = f"snapshot {sid}: {data['period']}, {len(data['heroes'])} heroes, {len(data['matchups'])} matchups"
    if data["failed"]:
        detail += f", {len(data['failed'])} pages failed"
    db.finish_run(run_id, "ok", detail)
    log.info("Saved to %s", db.DB_PATH)
    return True
