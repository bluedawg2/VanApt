# 🏠 East Van & Burnaby Apartment Finder (`vanapt`)

A local app that collects 1-bedroom (and studio / 2-BR) rental listings — plus
**roommate / room-share** posts — across **East Vancouver** and **Burnaby** from
multiple sites, removes duplicates, and gives you one clean dashboard to browse,
**favorite ★**, and **discard ✕** options.

Built for the simple goal: *see the most choices in one place and quickly narrow
down where to move.*

---

## Quick start

1. Make sure Python 3.10+ is installed (`python --version`).
2. From this folder, run:

   **Windows (easiest):** right-click `start.ps1` → *Run with PowerShell*

   **Or from a terminal:**
   ```
   python run.py
   ```
3. Your browser opens at **http://127.0.0.1:8777**.
4. Click **↻ Refresh listings** (top right) the first time to collect data.
   It takes ~30–90 seconds. New/updated listings appear automatically.

That's it. Re-open any time with `python run.py`; your favorites and discards
are remembered between sessions.

---

## Using the dashboard

| Control | What it does |
|---|---|
| **Max price slider** | Drag down to tighten the budget. Quick chips for ≤ $1500 / $1800 / $2000. |
| **Bedrooms** | Studio / 1 BR / 2 BR (any combination). Default is 1 BR. |
| **Area** | East Van and/or Burnaby. |
| **Sources** | Toggle Craigslist / Zumper / Kijiji / Rentals.ca / Manual on or off. |
| **Move-in by** | Show only places available now or by a chosen month (listings with no stated date are kept, not hidden). |
| **List / Map toggle** | Switch between cards and an interactive map (markers coloured by area; blue = East Van, green = Burnaby, amber = favorite). |
| **Include roommate / room-shares** | Shows "looking for a roommate" + room-in-shared-place posts. |
| **Show** | Active, ★ Favorites only, new/unseen, discarded, or everything. |
| **★ / ✕ on each card** | Favorite or discard. Discarded listings hide by default. |
| **Also on:** | When the same place was posted to several sites, links to each. |
| **+ Add a listing manually** | Paste a Facebook Marketplace (or any) link + details so it sits alongside the rest. |

---

## Sources

| Source | Method | Reliability |
|---|---|---|
| **Craigslist** | Official JSON search API (`sapi`), apartments + rooms, multiple price bands | ⭐ The backbone — ~700+ East Van/Burnaby places per refresh, with map coordinates, bedrooms, sqft & photos. |
| **Kijiji** | Embedded-JSON extraction | Good. Works without login. |
| **Rentals.ca** | Embedded-JSON extraction | Good on a home connection. |
| **Zumper / PadMapper** | JSON API with HTML fallback | Best-effort (they share a backend). |
| **Facebook Marketplace** | Manual import | Login-walled, can't be auto-scraped — use **+ Add a listing manually**. |

> **Note on blocking:** Some sites reject requests from data-center IPs but are
> fine from a normal home connection. If a source shows an error after a refresh,
> it's usually a temporary block — try again later, or just rely on the others.
> The app is designed so one blocked site never stops the rest.

---

## How duplicates are removed

A place posted to several sites (or re-posted) is detected by comparing price,
bedroom count, map distance (when coordinates exist), and title similarity. Each
real place is shown **once**, with links to every site it appeared on. Anything
you've favorited is never hidden as a duplicate.

---

## How areas are decided

A listing is tagged **East Van** or **Burnaby** by, in order of confidence:
1. **Map coordinates** (bounding boxes), then
2. **Postal code** (e.g. `V5K…` = East Van, `V5H…` = Burnaby), then
3. **Neighborhood keywords** (Commercial Drive, Metrotown, …).

West-side Vancouver, North Van, Coquitlam, White Rock, etc. are filtered out.
Tune the boundaries in `vanapt/config.py`.

---

## Commands

```
python run.py                 # start the app (opens browser)
python run.py --no-browser    # start without opening a browser
python run.py --refresh       # scrape once to the database, then exit
python run.py --only kijiji craigslist   # limit to specific sources
python run.py --port 9000     # use a different port
```

## Configuration (`vanapt/config.py`)

- `COLLECT_MAX_PRICE` — hard ceiling for what's collected (default $2200).
- `DEFAULT_TARGET_PRICE` — the budget you usually slide down to ($1500).
- `ENABLED_SOURCES` — turn individual sites on/off.
- `EAST_VAN_BBOX` / `BURNABY_BBOX` / `*_HOODS` — area definitions.
- `CRAIGSLIST_PRICE_BANDS` — price slices used to widen Craigslist coverage.

## Project layout

```
run.py / start.ps1        launchers
vanapt/
  config.py               filters, sources, area definitions
  models.py               Listing model + text parsing + fingerprints
  geo.py                  East Van / Burnaby classification
  db.py                   SQLite storage (remembers favorites/discards)
  dedup.py                cross-source duplicate grouping
  pipeline.py             refresh orchestration + manual import
  server.py               stdlib HTTP server (API + static UI)
  scrapers/               one module per site (pluggable)
web/                      dashboard (index.html, app.js, style.css)
data/listings.db          your saved listings + favorites (created on first run)
```

To add a new site: drop a `scrapers/yoursite.py` exposing `scrape() -> [Listing]`
and register it in `scrapers/__init__.py`.
