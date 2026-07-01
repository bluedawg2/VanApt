"""Craigslist Vancouver scraper via the modern JSON search API (sapi).

Craigslist's RSS/HTML endpoints are aggressively bot-blocked (HTTP 403), but the
JSON API its own site calls — https://sapi.craigslist.org — responds fine. It
returns up to 360 rich results per query (lat/lng, bedrooms, sqft, title, price,
photo, slug), so we widen coverage by querying multiple price bands per category.

Vancouver = area 16. Item arrays use a compact positional + tagged encoding;
see _decode_items() for the layout discovered from live responses.
"""
from __future__ import annotations

import datetime
import json
import random
import time
import urllib.error

from bs4 import BeautifulSoup

from .. import config
from ..geo import classify_area
from ..models import (Listing, parse_bedrooms, parse_sqft,
                      parse_available_date, looks_like_room_share)
from .base import fetch

SAPI = "https://sapi.craigslist.org/web/v8/postings/search/full"
VANCOUVER_AREA = 16

# searchPath -> default listing_type
CATEGORIES = [
    ("apa", "unit"),        # apartments / housing for rent
    ("roo", "room_share"),  # rooms & shares (roommate wanted)
]


class Blocked(Exception):
    """Craigslist refused the request (HTTP 403) — almost always an IP/rate
    block, not a bad URL. Raised by fetch_description() so the caller can stop
    hitting detail pages before the block escalates and takes out the bulk feed
    too (both share one IP)."""


def _build_url(search_path: str, lo: int, hi: int) -> str:
    parts = [
        f"batch={VANCOUVER_AREA}-0-360-0-0",
        "cc=US", "lang=en", f"searchPath={search_path}",
        f"min_price={lo}", f"max_price={hi}",
    ]
    if search_path == "apa":
        parts += [f"min_bedrooms={config.DEFAULT_MIN_BEDROOMS}",
                  f"max_bedrooms={config.COLLECT_MAX_BEDROOMS}"]
    return f"{SAPI}?{'&'.join(parts)}"


def _decode_items(data: dict, listing_type: str) -> list[Listing]:
    """Decode one sapi response payload into Listings.

    Item layout (positional):
      [0] postId delta (add to decode.minPostingId)
      [1] posted-time offset (add to decode.minPostedDate, epoch seconds)
      [2] category code   [3] price
      [4] "locIdx:descIdx~lat~lng"   [5] image-host code (ignored)
      then tagged sub-arrays + one bare-string title:
        [13, base62Id]  [4, img...]  [6, slug]  [10, "$price"]  [5, beds, sqft]
    """
    dec = data.get("decode", {})
    min_id = dec.get("minPostingId", 0)
    min_posted = dec.get("minPostedDate", 0)
    locations = dec.get("locations", [])
    loc_descs = dec.get("locationDescriptions", [])
    cat = data.get("categoryAbbr", "apa")
    out: list[Listing] = []

    for it in data.get("items", []):
        try:
            post_id = min_id + it[0]
            posted_at = ""
            if min_posted and len(it) > 1 and isinstance(it[1], (int, float)):
                posted_at = datetime.datetime.utcfromtimestamp(
                    min_posted + it[1]).strftime("%Y-%m-%dT%H:%M:%S")
            price = it[3] if isinstance(it[3], (int, float)) and it[3] else None

            lat = lng = None
            host, sub, hood = "vancouver", "", ""
            loc = it[4] if len(it) > 4 else None
            if isinstance(loc, str) and "~" in loc:
                head, *coords = loc.split("~")
                if len(coords) >= 2:
                    try:
                        lat, lng = float(coords[0]), float(coords[1])
                    except ValueError:
                        pass
                bits = head.split(":")
                li_idx = int(bits[0]) if bits[0].lstrip("-").isdigit() else 0
                d_idx = int(bits[1]) if len(bits) > 1 and bits[1].lstrip("-").isdigit() else 0
                if 0 < li_idx < len(locations) and isinstance(locations[li_idx], list):
                    host = locations[li_idx][1] or host
                    sub = locations[li_idx][2] or ""
                if 0 < d_idx < len(loc_descs) and isinstance(loc_descs[d_idx], str):
                    hood = loc_descs[d_idx]

            slug = title = img = ""
            beds = sqft = None
            for el in it[6:]:  # it[5] is the image-host code, not the title
                if isinstance(el, str):
                    if not title:
                        title = el           # first bare string = title
                elif isinstance(el, list) and el:
                    tc = el[0]
                    if tc == 6 and len(el) > 1:
                        slug = el[1] or ""
                    elif tc == 4 and len(el) > 1 and isinstance(el[1], str):
                        img = el[1]
                    elif tc == 5:
                        if len(el) > 1 and isinstance(el[1], (int, float)):
                            beds = float(el[1])
                        if len(el) > 2 and isinstance(el[2], (int, float)) and el[2]:
                            sqft = int(el[2])

            url = f"https://{host}.craigslist.org/{sub}/{cat}/d/{slug or 'listing'}/{post_id}.html"
            image_url = ""
            if img and ":" in img:
                image_url = f"https://images.craigslist.org/{img.split(':', 1)[1]}_600x450.jpg"

            if beds is None:
                beds = parse_bedrooms(title)
            if not sqft:
                sqft = parse_sqft(title)

            lt = listing_type
            if lt == "unit" and looks_like_room_share(title, hood):
                lt = "room_share"

            li = Listing(
                source="craigslist",
                source_id=str(post_id),
                url=url,
                title=title,
                price=int(price) if price else None,
                bedrooms=beds,
                sqft=sqft,
                listing_type=lt,
                neighborhood=hood,
                lat=lat,
                lng=lng,
                image_url=image_url,
                available_date=parse_available_date(title),
                posted_at=posted_at,
            )
            li.area = classify_area(lat, lng, hood, title)
            out.append(li)
        except Exception:
            continue  # one malformed item never breaks the batch
    return out


