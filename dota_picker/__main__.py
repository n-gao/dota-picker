"""Command line interface.

dota-picker serve [--host 127.0.0.1] [--port 8000] [--no-refresh]
dota-picker scrape [--force]
"""

from __future__ import annotations

import argparse
import logging
import os

from . import __version__


def _env_flag(name: str, default: bool) -> bool:
    return os.environ.get(name, "1" if default else "0").lower() not in ("0", "false", "no", "off")


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="dota-picker", description="Counter-pick suggestions from Dotabuff stats.")
    ap.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    ap.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    sub = ap.add_subparsers(dest="command")

    serve = sub.add_parser("serve", help="run the web app (default)")
    serve.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"), help="bind address (env: HOST)")
    serve.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8000)), help="port (env: PORT)")
    serve.add_argument(
        "--no-refresh",
        dest="refresh",
        action="store_false",
        default=_env_flag("DOTA_PICKER_AUTO_REFRESH", True),
        help="don't re-scrape Dotabuff in the background (env: DOTA_PICKER_AUTO_REFRESH=0)",
    )

    scrape = sub.add_parser("scrape", help="refresh the Dotabuff cache once and exit")
    scrape.add_argument("--force", action="store_true", help="scrape even if the cache is less than a day old")

    args = ap.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if args.command == "scrape":
        from . import scraper

        scraper.refresh(force=args.force)
    else:
        from . import server

        if args.command is None:  # bare `dota-picker` = serve with defaults
            args = serve.parse_args([])
        server.serve(args.host, args.port, auto_refresh=args.refresh)


if __name__ == "__main__":
    main()
