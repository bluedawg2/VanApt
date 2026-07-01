"""Refresh orchestration: scrape -> filter -> upsert -> dedup. Plus manual
import for login-walled sources (Facebook Marketplace, etc.)."""
from __future__ import annotations

import random
import threading
import time

from . import backup, config, db, dedup, scrapers
from .geo import classify_area
from .safety import safety_score, best_score
from .models import (Listing, ROOM_SQFT_MAX, parse_bedrooms, parse_price,
                     parse_sqft, parse_available_date, looks_like_room_share,
                     parse_amenities)

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
    # 3+ BR apartments get a higher price ceiling (rent gets split); everything
    # else stays capped at COLLECT_MAX_PRICE.
    ceiling = config.COLLECT_MAX_PRICE
    if li.bedrooms is not None and li.bedrooms >= 3:
        ceiling = config.COLLECT_MAX_PRICE_3BR
    if li.price is not None and li.price > ceiling:
        return False
    # Whole units: keep studio/1BR up to COLLECT_MAX_BEDROOMS (2-/3-BR places
    # she could share). Room-shares pass regardless of bedroom count.
    if (li.listing_type == "unit" and li.bedrooms is not None
            and li.bedrooms > config.COLLECT_MAX_BEDROOMS):
        return False
    return True


def _enrich_kept(listings) -> int:
    """Craigslist's bulk feed omits the description, so a room-in-a-house looks
    like a whole 'unit' and an absurdly small space goes unflagged. For the
    (much smaller) kept set, give each posting a description, then re-derive size
    + room-share. Descriptions already captured on a prior refresh are reused
    from the DB (no re-fetch, and they survive the empty bulk-feed value on
    upsert), so steady-state refreshes only hit detail pages for *new* postings.

    Detail pages are bot-detectable HTML hits and share an IP with the bulk feed
    we must NOT lose, so we fetch them defensively: capped per refresh, one at a
    time with a jittered pause, and with a circuit breaker that stops the instant
    Craigslist 403s — better to miss a few descriptions than to provoke a block
    that kills the feed. An aborted/failed fetch only costs a description; the
    listing is kept regardless. Returns how many listings were re-tagged as
    room-shares."""
    from .scrapers import craigslist
    cl = [li for li in listings if li.source == "craigslist"]
    if not cl:
        return 0

    existing = db.descriptions_by_uid()
    todo = []
    for li in cl:
        if (li.description or "").strip():
            continue
        prev = existing.get(li.uid())
        if prev:
            li.description = prev   # reuse — preserves it through upsert
        else:
            todo.append(li)         # genuinely new -> fetch the detail page

    lo, hi = config.CRAIGSLIST_DETAIL_DELAY
    for li in todo[:config.CRAIGSLIST_DETAIL_CAP]:
        try:
            desc = craigslist.fetch_description(li.url)
        except craigslist.Blocked:
            break   # circuit breaker: stop before the block escalates to the feed
        if desc:
            li.description = desc
        time.sleep(random.uniform(lo, hi))

    retagged = 0
    for li in cl:
        desc = li.description or ""
        if not li.sqft:
            li.sqft = parse_sqft(desc)
        if li.listing_type == "unit" and (
                looks_like_room_share(li.title, desc)
                or (li.sqft and li.sqft < ROOM_SQFT_MAX)):
            li.listing_type = "room_share"
            retagged += 1
    return retagged


def _refresh_worker(only):
    try:
        res = scrapers.run_all(only=only)
        kept = [li for li in res["listings"] if _relevant(li)]
        retagged = _enrich_kept(kept)
        up = db.upsert_listings(kept)
        db.reclassify_all(classify_area)  # heal any older mis-tagged rows
        db.backfill_amenities(parse_amenities)  # furnished/parking/laundry/lease tags
        db.backfill_safety(safety_score, best_score)  # vetting + composite rank
        collapsed = dedup.rebuild()
        summary = {
            "scraped": len(res["listings"]),
            "kept": len(kept),
            "rooms_retagged": retagged,
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


def _payload_to_listing(payload: dict) -> Listing | None:
    """Build a Listing from a hand-entered / captured payload. Returns None if
    there's no usable URL. Shared by single and bulk import."""
    url = (payload.get("url") or "").strip()
    if not url:
        return None

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
    # Manual/captured imports are trusted even if area can't be determined.
    if li.area == "other" and payload.get("area") in ("east_van", "burnaby"):
        li.area = payload["area"]
    return li


def _rescore_after_import() -> None:
    db.backfill_amenities(parse_amenities)
    db.backfill_safety(safety_score, best_score)
    dedup.rebuild()


def _backup_manual_async() -> None:
    """Mirror all manual/Facebook rows to GitHub so a redeploy or sleep can't
    lose them. Runs off-thread so it never delays the import response. No-op
    unless GITHUB_TOKEN + GITHUB_REPO are configured."""
    if not backup.enabled():
        return

    def _run():
        try:
            payloads = db.manual_backup_payloads(tuple(config.ENABLED_SOURCES))
            backup.backup(payloads)
        except Exception as e:  # pragma: no cover - best effort
            print(f"  manual backup failed: {e}")

    threading.Thread(target=_run, daemon=True).start()


def restore_manual() -> int:
    """Re-import manual/Facebook rows from the GitHub backup (used on a fresh
    boot after the disk was wiped). Preserves favorite/discarded status.
    Returns how many were restored."""
    if not backup.enabled():
        return 0
    payloads = backup.restore()
    if not payloads:
        return 0
    res = manual_import_bulk(payloads, _is_restore=True)
    for p in payloads:
        st = p.get("status")
        if st and st != "new":
            li = _payload_to_listing(p)
            if li:
                db.set_status(li.uid(), st)
    if res.get("imported"):
        _rescore_after_import()
    return res.get("imported", 0)


def restore_manual_async() -> None:
    """Kick off restore_manual in the background so boot isn't blocked on the
    network. No-op unless backup is configured."""
    if not backup.enabled():
        return
    threading.Thread(target=restore_manual, daemon=True).start()


def manual_import(payload: dict) -> dict:
    """Add a single listing by hand (e.g. a Facebook Marketplace post she found).
    Required: url. Everything else is parsed from a pasted blob or fields."""
    li = _payload_to_listing(payload)
    if li is None:
        return {"ok": False, "error": "url is required"}
    db.upsert_listings([li])
    _rescore_after_import()
    _backup_manual_async()
    return {"ok": True, "uid": li.uid(), "area": li.area}


def manual_import_bulk(items, _is_restore: bool = False) -> dict:
    """Add many listings at once — used by the 'Paste from Facebook' capture.
    `items` is a list of payloads (same shape as manual_import). Upserts them
    all, then runs one amenity/safety/dedup pass for the whole batch.
    `_is_restore` suppresses the backup write when we're restoring *from* a
    backup, so a fresh boot doesn't immediately rewrite what it just read."""
    if not isinstance(items, list):
        return {"ok": False, "error": "expected a list of listings"}
    listings, skipped = [], 0
    for payload in items:
        li = _payload_to_listing(payload if isinstance(payload, dict) else {})
        if li is None:
            skipped += 1
        else:
            listings.append(li)
    if listings:
        # Default captured items with no determinable area to east_van so they
        # aren't filtered out of the default view; the user can discard misses.
        for li in listings:
            if li.area == "other":
                li.area = "east_van"
        db.upsert_listings(listings)
        _rescore_after_import()
        if not _is_restore:
            _backup_manual_async()
    return {"ok": True, "imported": len(listings), "skipped": skipped}
