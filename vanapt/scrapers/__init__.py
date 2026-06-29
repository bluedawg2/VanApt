"""Scraper registry + runner. Each scraper module exposes scrape() -> [Listing].
Failures are isolated per-source so one blocked site never breaks a refresh."""
from __future__ import annotations

import time
import traceback

from .. import config
from . import craigslist, kijiji, rentals_ca, zumper

REGISTRY = {
    "craigslist": craigslist.scrape,
    "kijiji": kijiji.scrape,
    "zumper": zumper.scrape,
    "rentals_ca": rentals_ca.scrape,
}


def run_all(only: list[str] | None = None) -> dict:
    """Run enabled scrapers, return per-source results + any errors."""
    results = {"listings": [], "sources": {}}
    for name, fn in REGISTRY.items():
        if only and name not in only:
            continue
        if not only and not config.ENABLED_SOURCES.get(name, False):
            results["sources"][name] = {"status": "disabled", "count": 0}
            continue
        t0 = time.time()
        try:
            items = fn()
            results["listings"].extend(items)
            results["sources"][name] = {
                "status": "ok",
                "count": len(items),
                "seconds": round(time.time() - t0, 1),
            }
        except Exception as e:
            results["sources"][name] = {
                "status": "error",
                "count": 0,
                "error": str(e)[:300],
                "seconds": round(time.time() - t0, 1),
            }
            traceback.print_exc()
    return results
