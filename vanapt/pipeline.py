"""Refresh orchestration: scrape -> filter -> upsert -> dedup. Plus manual
import for login-walled sources (Facebook Marketplace, etc.)."""
from __future__ import annotations

import threading
import time

from . import config, db, dedup, scrapers
from .geo import classify_area
from .models import (Listing, parse_bedrooms, parse_price, parse_sqft,
                     parse_available_date, looks_like_room_share, parse_amenities)

# Refresh runs in a background thread; this tracks live status for the UI.
_state = {"running": False, "started": None, "finished": None,
          "result": None, "error": None}
_state_lock = threading.Lock()


def status() -> dict:
    with _state_lock:
        s = dict(_state)
    s["last_refresh"] = db.get_meta("last_refresh")
    s["last_summary"] = db.get_meta("last_summary")
    s["counts"] = db.counts()
    return s


def is_running() -> bool:
    with _state_lock:
        return _state["running"]


def _relevant(li: Listing) -> bool:
    """Collection-time filter: keep only plausibly-relevant inventory."""
    if li.area not in ("east_van", "burnaby"):
        return False
    if li.price is not None and li.price > config.COLLECT_MAX_PRICE:
        return False
    # Whole units: keep studio/1BR (and 2BR, which may host a room-share).
    if li.listing_type == "unit" and li.bedrooms is not None and li.bedrooms > 2:
        return False
    return True


def _refresh_worker(only):
    try:
        res = scrapers.run_all(only=only)
        kept = [li for li in res["listings"] if _relevant(li)]
        up = db.upsert_listings(kept)
        db.reclassify_all(classify_area)  # heal any older mis-tagged rows
        db.backfill_amenities(parse_amenities)  # furnished/parking/laundry/lease tags
        collapsed = dedup.rebuild()
        summary = {
            "scraped": len(res["listings"]),
            "kept": len(kept),
            "new": up["new"],
            "updated": up["updated"],
            "duplicates_collapsed": collapsed,
            "sources": res["sources"],
        }
        db.set_meta("last_refresh", time.strftime("%Y-%m-%d %H:%M:%S"))
        db.set_meta("last_summary", summary)
        with _state_lock:
            _state.update(running=False, finished=time.time(),
                          result=summary, error=None)
    except Exception as e:  # pragma: no cover - safety net
        with _state_lock:
            _state.update(running=False, finished=time.time(), error=str(e))


def refresh(only=None, blocking=False) -> dict:
    with _state_lock:
        if _state["running"]:
            return {"started": False, "reason": "already running"}
        _state.update(running=True, started=time.time(), finished=None,
                      result=None, error=None)
    if blocking:
        _refresh_worker(only)
        return {"started": True, "result": status()}
    threading.Thread(target=_refresh_worker, args=(only,), daemon=True).start()
    return {"started": True}


def manual_import(payload: dict) -> dict:
    """Add a listing by hand (e.g. a Facebook Marketplace post she found).
    Required: url. Everything else is parsed from a pasted blob or fields."""
    url = (payload.get("url") or "").strip()
    if not url:
        return {"ok": False, "error": "url is required"}

    blob = " ".join(str(payload.get(k, "")) for k in ("title", "description", "raw"))
    price = payload.get("price")
    price = int(price) if str(price).strip().isdigit() else parse_price(blob)
    beds = payload.get("bedrooms")
    try:
        beds = float(beds)
    except (TypeError, ValueError):
        beds = parse_bedrooms(blob)

    title = (payload.get("title") or "").strip() or "(manual import)"
    desc = (payload.get("description") or payload.get("raw") or "").strip()
    hood = (payload.get("neighborhood") or "").strip()
    lt = payload.get("listing_type")
    if lt not in ("unit", "room_share"):
        lt = "room_share" if looks_like_room_share(title, desc) else "unit"

    li = Listing(
        source=(payload.get("source") or "manual"),
        source_id=url,
        url=url,
        title=title,
        description=desc,
        price=price,
        bedrooms=beds,
        sqft=parse_sqft(blob),
        listing_type=lt,
        neighborhood=hood,
        image_url=(payload.get("image_url") or "").strip(),
        contact=(payload.get("contact") or "").strip(),
        available_date=(payload.get("available_date") or "").strip()
        or parse_available_date(blob),
    )
    li.area = classify_area(payload.get("lat"), payload.get("lng"),
                            hood, title, desc) if (hood or title or desc) else "other"
    # Manual imports are trusted even if area can't be determined.
    if li.area == "other" and payload.get("area") in ("east_van", "burnaby"):
        li.area = payload["area"]
    db.upsert_listings([li])
    db.backfill_amenities(parse_amenities)
    dedup.rebuild()
    return {"ok": True, "uid": li.uid(), "area": li.area}
