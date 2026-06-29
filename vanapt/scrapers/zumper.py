"""Zumper scraper (also covers PadMapper — same backend).

Zumper server-renders its search results into a Redux store at
``window.__PRELOADED_STATE__``; the listings live at
``currentSearch.listables.listables``. We page through the Vancouver and Burnaby
search pages and read that array directly. These are mostly managed *buildings*
with a price/bedroom *range*, so we use the building's minimum 1BR price.
"""
from __future__ import annotations

from .. import config
from ..geo import classify_area
from ..models import Listing, parse_available_date, looks_like_room_share
from .base import fetch
from .jsonwalk import extract_assigned_json

# Each base page is paged with ?page=N. 1-bedroom + studio focus keeps results
# relevant to the search (still filtered against our price ceiling downstream).
BASE_PAGES = [
    "https://www.zumper.com/apartments-for-rent/vancouver-bc/1-bedroom",
    "https://www.zumper.com/apartments-for-rent/vancouver-bc/studio",
    "https://www.zumper.com/apartments-for-rent/burnaby-bc/1-bedroom",
    "https://www.zumper.com/apartments-for-rent/burnaby-bc/studio",
]
MAX_PAGES = 5  # 25 listings/page


def _to_listing(d: dict) -> Listing | None:
    url = d.get("url") or d.get("pa_url") or d.get("padmapper_url") or ""
    if url.startswith("/"):
        url = "https://www.zumper.com" + url
    if not url.startswith("http"):
        return None

    def _sane(v, lo, hi):  # Zumper uses 2^63-1 / 0 as "no data" sentinels
        return v if isinstance(v, (int, float)) and lo <= v <= hi else None

    price = _sane(d.get("min_price"), 200, 20000) or _sane(d.get("max_price"), 200, 20000)
    beds = d.get("min_bedrooms")
    if not isinstance(beds, (int, float)) or beds < 0 or beds > 10:
        beds = None
    sqft = _sane(d.get("min_square_feet"), 100, 20000)
    baths = _sane(d.get("min_bathrooms"), 0.5, 20)
    lat, lng = d.get("lat"), d.get("lng")
    title = d.get("title") or d.get("building_name") or d.get("name") or ""
    hood = d.get("neighborhood_name") or ""
    addr = ", ".join(p for p in (d.get("address"), d.get("city"),
                                 d.get("state"), d.get("zipcode")) if p)
    img = ""
    ids = d.get("image_ids") or []
    if ids:
        img = f"https://img.zumpercdn.com/{ids[0]}/600x400"

    li = Listing(
        source="zumper",
        source_id=str(d.get("listing_id") or url),
        url=url,
        title=str(title)[:300],
        description=str(d.get("short_description") or "")[:1000],
        price=int(price) if price else None,
        bedrooms=float(beds) if beds is not None else None,
        bathrooms=float(baths) if baths else None,
        sqft=int(sqft) if sqft else None,
        listing_type="room_share" if looks_like_room_share(str(title)) else "unit",
        neighborhood=hood,
        address=addr,
        lat=lat,
        lng=lng,
        image_url=img,
        available_date=parse_available_date(str(d.get("date_available") or "")),
    )
    li.area = classify_area(lat, lng, hood, addr, str(title))
    return li


def _listables(html: str) -> list[dict]:
    state = extract_assigned_json(html, r"window\.__PRELOADED_STATE__")
    if not state:
        return []
    try:
        return state["currentSearch"]["listables"]["listables"] or []
    except (KeyError, TypeError):
        return []


def scrape() -> list[Listing]:
    out: dict[str, Listing] = {}
    ok = False
    for base in BASE_PAGES:
        for page in range(1, MAX_PAGES + 1):
            url = base if page == 1 else f"{base}?page={page}"
            try:
                rows = _listables(fetch(url))
                ok = True
            except Exception:
                break  # stop paging this base on error
            if not rows:
                break  # no more pages
            for d in rows:
                li = _to_listing(d)
                if li and li.price:
                    out[li.url] = li
    if not out and not ok:
        raise RuntimeError("zumper: search pages failed to load")
    return list(out.values())
