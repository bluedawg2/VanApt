# Assessment: turning vanapt into a web app you can share with your daughter

**Short version:** This is very achievable, and cheaper/easier than usual because
the app is already a self-contained web server with no heavy dependencies. The
main work is *hosting it somewhere always-on, putting a password on it, and
refreshing on a schedule.* Budget **1 evening of setup** and **$0–7/month**.

---

## What we have today
- A single Python web server (stdlib only + `beautifulsoup4`), SQLite storage,
  a browser dashboard. Runs with `python run.py` on `127.0.0.1` (local only).
- Refresh is **on-demand** (you click a button).
- One shared database — favorites/discards are global (fine for one user).

## The pleasant surprise about scraping from the cloud
When I tested the scrapers from a data-center IP (like a cloud host would have):

| Source | From a cloud host | From your home PC |
|---|---|---|
| **Craigslist** (JSON API) | ✅ works | ✅ works |
| **Zumper / PadMapper** | ✅ works | ✅ works |
| **Kijiji** | ✅ works | ✅ works |
| **Rentals.ca** | ❌ blocked | ✅ works |

So **3 of 4 sources work fine from a cloud server** — hosting it online does *not*
break scraping (it only costs you Rentals.ca, the smallest contributor). That
makes a hosted option genuinely viable.

---

## Three ways to share it (easiest → most robust)

### Option A — Tunnel from a machine that stays on  ·  $0  ·  ~15 min
Keep running it on a home PC (or a Raspberry Pi) and expose it with
**Cloudflare Tunnel** or **Tailscale Funnel**. You get a private HTTPS URL to
send her.
- ➕ Zero hosting cost, all 4 sources work, no code changes.
- ➖ The machine must stay powered on; you manage updates yourself.

### Option B — Free/cheap PaaS (Render, Railway, Fly.io)  ·  $0–7/mo  ·  ~1 hr ⭐ recommended
Deploy the app to a small managed host with a **persistent disk** for the
SQLite file and a **scheduled job** to auto-refresh every few hours.
- ➕ Always on, HTTPS included, she just opens a URL on her phone.
- ➕ Auto-refreshes — new listings appear without anyone clicking.
- ➖ Loses Rentals.ca; free tiers may sleep when idle (a cron ping fixes that).

### Option C — Tiny VPS (DigitalOcean / Hetzner)  ·  $4–6/mo  ·  ~2 hr
Full control on a small Linux box; run it under `systemd` with a cron refresh.
- ➕ Most reliable & flexible. ➖ You own the server maintenance.

---

## What needs to change for any hosted option (all small)
1. **Bind to `0.0.0.0`** instead of localhost — already a `--host` flag: `python run.py --host 0.0.0.0 --port 8777`.
2. **Add a password.** It would be public, so gate it with HTTP Basic Auth (a
   ~15-line addition to `server.py`) or the host's built-in access control.
   *(Not yet implemented — flagged as the one real prerequisite.)*
3. **HTTPS** — provided automatically by Cloudflare Tunnel / Render / Fly.io.
4. **Scheduled refresh** — instead of clicking the button, run
   `python run.py --refresh` on a timer (platform cron, or add an internal
   scheduler thread). Every 3–6 hours is plenty.
5. *(Optional)* **Per-user favorites** — today favorites are shared. If both of
   you use it and want separate lists, add a simple user column. For one primary
   user, skip it.

## Effort / cost summary
| | Setup effort | Monthly cost | Always-on | All sources |
|---|---|---|---|---|
| A. Tunnel from home PC | ~15 min | $0 | only if PC is on | ✅ 4/4 |
| **B. PaaS (recommended)** | ~1 hr | $0–7 | ✅ | 3/4 |
| C. VPS | ~2 hr | $4–6 | ✅ | 3/4 |

## Recommendation
For sharing with one person in Vancouver: **Option B (Render or Fly.io free
tier)** with **Basic Auth** and a **cron refresh every ~4 hours**. She gets a
phone-friendly URL that's always current, you pay little to nothing, and only
Rentals.ca drops off (Craigslist + Zumper + Kijiji still cover the large
majority). If you'd rather not touch the cloud at all, **Option A** with
Cloudflare Tunnel is the zero-cost fallback.

The **Basic Auth gate** and **scheduled-refresh** hook are now implemented (see below).

---

## Deploying to Render (the recommended path)

Everything is wired through **`render.yaml`** (a Render Blueprint), so you don't
click services together by hand.

**Steps:**
1. In Render: **New ▸ Blueprint**.
2. Connect the **VanApt** GitHub repo. Render reads `render.yaml` and shows a
   plan: one web service + a 1 GB disk + auto-refresh.
3. It prompts for two values — **`VANAPT_USER`** and **`VANAPT_PASS`**. These are
   the login you give your daughter. Pick anything.
4. Click **Apply**. First build takes a few minutes; on boot it auto-runs one
   refresh to populate the disk, then refreshes every 24h.
5. Open the service URL, log in with the user/pass — send her that URL.

**How the moving parts map to the app:**
| Concern | How it's handled |
|---|---|
| Password | `VANAPT_USER` + `VANAPT_PASS` env vars → HTTP Basic Auth (`server.py`). Unset locally = no password. |
| Persistent favorites | SQLite on the mounted disk via `VANAPT_DATA_DIR=/var/data`. Survives deploys. |
| Daily refresh | `VANAPT_REFRESH_HOURS=24` → internal scheduler thread (no separate cron, because a Render disk attaches to only one service). |
| Bind address / port | `run.py` auto-uses `$PORT` and `0.0.0.0` when hosted; stays `127.0.0.1:8777` locally. |
| Auto-deploy | `autoDeploy: true` → every `git push` redeploys. |

**Cost:** the blueprint uses the **Starter** plan ($7/mo, always-on). To test for
free first, change `plan: starter` to `plan: free` in `render.yaml` (it sleeps
when idle — first load after a quiet spell is slow, and the in-process scheduler
only runs while it's awake).

## One caveat (legal/ToS)
These sites' terms discourage scraping. Keeping this **personal, low-volume, and
not redistributed/commercial** keeps it firmly in normal-personal-use territory.
Don't turn it into a public listing site.
