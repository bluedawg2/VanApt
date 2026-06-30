"""Durable backup of manual / Facebook imports to GitHub.

Render's free tier wipes the disk on every redeploy and on sleep. Scraped
sources self-heal (the next refresh re-pulls them), but hand-entered Facebook
Marketplace imports are gone for good — which is why they keep vanishing.

This module mirrors those manual rows to a JSON file on a dedicated branch
(default `vanapt-data`) that Render does NOT auto-deploy, so writing a backup
never triggers a redeploy. On boot we read it back and re-import. Entirely a
no-op unless GITHUB_TOKEN + GITHUB_REPO are set, so local use is unaffected.
"""
from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.request

_TOKEN = os.environ.get("GITHUB_TOKEN") or ""
_REPO = os.environ.get("GITHUB_REPO") or ""              # "owner/name"
_BRANCH = os.environ.get("VANAPT_BACKUP_BRANCH") or "vanapt-data"
_PATH = os.environ.get("VANAPT_BACKUP_PATH") or "manual_imports.json"
_API = "https://api.github.com"


def enabled() -> bool:
    return bool(_TOKEN and _REPO)


def _req(method: str, url: str, body: dict | None = None) -> dict:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": f"Bearer {_TOKEN}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "vanapt-backup",
        "X-GitHub-Api-Version": "2022-11-28",
    })
    with urllib.request.urlopen(req, timeout=20) as r:
        raw = r.read().decode("utf-8")
        return json.loads(raw) if raw else {}


def _get_file() -> dict | None:
    """Current backup file metadata (incl. sha + base64 content), or None."""
    try:
        return _req("GET", f"{_API}/repos/{_REPO}/contents/{_PATH}?ref={_BRANCH}")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise


def _ensure_branch() -> None:
    """Create the (non-deploying) backup branch off the default branch head
    the first time we need it."""
    try:
        _req("GET", f"{_API}/repos/{_REPO}/git/ref/heads/{_BRANCH}")
        return
    except urllib.error.HTTPError as e:
        if e.code != 404:
            raise
    repo = _req("GET", f"{_API}/repos/{_REPO}")
    base = repo.get("default_branch", "main")
    ref = _req("GET", f"{_API}/repos/{_REPO}/git/ref/heads/{base}")
    _req("POST", f"{_API}/repos/{_REPO}/git/refs",
         {"ref": f"refs/heads/{_BRANCH}", "sha": ref["object"]["sha"]})


def restore() -> list:
    """Return the backed-up import payloads (a list), or [] on any problem."""
    if not enabled():
        return []
    try:
        f = _get_file()
        if not f or not f.get("content"):
            return []
        data = json.loads(base64.b64decode(f["content"]).decode("utf-8"))
        return data if isinstance(data, list) else []
    except Exception as e:  # never let a restore failure block boot
        print(f"  backup restore failed: {e}")
        return []


def backup(items: list) -> bool:
    """Write the manual-import payloads to the backup branch. Returns success."""
    if not enabled():
        return False
    try:
        _ensure_branch()
        existing = _get_file()
        content = base64.b64encode(
            json.dumps(items, ensure_ascii=False).encode("utf-8")).decode("ascii")
        body = {
            "message": f"vanapt: backup {len(items)} manual import(s)",
            "content": content,
            "branch": _BRANCH,
        }
        if existing and existing.get("sha"):
            body["sha"] = existing["sha"]
        _req("PUT", f"{_API}/repos/{_REPO}/contents/{_PATH}", body)
        return True
    except Exception as e:  # never let a backup failure break an import
        print(f"  backup failed: {e}")
        return False
