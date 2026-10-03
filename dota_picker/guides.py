"""Item and skill builds from Dotabuff's hero guides (recent high-MMR matches).

Guides are fetched on demand per hero (a few pages), cached in SQLite for a day, and
aggregated for the position you're drafting: common starting items, core items with
typical timings, skill order and talents.
"""

from __future__ import annotations

import json
import logging
import re
import statistics
from collections import Counter
from contextlib import closing
from datetime import UTC, datetime

from bs4 import BeautifulSoup

from . import db, scraper

PAGES = 4  # 5 guides per page
MAX_AGE_SECONDS = 24 * 3600
MIN_ROLE_GUIDES = 3  # fall back to all guides if fewer match your position
CORE_SHARE = 0.4  # item in at least this share of final builds -> core
SITUATIONAL_SHARE = 0.15

# Which Dotabuff (lane, role) a position corresponds to.
POSITION_FIT = {
    1: {("safelane", "core")},
    2: {("midlane", "core")},
    3: {("offlane", "core")},
    4: {("offlane", "support"), ("roaming", "support"), ("midlane", "support")},
    5: {("safelane", "support")},
}

log = logging.getLogger("guides")

SCHEMA = """
CREATE TABLE IF NOT EXISTS hero_guides (
    slug       TEXT PRIMARY KEY,
    fetched_at TEXT NOT NULL,
    guides     TEXT NOT NULL              -- JSON list of parsed guides
);
CREATE TABLE IF NOT EXISTS assets (
    path         TEXT PRIMARY KEY,        -- e.g. items/blink-dagger.jpg
    content_type TEXT NOT NULL,
    data         BLOB NOT NULL
);
"""


def _conn():
    conn = db.connect()
    conn.executescript(SCHEMA)
    return conn


# ---------- parsing ----------


def _seconds(text: str) -> int | None:
    m = re.fullmatch(r"(-)?(\d+):(\d\d)", text.strip())
    if not m:
        return None
    secs = int(m.group(2)) * 60 + int(m.group(3))
    return -secs if m.group(1) else secs


def _item(img) -> dict:
    href = (img.find_parent("a") or {}).get("href") or ""
    slug = href.rsplit("/", 1)[-1] if href.startswith("/items/") else img["src"].rsplit("/", 1)[-1].split(".")[0]
    return {"slug": slug, "name": img.get("alt") or slug, "icon": img["src"].split("/assets/", 1)[1]}


def parse_guides(page: str) -> list[dict]:
    soup = BeautifulSoup(page, "html.parser")
    guides = []
    for start_label in soup.find_all("small", string="Starting Items"):
        sec = start_label
        while sec.parent and len(sec.parent.find_all("small", string="Starting Items")) == 1:
            sec = sec.parent
        try:
            guides.append(_parse_guide(sec))
        except Exception as e:  # one odd guide shouldn't break the rest
            log.warning("Skipping unparseable guide: %s", e)
    return guides


def _icon_suffix(icon, prefix: str) -> str | None:
    """'fa-lane-offlane' -> 'offlane' for the icon's class with the given prefix."""
    classes = icon.get("class", []) if icon else []
    return next((c[len(prefix) :] for c in classes if c.startswith(prefix)), None)


