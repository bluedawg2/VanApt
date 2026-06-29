#!/usr/bin/env python
"""vanapt launcher.

  python run.py                 # start the web app (opens browser)
  python run.py --no-browser    # start without opening a browser
  python run.py --refresh       # scrape once to the DB and exit (no server)
  python run.py --port 9000     # use a different port
"""
from __future__ import annotations

import argparse
import threading
import time
import webbrowser

from vanapt import db, pipeline
from vanapt.server import serve


def main():
    ap = argparse.ArgumentParser(description="Vancouver apartment finder")
    ap.add_argument("--port", type=int, default=8777)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--refresh", action="store_true",
                    help="Run a one-off scrape to the database, then exit")
    ap.add_argument("--only", nargs="*", help="Limit to specific sources")
    args = ap.parse_args()

    db.init()

    if args.refresh:
        print("Scraping (this can take 30-90s)...")
        pipeline.refresh(only=args.only, blocking=True)
        print(_fmt(pipeline.status().get("last_summary")))
        return

    if not args.no_browser:
        def _open():
            time.sleep(1.0)
            webbrowser.open(f"http://{args.host}:{args.port}/")
        threading.Thread(target=_open, daemon=True).start()

    serve(host=args.host, port=args.port)


def _fmt(summary):
    if not summary:
        return "No summary."
    lines = [
        f"  scraped={summary['scraped']}  kept={summary['kept']}  "
        f"new={summary['new']}  updated={summary['updated']}  "
        f"dupes_collapsed={summary['duplicates_collapsed']}",
        "  sources:",
    ]
    for name, info in summary["sources"].items():
        extra = f"  ({info['error']})" if info.get("error") else ""
        lines.append(f"    - {name:12s} {info['status']:8s} "
                     f"count={info['count']}{extra}")
    return "\n".join(lines)


if __name__ == "__main__":
    main()