def fetch_description(url: str, timeout: int = 8) -> str:
    """Fetch one posting's detail page and return its body text plus the
    structured attribute chips (bedrooms, ft², housing type, laundry, parking).

    The bulk sapi feed carries none of this, so a room-in-someone's-house reads
    as a whole 'unit' until we read the actual post. Returns '' on any failure
    (incl. timeout) so a single slow/blocked page never stalls a refresh — a
    short timeout matters because a datacenter IP gets throttled here."""
    try:
        html = fetch(url, timeout=timeout, proxy=True)
    except urllib.error.HTTPError as e:
        if e.code == 403:
            raise Blocked(url) from e   # signal the caller to back off
        return ""
    except Exception:
        return ""
    soup = BeautifulSoup(html, "html.parser")
    parts: list[str] = []
    body = soup.select_one("#postingbody")
    if body:
        for junk in body.select(".print-information, .print-qrcode-container"):
            junk.decompose()
        txt = body.get_text(" ", strip=True).replace("QR Code Link to This Post", "").strip()
        if txt:
            parts.append(txt)
    attrs = [a.get_text(" ", strip=True)
             for a in soup.select(".attrgroup span") if a.get_text(strip=True)]
    if attrs:
        parts.append(" · ".join(attrs))
    return "\n".join(parts)


def scrape() -> list[Listing]:
    seen: dict[str, Listing] = {}
    errors = 0
    d_lo, d_hi = config.CRAIGSLIST_FEED_DELAY
    first = True
    for search_path, lt in CATEGORIES:
        for (lo, hi) in config.CRAIGSLIST_PRICE_BANDS:
            if not first:
                time.sleep(random.uniform(d_lo, d_hi))  # don't fire all 14 at once
            first = False
            try:
                text = fetch(_build_url(search_path, lo, hi), proxy=True)
                data = json.loads(text).get("data", {})
                for li in _decode_items(data, lt):
                    seen[li.url] = li
            except Exception:
                errors += 1
                continue
    if not seen and errors:
        raise RuntimeError(f"craigslist: all {errors} API requests failed")
    return list(seen.values())