def _parse_guide(sec) -> dict:
    text = sec.get_text(" | ", strip=True)
    lane = _icon_suffix(sec.select_one("i.lane-icon"), "fa-lane-")
    role = _icon_suffix(sec.select_one("i.role-icon"), "fa-role-")
    mmr = re.search(r"~\s*([\d,]+)\s*MMR", text)
    match = sec.find("a", href=re.compile(r"^/matches/\d+"))
    player = sec.find("a", class_=re.compile("link-type-player"))
    time_el = sec.find("time")

    # Purchase timeline: groups of item icons labelled "Starting Items" or a game time.
    start, timeline = [], []
    for label in sec.find_all("small"):
        txt = label.get_text(strip=True)
        group = label.parent
        if txt == "Starting Items":
            start = [_item(i) for i in group.find_all("img", src=re.compile(r"/assets/items/"))]
        elif (t := _seconds(txt)) is not None and group.find("img", src=re.compile(r"/assets/items/")):
            timeline += [{**_item(i), "t": t} for i in group.find_all("img", src=re.compile(r"/assets/items/"))]

    # Skill build: ability/talent icons with the hero level as an overlay.
    skills = []
    for img in sec.find_all("img", src=re.compile(r"/assets/skills/")):
        level = img.find_next_sibling("div")
        if not level or not level.get_text(strip=True).isdigit():
            continue
        src = img["src"]
        talent = src.endswith("/talent.jpg")
        name = img.get("alt") or ""
        skills.append(
            {
                "level": int(level.get_text(strip=True)),
                "talent": talent,
                "name": name.removeprefix("Talent: ") if talent else name,
                "icon": None if talent else src.split("/assets/", 1)[1],
                "order": int(m.group(1)) if (m := re.search(r"-(\d+)\.jpg$", src)) else 0,
            }
        )

    # Final build: item icons after the "Opponents" hero list.
    final = []
    opp = sec.find(string=re.compile("Opponents"))
    if opp:
        for img in opp.find_all_next("img", src=re.compile(r"/assets/items/")):
            if sec not in img.parents:
                break
            final.append(_item(img))

    return {
        "matchId": int(match["href"].rsplit("/", 1)[1]) if match else None,
        "player": player.get_text(strip=True) if player else None,
        "date": time_el.get_text(strip=True) if time_el else None,
        "won": " won " in f" {text} ",
        "lane": lane,
        "role": role,
        "mmr": int(mmr.group(1).replace(",", "")) if mmr else None,
        "start": start,
        "timeline": timeline,
        "final": final,
        "skills": sorted(skills, key=lambda s: s["level"]),
    }


# ---------- fetching & caching ----------


def get_guides(slug: str) -> list[dict]:
    with closing(_conn()) as conn:
        row = conn.execute("SELECT * FROM hero_guides WHERE slug = ?", (slug,)).fetchone()
    if row:
        age = (datetime.now(UTC) - datetime.fromisoformat(row["fetched_at"])).total_seconds()
        if age < MAX_AGE_SECONDS:
            return json.loads(row["guides"])
    try:
        guides = []
        for page in range(1, PAGES + 1):
            guides += parse_guides(scraper.fetch(f"/heroes/{slug}/guides" + (f"?page={page}" if page > 1 else "")))
    except Exception:
        if row:  # Dotabuff hiccup: serve stale guides
            return json.loads(row["guides"])
        raise
    with closing(_conn()) as conn, conn:
        conn.execute("INSERT OR REPLACE INTO hero_guides VALUES (?, ?, ?)", (slug, db.now(), json.dumps(guides)))
    return guides


ASSET_RE = re.compile(r"(items|skills)/[a-z0-9_-]+\.(?:jpg|png)")


def get_asset(path: str) -> tuple[bytes, str] | None:
    """Item/skill icon from the local cache, fetched from Dotabuff the first time."""
    if not ASSET_RE.fullmatch(path):
        return None
    with closing(_conn()) as conn:
        row = conn.execute("SELECT data, content_type FROM assets WHERE path = ?", (path,)).fetchone()
    if row:
        return row["data"], row["content_type"]
    r = scraper._session().get(f"{scraper.BASE}/assets/{path}", timeout=30)
    ctype = r.headers.get("content-type", "")
    if r.status_code != 200 or not ctype.startswith("image"):
        return None
    with closing(_conn()) as conn, conn:
        conn.execute("INSERT OR REPLACE INTO assets VALUES (?, ?, ?)", (path, ctype, r.content))
    return r.content, ctype


# ---------- aggregation ----------


