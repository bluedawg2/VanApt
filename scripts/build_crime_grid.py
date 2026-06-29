"""Offline builder for the block-level crime grid shipped at vanapt/crime_grid.json.

Run once (not at runtime, not on Render). Reads the VPD GeoDASH open-data CSV
(TYPE,YEAR,MONTH,DAY,HOUR,MINUTE,HUNDRED_BLOCK,NEIGHBOURHOOD,X,Y where X/Y are
UTM zone 10N metres), keeps a recent multi-year window, converts to lat/lng,
weights incidents toward personal-safety crime, bins them into ~150 m cells,
and stores each cell's crime *percentile* (0 = quietest, 100 = worst East-Van
block). The runtime (vanapt/safety.py) bins a listing's lat/lng the same way
and turns the percentile into a location deduction — no data dependency or
network call ships to production.

Usage:  python scripts/build_crime_grid.py file1.csv [file2.csv ...]
        (pass the recent YTD export and the historical all-years export; rows
         are merged and filtered to the YEARS window below.)

Requires pyproj (only here, for the one-time UTM->WGS84 conversion).

Data: Vancouver Police Department, GeoDASH Open Data (https://geodash.vpd.ca/opendata/).
Person-offence locations are deliberately offset by VPD, so this is a
block/neighbourhood-level signal, not pinpoint.
"""
from __future__ import annotations

import csv
import json
import math
import os
import sys

from pyproj import Transformer

# --- knobs ------------------------------------------------------------------
# Recent ~18-month window: full-year 2025 + 2026 year-to-date. Current and
# dense enough for a stable per-block ranking.
YEARS = {"2025", "2026"}
CELL_M = 150.0                                    # grid cell size (metres)
# Personal-safety-first weighting: crimes that bear on walking home / being
# around the building count most; property/vehicle crime counts least.
WEIGHTS = {
    "Homicide": 10.0,
    "Offence Against a Person": 5.0,
    "Vehicle Collision or Pedestrian Struck (with Fatality)": 1.5,
    "Break and Enter Residential/Other": 1.5,
    "Other Theft": 1.0,        # includes theft-from-person / shoplifting
    "Mischief": 0.7,           # street-disorder proxy
    "Break and Enter Commercial": 0.6,
    "Theft from Vehicle": 0.5,
    "Theft of Vehicle": 0.5,
    "Theft of Bicycle": 0.4,
    "Vehicle Collision or Pedestrian Struck (with Injury)": 0.3,
}
DEFAULT_WEIGHT = 0.5

# East Van coverage box (+small margin). Burnaby has no VPD data and falls back
# to the heuristic model at runtime, so we don't grid it.
BOX = dict(min_lat=49.19, max_lat=49.31, min_lng=-123.135, max_lng=-123.00)

# Cell size in degrees at Vancouver's latitude (~49.25°). Runtime MUST match.
DLAT = CELL_M / 111_320.0
DLNG = CELL_M / (111_320.0 * math.cos(math.radians(49.25)))

OUT = os.path.join(os.path.dirname(__file__), "..", "vanapt", "crime_grid.json")


def main(csv_paths: list[str]) -> None:
    tf = Transformer.from_crs("epsg:32610", "epsg:4326", always_xy=True)
    cells: dict[str, float] = {}
    kept = skipped = 0
    used_years: set[str] = set()

    # Convert in batches for speed.
    batch_x: list[float] = []
    batch_y: list[float] = []
    batch_w: list[float] = []

    def flush():
        if not batch_x:
            return
        lngs, lats = tf.transform(batch_x, batch_y)
        for lat, lng, w in zip(lats, lngs, batch_w):
            if not (BOX["min_lat"] <= lat <= BOX["max_lat"]
                    and BOX["min_lng"] <= lng <= BOX["max_lng"]):
                continue
            i = math.floor(lat / DLAT)
            j = math.floor(lng / DLNG)
            cells[f"{i},{j}"] = cells.get(f"{i},{j}", 0.0) + w
        batch_x.clear(); batch_y.clear(); batch_w.clear()

    for csv_path in csv_paths:
        with open(csv_path, newline="", encoding="utf-8", errors="replace") as f:
            for row in csv.DictReader(f):
                if row.get("YEAR") not in YEARS:
                    skipped += 1
                    continue
                try:
                    x = float(row["X"]); y = float(row["Y"])
                except (TypeError, ValueError):
                    skipped += 1
                    continue
                if x == 0.0 or y == 0.0:   # VPD uses 0,0 for un-geocoded incidents
                    skipped += 1
                    continue
                used_years.add(row["YEAR"])
                batch_x.append(x); batch_y.append(y)
                batch_w.append(WEIGHTS.get(row["TYPE"], DEFAULT_WEIGHT))
                kept += 1
                if len(batch_x) >= 50_000:
                    flush()
    flush()

    # Convert weighted density -> percentile rank (0..100). Percentile is robust
    # to absolute scale and reads naturally ("top 5% of blocks for crime").
    vals = sorted(cells.values())
    n = len(vals)

    def pct(v: float) -> int:
        # fraction of cells with density <= v
        lo, hi = 0, n
        while lo < hi:
            mid = (lo + hi) // 2
            if vals[mid] <= v:
                lo = mid + 1
            else:
                hi = mid
        return round(100 * lo / n)

    grid = {k: pct(v) for k, v in cells.items()}

    payload = {
        "meta": {
            "source": "Vancouver Police Department GeoDASH Open Data",
            "url": "https://geodash.vpd.ca/opendata/",
            "years": sorted(used_years),
            "cell_m": CELL_M,
            "dlat": DLAT,
            "dlng": DLNG,
            "weights": WEIGHTS,
            "incidents_used": kept,
            "cells": n,
            "note": ("Percentile of personal-safety-weighted crime density per "
                     "~150m block among East Vancouver cells. 100 = worst. "
                     "Person-offence locations are offset by VPD, so this is a "
                     "block/neighbourhood signal, not pinpoint."),
        },
        "grid": grid,
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(payload, f, separators=(",", ":"))
    print(f"kept {kept:,} incidents ({skipped:,} skipped), {n:,} cells")
    print(f"wrote {os.path.normpath(OUT)} ({os.path.getsize(OUT):,} bytes)")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("usage: python scripts/build_crime_grid.py <csv> [<csv> ...]")
    main(sys.argv[1:])
