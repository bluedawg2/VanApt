"""Stdlib HTTP server: JSON API + static web UI. No framework dependencies."""
from __future__ import annotations

import json
import os
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import config, db, pipeline

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
        self.end_headers()
        self.wfile.write(data)

    # ---- routing ----
    def do_GET(self):
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
                areas=areas,
                sources=sources,
                available_by=one("available_by") or None,
                include_rooms=_truthy(one("include_rooms", "true")),
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
        else:
            self._json({"error": "not found"}, 404)


def serve(host="127.0.0.1", port=8777):
    db.init()
    httpd = ThreadingHTTPServer((host, port), Handler)
    url = f"http://{host}:{port}/"
    print(f"\n  vanapt running -> {url}")
    print("  (Ctrl+C to stop)\n")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n  stopped.")
        httpd.shutdown()
