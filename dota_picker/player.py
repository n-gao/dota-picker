"""Fetch a player's hero pool.

Dotabuff only shows a player's full per-hero stats to Plus subscribers, so this uses
OpenDota's public API. Account ids are the same Steam32 ids Dotabuff uses in its URLs.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime

from . import db

API = "https://api.opendota.com/api"
STEAM64_BASE = 76561197960265728
MAX_AGE_SECONDS = 24 * 3600
RECENT_DAYS = 365


class PlayerError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _resolve_vanity(name: str) -> str:
    """steamcommunity.com/id/<name> -> Steam64 id, via the profile's public XML."""
    url = f"https://steamcommunity.com/id/{urllib.parse.quote(name)}?xml=1"
    req = urllib.request.Request(url, headers={"User-Agent": "dota-picker"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            m = re.search(r"<steamID64>(\d+)</steamID64>", r.read().decode(errors="replace"))
    except urllib.error.URLError as e:
        raise PlayerError(f"Could not reach Steam: {e}", 502) from e
    if not m:
        raise PlayerError("Steam profile not found", 404)
    return m.group(1)


def parse_account_id(text: str) -> int:
    """Accept a Steam32/Steam64 id, a Dotabuff/OpenDota/Stratz/Steam profile URL."""
    text = text.strip()
    if v := re.search(r"steamcommunity\.com/id/([\w.-]+)", text):
        text = _resolve_vanity(v.group(1))
    m = re.search(r"(?:players|player|profiles)/(\d+)", text) or re.fullmatch(r"(\d+)", text)
    if not m:
        raise PlayerError("Enter a Dotabuff or Steam profile URL, or a numeric account id")
    n = int(m.group(1))
    if n >= STEAM64_BASE:
        n -= STEAM64_BASE
    if not 0 < n < 2**32:
        raise PlayerError("That doesn't look like a valid account id")
    return n


def _get(path: str):
    req = urllib.request.Request(API + path, headers={"User-Agent": "dota-picker"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        raise PlayerError(f"OpenDota returned {e.code}", 502) from e
    except urllib.error.URLError as e:
        raise PlayerError(f"Could not reach OpenDota: {e.reason}", 502) from e


def fetch_player(account_id: int) -> dict:
    player = _get(f"/players/{account_id}") or {}
    profile = player.get("profile")
    if not profile:
        raise PlayerError("Player not found, or their match data is private", 404)

    def heroes(query: str) -> list[dict]:
        return [
            {"heroId": int(h["hero_id"]), "games": h["games"], "wins": h["win"], "lastPlayed": h["last_played"]}
            for h in _get(f"/players/{account_id}/heroes{query}")
            if h["games"]
        ]

    pools = {"all": heroes(""), "recent": heroes(f"?date={RECENT_DAYS}")}
    if not pools["all"]:
        raise PlayerError("No public matches for this player (is 'Expose Public Match Data' on?)", 404)

    return {
        "accountId": account_id,
        "name": profile.get("personaname") or str(account_id),
        "avatar": profile.get("avatarmedium") or profile.get("avatar"),
        "rankTier": player.get("rank_tier"),
        "heroes": pools,
    }


def get_player(text: str, force: bool = False) -> dict:
    """Return a cached player, re-fetching if older than a day."""
    account_id = parse_account_id(text)
    cached = db.load_player(account_id)
    if cached and not force:
        age = (datetime.now(UTC) - datetime.fromisoformat(cached["fetchedAt"])).total_seconds()
        if age < MAX_AGE_SECONDS:
            return cached
    try:
        db.save_player(fetch_player(account_id))
    except PlayerError:
        if cached:  # OpenDota hiccup: serve stale data rather than nothing
            return cached
        raise
    return db.load_player(account_id)
