"""SQLite storage. Listings are upserted on each refresh; user state
(favorite/discarded) and dedup grouping are preserved across refreshes."""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from typing import Iterable, Optional

from .models import Listing

# Data dir is overridable so a hosted deployment can point it at a mounted
# persistent disk (set VANAPT_DATA_DIR) shared by the web + refresh processes.
_DATA_DIR = os.environ.get("VANAPT_DATA_DIR") or os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "data")
_DB_PATH = os.path.join(_DATA_DIR, "listings.db")
_lock = threading.Lock()


def _conn() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(_DB_PATH), exist_ok=True)
    c = sqlite3.connect(_DB_PATH, timeout=30)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    return c


SCHEMA = """
CREATE TABLE IF NOT EXISTS listings (
    uid           TEXT PRIMARY KEY,
    source        TEXT,
    source_id     TEXT,
    url           TEXT,
    title         TEXT,
    description   TEXT,
    price         INTEGER,
    bedrooms      REAL,
    bathrooms     REAL,
    sqft          INTEGER,
    listing_type  TEXT,
    area          TEXT,
    neighborhood  TEXT,
    address       TEXT,
    lat           REAL,
    lng           REAL,
    image_url     TEXT,
    available_date TEXT,
    posted_at     TEXT,
    contact       TEXT,
    furnished     INTEGER,             -- 1 = furnished, 0/NULL = not stated
    parking       INTEGER,             -- 1 = included, 0 = none, NULL = unknown
    laundry       TEXT,                -- 'in_suite' | 'shared' | NULL
    lease_term    TEXT,                -- 'month-to-month' | '<n>-month' | '1-year' | ''
    safety        INTEGER,             -- 0-100 heuristic vetting score (NULL = not yet scored)
    safety_flags  TEXT,                -- JSON array of {text, kind} reasons
    crime_pct     INTEGER,             -- VPD block crime percentile 0-100 (NULL = outside grid)
    best          INTEGER,             -- 0-100 composite "best match" ranking score
    fingerprint   TEXT,
    dedup_group   TEXT,
    is_primary    INTEGER DEFAULT 1,   -- 1 = shown, 0 = collapsed duplicate
    status        TEXT DEFAULT 'new',  -- new | favorite | discarded
    first_seen    REAL,
    last_seen     REAL
);
CREATE INDEX IF NOT EXISTS idx_area ON listings(area);
CREATE INDEX IF NOT EXISTS idx_price ON listings(price);
CREATE INDEX IF NOT EXISTS idx_group ON listings(dedup_group);

CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""


_MIGRATION_COLUMNS = {  # name -> SQL type, added to old DBs that predate them
    "furnished": "INTEGER", "parking": "INTEGER",
    "laundry": "TEXT", "lease_term": "TEXT",
    "safety": "INTEGER", "safety_flags": "TEXT", "crime_pct": "INTEGER",
    "best": "INTEGER",
}


def init() -> None:
    with _lock, _conn() as c:
        c.executescript(SCHEMA)
        have = {r["name"] for r in c.execute("PRAGMA table_info(listings)")}
        for col, typ in _MIGRATION_COLUMNS.items():
            if col not in have:
                c.execute(f"ALTER TABLE listings ADD COLUMN {col} {typ}")


def upsert_listings(items: Iterable[Listing]) -> dict:
    """Insert new listings, refresh existing ones (keep user status). Returns
    {'new': n, 'updated': n}."""
    now = time.time()
    new = updated = 0
    with _lock, _conn() as c:
        for li in items:
            uid = li.uid()
            fp = li.dedup_fingerprint()
            row = c.execute("SELECT uid FROM listings WHERE uid=?", (uid,)).fetchone()
            if row:
                c.execute(
                    """UPDATE listings SET url=?, title=?, description=?, price=?,
                       bedrooms=?, bathrooms=?, sqft=?, listing_type=?, area=?,
                       neighborhood=?, address=?, lat=?, lng=?, image_url=?,
                       available_date=?, posted_at=?, contact=?, fingerprint=?,
                       last_seen=? WHERE uid=?""",
                    (li.url, li.title, li.description, li.price, li.bedrooms,
                     li.bathrooms, li.sqft, li.listing_type, li.area,
                     li.neighborhood, li.address, li.lat, li.lng, li.image_url,
                     li.available_date, li.posted_at, li.contact, fp, now, uid),
                )
                updated += 1
            else:
                c.execute(
                    """INSERT INTO listings (uid, source, source_id, url, title,
                       description, price, bedrooms, bathrooms, sqft, listing_type,
                       area, neighborhood, address, lat, lng, image_url,
                       available_date, posted_at, contact, fingerprint, dedup_group,
                       is_primary, status, first_seen, last_seen)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,'new',?,?)""",
                    (uid, li.source, li.source_id, li.url, li.title, li.description,
                     li.price, li.bedrooms, li.bathrooms, li.sqft, li.listing_type,
                     li.area, li.neighborhood, li.address, li.lat, li.lng,
                     li.image_url, li.available_date, li.posted_at, li.contact,
                     fp, uid, now, now),
                )
                new += 1
    return {"new": new, "updated": updated}


def all_rows() -> list[sqlite3.Row]:
    with _lock, _conn() as c:
        return c.execute("SELECT * FROM listings").fetchall()


_BACKUP_COLS = ("url", "title", "description", "price", "bedrooms", "bathrooms",
                "sqft", "listing_type", "neighborhood", "address", "area",
                "image_url", "contact", "available_date", "source", "lat", "lng",
                "posted_at", "status")


def manual_backup_payloads(scraped_sources) -> list[dict]:
    """Every hand-entered / Facebook row (anything not produced by a scraper),
    shaped as import payloads so they can be re-imported verbatim later. Status
    (favorite/discarded) is preserved so her decisions survive too."""
    if not scraped_sources:
        scraped_sources = ("",)
    ph = ",".join("?" * len(scraped_sources))
    with _lock, _conn() as c:
        rows = c.execute(
            f"SELECT * FROM listings WHERE source NOT IN ({ph})",
            tuple(scraped_sources)).fetchall()
    return [{k: r[k] for k in _BACKUP_COLS} for r in rows]


def descriptions_by_uid() -> dict:
    """uid -> stored description, for rows that already have one. Lets a refresh
    reuse detail-page text it already captured instead of re-fetching (and
    avoids the empty bulk-feed description clobbering it on upsert)."""
    with _lock, _conn() as c:
        rows = c.execute(
            "SELECT uid, description FROM listings "
            "WHERE description IS NOT NULL AND description != ''").fetchall()
    return {r["uid"]: r["description"] for r in rows}


def reclassify_all(classify) -> int:
    """Re-run area classification over every stored row using the current
    logic. Heals listings that were mis-tagged by an older version. Returns the
    number of rows whose area changed."""
    changed = 0
    with _lock, _conn() as c:
        rows = c.execute(
            "SELECT uid, area, lat, lng, neighborhood, address, title, description "
            "FROM listings").fetchall()
        for r in rows:
            new_area = classify(r["lat"], r["lng"], r["neighborhood"] or "",
                                r["address"] or "", r["title"] or "",
                                r["description"] or "")
            if new_area != r["area"]:
                c.execute("UPDATE listings SET area=? WHERE uid=?",
                          (new_area, r["uid"]))
                changed += 1
    return changed


def backfill_amenities(parse) -> int:
    """Recompute amenity flags (furnished/parking/laundry/lease) for every row
    from its stored title+description. parse(title, desc) -> dict. Returns the
    number of rows updated."""
    n = 0
    with _lock, _conn() as c:
        rows = c.execute("SELECT uid, title, description FROM listings").fetchall()
        for r in rows:
            a = parse(r["title"] or "", r["description"] or "")
            c.execute(
                "UPDATE listings SET furnished=?, parking=?, laundry=?, lease_term=? "
                "WHERE uid=?",
                (a["furnished"], a["parking"], a["laundry"], a["lease_term"], r["uid"]),
            )
            n += 1
    return n


def backfill_safety(score, best=None) -> int:
    """Recompute the heuristic safety score for every row. score(row_dict) ->
    {'score': int, 'flags': [...]} (see safety.safety_score). If `best` is given
    (best(row_dict, safety) -> int, see safety.best_score), the composite
    ranking score is recomputed too. Returns the number of rows updated. Runs
    after area/amenity backfill so it can use the healed area/lat/lng, the full
    text and the populated amenity columns."""
    n = 0
    with _lock, _conn() as c:
        rows = c.execute(
            "SELECT uid, title, description, price, bedrooms, listing_type, "
            "lat, lng, neighborhood, address, image_url, sqft, furnished, "
            "parking, laundry, lease_term, posted_at FROM listings").fetchall()
        for r in rows:
            d = dict(r)
            res = score(d)
            b = best(d, res["score"]) if best else None
            c.execute("UPDATE listings SET safety=?, safety_flags=?, crime_pct=?, "
                      "best=? WHERE uid=?",
                      (res["score"], json.dumps(res["flags"]),
                       res.get("block_pct"), b, r["uid"]))
            n += 1
    return n


def set_dedup(groups: dict[str, str], primaries: set[str]) -> None:
    """groups: uid -> group_id ; primaries: set of uids that are the shown one."""
    with _lock, _conn() as c:
        for uid, gid in groups.items():
            c.execute(
                "UPDATE listings SET dedup_group=?, is_primary=? WHERE uid=?",
                (gid, 1 if uid in primaries else 0, uid),
            )


def set_status(uid: str, status: str) -> bool:
    if status not in ("new", "favorite", "discarded"):
        return False
    with _lock, _conn() as c:
        cur = c.execute("UPDATE listings SET status=? WHERE uid=?", (status, uid))
        return cur.rowcount > 0


def query(max_price=None, min_price=None, min_bedrooms=None, max_bedrooms=None,
          areas=None, include_rooms=True, status=None, sort="newest",
          include_other=False, sources=None, available_by=None,
          min_sqft=None, listing_type=None, furnished=False, parking=False,
          laundry_in_suite=False, month_to_month=False, min_safety=None,
          hide_hotspots=False) -> list[dict]:
    sql = "SELECT * FROM listings WHERE is_primary=1"
    args: list = []
    if sources:
        ph = ",".join("?" * len(sources))
        sql += f" AND source IN ({ph})"
        args.extend(sources)
    if available_by:
        # Include immediate ('now') and unknown ('') so nothing is hidden just
        # for lacking a parsed date; otherwise the date must be on/before it.
        sql += " AND (available_date IN ('', 'now') OR available_date <= ?)"
        args.append(available_by)
    if max_price is not None:
        sql += " AND (price IS NULL OR price <= ?)"
        args.append(max_price)
    if min_price is not None:
        sql += " AND (price IS NULL OR price >= ?)"
        args.append(min_price)
    if min_sqft is not None:
        # Keep listings with unknown size (sqft IS NULL) rather than hiding them
        # just for missing data — same policy as price/bedrooms.
        sql += " AND (sqft IS NULL OR sqft >= ?)"
        args.append(min_sqft)
    if min_bedrooms is not None:
        sql += " AND (bedrooms IS NULL OR bedrooms >= ?)"
        args.append(min_bedrooms)
    if max_bedrooms is not None:
        sql += " AND (bedrooms IS NULL OR bedrooms <= ?)"
        args.append(max_bedrooms)
    if listing_type == "room_share":
        sql += " AND listing_type = 'room_share'"   # shared rooms only
    elif listing_type == "unit" or not include_rooms:
        sql += " AND listing_type != 'room_share'"  # whole units only
    if furnished:
        sql += " AND furnished = 1"
    if parking:
        sql += " AND parking = 1"
    if laundry_in_suite:
        sql += " AND laundry = 'in_suite'"
    if month_to_month:
        sql += " AND lease_term = 'month-to-month'"
    if min_safety is not None:
        # Unscored rows (safety IS NULL) are kept rather than hidden for missing
        # data — same policy as price/sqft. They get scored on the next refresh.
        sql += " AND (safety IS NULL OR safety >= ?)"
        args.append(min_safety)
    if hide_hotspots:
        # Drop listings in the worst East Van crime blocks (top ~15% by VPD
        # density). Rows outside the grid (Burnaby / un-geocoded) are kept.
        sql += " AND (crime_pct IS NULL OR crime_pct < 85)"
    if areas:
        placeholders = ",".join("?" * len(areas))
        sql += f" AND area IN ({placeholders})"
        args.extend(areas)
    elif not include_other:
        sql += " AND area IN ('east_van','burnaby')"
    if status:
        sql += " AND status = ?"
        args.append(status)

    order = {
        "newest": "last_seen DESC",
        "posted": "posted_at DESC NULLS LAST",
        "price_asc": "price ASC NULLS LAST",
        "price_desc": "price DESC NULLS LAST",
        "sqft_desc": "sqft DESC NULLS LAST",
        "safety_desc": "safety DESC NULLS LAST",
        "best_desc": "best DESC NULLS LAST",
    }.get(sort, "last_seen DESC")
    sql += f" ORDER BY {order}"

    with _lock, _conn() as c:
        rows = c.execute(sql, args).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            try:
                d["safety_flags"] = json.loads(d["safety_flags"]) if d.get("safety_flags") else []
            except (TypeError, ValueError):
                d["safety_flags"] = []
            # Attach the other source links in this dedup group.
            dupes = c.execute(
                "SELECT source, url FROM listings WHERE dedup_group=? AND uid!=?",
                (r["dedup_group"], r["uid"]),
            ).fetchall()
            d["also_on"] = [{"source": x["source"], "url": x["url"]} for x in dupes]
            out.append(d)
    return out


def counts() -> dict:
    with _lock, _conn() as c:
        def n(where, *a):
            return c.execute(f"SELECT COUNT(*) FROM listings WHERE {where}", a).fetchone()[0]
        by_source = {
            r["source"]: r["n"] for r in c.execute(
                "SELECT source, COUNT(*) n FROM listings WHERE is_primary=1 GROUP BY source")
        }
        return {
            "total": n("1=1"),
            "primary": n("is_primary=1"),
            "favorites": n("status='favorite'"),
            "discarded": n("status='discarded'"),
            "east_van": n("area='east_van' AND is_primary=1"),
            "burnaby": n("area='burnaby' AND is_primary=1"),
            "by_source": by_source,
        }


def get_meta(key, default=None):
    with _lock, _conn() as c:
        r = c.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return json.loads(r["value"]) if r else default


def set_meta(key, value) -> None:
    with _lock, _conn() as c:
        c.execute("INSERT INTO meta(key,value) VALUES(?,?) "
                  "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                  (key, json.dumps(value)))