def build(slug: str, position: int) -> dict:
    all_guides = get_guides(slug)
    fit = POSITION_FIT.get(position, set())
    guides = [g for g in all_guides if (g["lane"], g["role"]) in fit]
    matched = len(guides) >= MIN_ROLE_GUIDES
    if not matched:
        guides = all_guides
    n = len(guides)
    if not n:
        return {"hero": slug, "position": position, "guides": 0, "matchedPosition": False}

    seen = [i for g in guides for i in g["start"] + g["timeline"] + g["final"]]
    names = {i["slug"]: i["name"] for i in seen}
    icons = {i["slug"]: i.get("icon") or f"items/{i['slug']}.jpg" for i in seen}

    def item(slug: str, share: float) -> dict:
        return {"slug": slug, "name": names.get(slug, slug), "icon": icons[slug], "share": round(share, 3)}

    # Starting items (Dotabuff lists the non-consumable ones): how often each is bought.
    start_counts = Counter(s for g in guides for s in {i["slug"] for i in g["start"]})
    starting = [item(s, c / n) for s, c in start_counts.most_common() if c / n >= SITUATIONAL_SHARE]

    # Core/situational items: share of final builds containing them, typical purchase time.
    final_counts = Counter(s for g in guides for s in {i["slug"] for i in g["final"]})
    bought = {t["slug"] for g in guides for t in g["timeline"]}
    items, neutrals = [], []
    for item_slug, count in final_counts.items():
        share = count / n
        if share < SITUATIONAL_SHARE:
            continue
        if item_slug not in bought:  # never purchased -> neutral item drop
            neutrals.append(item(item_slug, share))
            continue
        times = [
            min(t["t"] for t in g["timeline"] if t["slug"] == item_slug)
            for g in guides
            if any(t["slug"] == item_slug for t in g["timeline"])
        ]
        items.append(
            {
                **item(item_slug, share),
                "time": int(statistics.median(times)) if times else None,
                "core": share >= CORE_SHARE,
            }
        )
    items.sort(key=lambda i: (i["time"] is None, i["time"] or 0))
    neutrals.sort(key=lambda i: -i["share"])

    # Skill order: most common ability at each level; abilities in slot order.
    abilities = {}
    for g in guides:
        for s in g["skills"]:
            if not s["talent"]:
                abilities.setdefault(s["name"], {"name": s["name"], "icon": s["icon"], "order": s["order"]})
    by_level: dict[int, Counter] = {}
    for g in guides:
        for s in g["skills"]:
            by_level.setdefault(s["level"], Counter())[("talent" if s["talent"] else "ability", s["name"])] += 1
    max_level = max((lvl for lvl, c in by_level.items() if sum(c.values()) >= max(2, n // 3)), default=0)
    order = []
    for lvl in range(1, max_level + 1):
        if lvl not in by_level:
            continue
        (kind, name), count = by_level[lvl].most_common(1)[0]
        order.append({"level": lvl, "kind": kind, "name": name, "share": round(count / n, 2)})

    # Share of games taking each talent (attribute bonuses can be taken several times per game).
    talents = Counter(name for g in guides for name in {s["name"] for s in g["skills"] if s["talent"]})
    return {
        "hero": slug,
        "position": position,
        "matchedPosition": matched,
        "guides": n,
        "wins": sum(g["won"] for g in guides),
        "avgMmr": int(statistics.mean(g["mmr"] for g in guides if g["mmr"])) if any(g["mmr"] for g in guides) else None,
        "starting": starting,
        "items": items,
        "neutrals": neutrals,
        "abilities": sorted(abilities.values(), key=lambda a: a["order"]),
        "skillOrder": order,
        "talents": [{"name": t, "share": round(c / n, 2)} for t, c in talents.most_common(4)],
        "matches": [
            {k: g[k] for k in ("matchId", "player", "date", "won", "lane", "role", "mmr")}
            | {"final": [{"name": i["name"], "icon": i.get("icon") or f"items/{i['slug']}.jpg"} for i in g["final"]]}
            for g in guides
        ],
    }
