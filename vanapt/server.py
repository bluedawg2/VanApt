"""Stdlib HTTP server: JSON API + static web UI. No framework dependencies."""
from __future__ import annotations

import base64
import hmac
import json
import os
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import config, db, pipeline

# Optional HTTP Basic Auth. Active only when BOTH env vars are set, so local
# use stays password-free; a hosted deployment sets them to gate access.
_AUTH_USER = os.environ.get("VANAPT_USER") or ""
_AUTH_PASS = os.environ.get("VANAPT_PASS") or ""
_AUTH_ON = bool(_AUTH_USER and _AUTH_PASS)


def _auth_ok(header: str | None) -> bool:
    if not header or not header.startswith("Basic "):
        return False
    try:
        user, _, pw = base64.b64decode(header[6:]).decode("utf-8").partition(":")
    except Exception:
        return False
    # constant-time compare on both fields to avoid timing leaks
    return (hmac.compare_digest(user, _AUTH_USER)
            and hmac.compare_digest(pw, _AUTH_PASS))

# Bump on each deploy so /healthz reveals which build is actually live.
VERSION = "2026-06-29-descfetch-1"

WEB_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "web")
_CT = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
       ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml",
       ".ico": "image/x-icon", ".json": "application/json"}


def _truthy(v) -> bool:
    return str(v).lower() in ("1", "true", "yes", "on")


class Handler(BaseHTTPRequestHandler):
    server_version = "vanapt/1.0"

    def log_message(self, *a):  # quieter console
        pass

    # ---- auth ----
    def _guard(self) -> bool:
        """Return True if the request may proceed. Sends a 401 challenge if not."""
        if not _AUTH_ON or _auth_ok(self.headers.get("Authorization")):
            return True
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="vanapt"')
        self.send_header("Content-Length", "0")
        self.end_headers()
        return False

    # ---- helpers ----
    def _json(self, obj, code=200):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n).decode("utf-8"))
        except Exception:
            return {}

    def _static(self, path: str):
        if path == "/" or path == "":
            path = "/index.html"
        fp = os.path.normpath(os.path.join(WEB_DIR, path.lstrip("/")))
        if not fp.startswith(WEB_DIR) or not os.path.isfile(fp):
            self._json({"error": "not found"}, 404)
            return
        ext = os.path.splitext(fp)[1]
        with open(fp, "rb") as f:
            data = f.read()
        self.send_response(200)
        self.send_header("Content-Type", _CT.get(ext, "application/octet-stream"))
        self.send_header("Content-Length", str(len(data)))
        # Always revalidate so a redeploy's new HTML/JS/CSS shows immediately
        # instead of the browser serving a stale cached copy.
        self.send_header("Cache-Control", "no-cache, must-revalidate")
        self.end_headers()
        self.wfile.write(data)

    # ---- routing ----
    def do_GET(self):
        # Health check must bypass auth, else the platform's probe gets a 401
        # and the deploy never goes live.
        if urllib.parse.urlparse(self.path).path == "/healthz":
            self._json({"ok": True, "version": VERSION})
            return
        if not self._guard():
            return
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        one = lambda k, d=None: q.get(k, [d])[0]

        if u.path == "/api/listings":
            areas = q.get("areas") or None
            if areas and len(areas) == 1 and "," in areas[0]:
                areas = areas[0].split(",")
            sources = q.get("sources") or None
            if sources and len(sources) == 1 and "," in sources[0]:
                sources = sources[0].split(",")
            data = db.query(
                max_price=int(one("max_price")) if one("max_price") else None,
                min_price=int(one("min_price")) if one("min_price") else None,
                min_bedrooms=float(one("min_bedrooms")) if one("min_bedrooms") else None,
                max_bedrooms=float(one("max_bedrooms")) if one("max_bedrooms") else None,
                min_sqft=int(one("min_sqft")) if one("min_sqft") else None,
                min_safety=int(one("min_safety")) if one("min_safety") else None,
                hide_hotspots=_truthy(one("hide_hotspots", "false")),
                areas=areas,
                sources=sources,
                available_by=one("available_by") or None,
                include_rooms=_truthy(one("include_rooms", "true")),
                listing_type=one("listing_type") or None,
                furnished=_truthy(one("furnished", "false")),
                parking=_truthy(one("parking", "false")),
                laundry_in_suite=_truthy(one("laundry_in_suite", "false")),
                month_to_month=_truthy(one("month_to_month", "false")),
                status=one("status") or None,
                sort=one("sort", "newest"),
                include_other=_truthy(one("include_other", "false")),
            )
            self._json({"count": len(data), "listings": data})
        elif u.path == "/api/status":
            self._json(pipeline.status())
        elif u.path == "/api/config":
            self._json({
                "default_max_price": config.DEFAULT_MAX_PRICE,
                "target_price": config.DEFAULT_TARGET_PRICE,
                "collect_max_price": config.COLLECT_MAX_PRICE,
                "sources": list(config.ENABLED_SOURCES.keys()) + ["manual"],
            })
        else:
            self._static(u.path)

    def do_POST(self):
        if not self._guard():
            return
        u = urllib.parse.urlparse(self.path)
        if u.path == "/api/refresh":
            body = self._body()
            self._json(pipeline.refresh(only=body.get("only")))
        elif u.path.startswith("/api/listings/") and u.path.endswith("/status"):
            uid = u.path.split("/")[3]
            status = self._body().get("status", "")
            ok = db.set_status(uid, status)
            self._json({"ok": ok}, 200 if ok else 400)
        elif u.path == "/api/import":
            self._json(pipeline.manual_import(self._body()))
        elif u.path == "/api/import_bulk":
            self._json(pipeline.manual_import_bulk(self._body().get("items")))
        else:
            self._json({"error": "not found"}, 404)


def _start_scheduler():
    """If VANAPT_REFRESH_HOURS is set, refresh the DB on that interval in a
    background thread. Used by hosted deployments so listings stay current
    without anyone clicking — sidesteps Render's one-disk-per-service limit by
    keeping the scrape inside the web process that owns the disk."""
    import threading

    try:
        hours = float(os.environ.get("VANAPT_REFRESH_HOURS") or 0)
    except ValueError:
        hours = 0
    if hours <= 0:
        return

    def _loop():
        import time as _t
        # Populate right away on a fresh deploy (empty disk), else wait a cycle.
        try:
            if not db.counts().get("total"):
                pipeline.refresh(blocking=True)
        except Exception as e:
            print(f"  initial refresh failed: {e}")
        while True:
            _t.sleep(hours * 3600)
            try:
                pipeline.refresh(blocking=True)
            except Exception as e:  # never let the scheduler die
                print(f"  scheduled refresh failed: {e}")

    threading.Thread(target=_loop, daemon=True).start()
    print(f"  auto-refresh every {hours:g}h enabled")


def serve(host="127.0.0.1", port=8777):
    db.init()
    _start_scheduler()
    httpd = ThreadingHTTPServer((host, port), Handler)
    url = f"http://{host}:{port}/"
    print(f"\n  vanapt running -> {url}")
    if _AUTH_ON:
        print(f"  basic auth: ON (user '{_AUTH_USER}')")
    print("  (Ctrl+C to stop)\n")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n  stopped.")
        httpd.shutdown()
