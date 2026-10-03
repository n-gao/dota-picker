"""Serve the picker web app and keep the Dotabuff cache fresh.

A background thread re-scrapes Dotabuff whenever the SQLite cache is older than 24h
(checked at startup and every CHECK_INTERVAL seconds).
"""

from __future__ import annotations

import json
import logging
import mimetypes
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from . import db, guides, player, scraper

WEB_DIR = Path(__file__).resolve().parent / "static"
CHECK_INTERVAL = 3600

log = logging.getLogger("server")


class Refresher:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.wake = threading.Event()
        self.force = False
        self.running = False
        self.payload: bytes | None = None  # cached /api/data response
        self.load_payload()

    def load_payload(self) -> None:
        data = db.export()
        self.payload = json.dumps(data, separators=(",", ":")).encode() if data else None

    def trigger(self) -> None:
        self.force = True
        self.wake.set()

    def loop(self) -> None:
        while True:
            force, self.force = self.force, False
            self.running = True
            try:
                if scraper.refresh(force=force):
                    self.load_payload()
            except Exception:
                log.exception("Refresh failed; will retry at next check")
            finally:
                self.running = False
            self.wake.wait(CHECK_INTERVAL)
            self.wake.clear()


refresher: Refresher


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        log.debug("%s - %s", self.address_string(), fmt % args)

    def send(self, status: int, body: bytes, ctype: str, cache: str = "no-cache") -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, obj, status: int = 200) -> None:
        self.send(status, json.dumps(obj).encode(), "application/json")

    def do_GET(self):
        url = urlsplit(self.path)
        path = url.path
        if path == "/api/player":
            query = parse_qs(url.query)
            try:
                return self.send_json(player.get_player(query.get("id", [""])[0], force="force" in query))
            except player.PlayerError as e:
                return self.send_json({"error": str(e)}, e.status)
        if path == "/api/build":
            query = parse_qs(url.query)
            hero = query.get("hero", [""])[0]
            if not re.fullmatch(r"[a-z0-9-]+", hero) or not db.get_image(hero):
                return self.send_json({"error": "Unknown hero"}, 404)
            try:
                pos = int(query.get("pos", ["1"])[0])
                return self.send_json(guides.build(hero, pos))
            except Exception as e:
                log.exception("Build for %s failed", hero)
                return self.send_json({"error": f"Could not load guides from Dotabuff: {e}"}, 502)
        if path.startswith("/asset/"):
            asset = guides.get_asset(path[len("/asset/") :])
            if asset is None:
                return self.send(404, b"", "text/plain")
            return self.send(200, asset[0], asset[1], "public, max-age=604800")
        if path == "/api/data":
            if refresher.payload is None:
                return self.send_json({"error": "No data yet, first scrape in progress"}, 503)
            return self.send(200, refresher.payload, "application/json")
        if path == "/api/status":
            return self.send_json(
                {
                    "updatedAt": db.updated_at(),
                    "refreshing": refresher.running,
                    "lastRun": db.last_run(),
                }
            )
        if m := re.fullmatch(r"/img/([a-z0-9-]+)\.jpg", path):
            img = db.get_image(m.group(1))
            if img is None:
                return self.send(404, b"", "text/plain")
            return self.send(200, img, "image/jpeg", "public, max-age=604800")

        rel = "index.html" if path == "/" else path.lstrip("/")
        file = (WEB_DIR / rel).resolve()
        if WEB_DIR in file.parents and file.is_file():
            ctype = mimetypes.guess_type(file.name)[0] or "application/octet-stream"
            return self.send(200, file.read_bytes(), ctype)
        self.send(404, b"Not found", "text/plain")

    def do_POST(self):
        if self.path == "/api/refresh":
            refresher.trigger()
            return self.send_json({"ok": True})
        self.send(404, b"Not found", "text/plain")


def serve(host: str, port: int, auto_refresh: bool = True) -> None:
    global refresher
    refresher = Refresher()
    if auto_refresh:
        threading.Thread(target=refresher.loop, daemon=True, name="refresher").start()
    else:
        log.info("Automatic refresh disabled")
    httpd = ThreadingHTTPServer((host, port), Handler)
    log.info("Dota Picker on http://%s:%d (db: %s)", host, port, db.DB_PATH)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
