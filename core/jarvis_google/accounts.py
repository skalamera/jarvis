"""Google account registry for JARVIS.

Two linked accounts, each with its own OAuth token file (see scripts/google_auth.py):
  personal -> skalamera@gmail.com
  work     -> stephen@hadrius.com
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

GOOGLE_DIR = Path(os.environ.get("JARVIS_GOOGLE_DIR", Path.home() / ".hermes/jarvis/google"))
ACCOUNTS = ("personal", "work")

_ALIASES = {
    "personal": "personal", "home": "personal", "gmail": "personal", "private": "personal",
    "work": "work", "hadrius": "work", "office": "work", "business": "work", "company": "work",
}
_lock = threading.Lock()


class AccountError(ValueError):
    pass


def resolve_account(name: str | None) -> str:
    """Map a free-form account name/email to 'personal' or 'work'."""
    if not name:
        raise AccountError("account is required: 'personal' (skalamera@gmail.com) or 'work' (stephen@hadrius.com)")
    key = name.strip().lower()
    if key in _ALIASES:
        return _ALIASES[key]
    for acct in ACCOUNTS:
        if key == account_email(acct).lower():
            return acct
    if key.endswith("@hadrius.com"):
        return "work"
    if key.endswith("@gmail.com"):
        return "personal"
    raise AccountError(f"unknown account {name!r}; use 'personal' or 'work'")


def token_path(account: str) -> Path:
    return GOOGLE_DIR / f"{account}.json"


def account_email(account: str) -> str:
    try:
        return json.loads(token_path(account).read_text()).get("email") or account
    except FileNotFoundError:
        return account


def linked_accounts() -> list[dict]:
    return [{"account": a, "email": account_email(a)} for a in ACCOUNTS if token_path(a).exists()]


def credentials(account: str) -> Credentials:
    account = resolve_account(account)
    p = token_path(account)
    if not p.exists():
        raise AccountError(f"{account} account is not linked (missing {p})")
    with _lock:
        creds = Credentials.from_authorized_user_file(str(p))
        if not creds.valid:
            creds.refresh(Request())
            data = json.loads(p.read_text())
            data.update(json.loads(creds.to_json()))
            p.write_text(json.dumps(data, indent=2))
            os.chmod(p, 0o600)
    return creds


_API_VERSIONS = {
    "gmail": "v1", "calendar": "v3", "drive": "v3", "docs": "v1", "sheets": "v4", "people": "v1",
}


_local = threading.local()


def _cached_service(api: str, account: str, token_fingerprint: str):
    # Per-THREAD cache: googleapiclient's httplib2 transport is not thread-safe, and Core runs Gmail calls
    # concurrently via asyncio.to_thread (briefing + telemetry + HUD clicks). A shared client caused
    # "[SSL] record layer failure". One client per (thread, api, account, token) avoids that.
    cache = getattr(_local, "services", None)
    if cache is None:
        cache = _local.services = {}
    key = (api, account, token_fingerprint)
    svc = cache.get(key)
    if svc is None:
        for k in [k for k in cache if k[:2] == (api, account)]:
            del cache[k]  # drop clients built with an old token
        svc = cache[key] = build(api, _API_VERSIONS[api], credentials=credentials(account), cache_discovery=False)
    return svc


def service(api: str, account: str):
    """Build (and cache) a Google API client. Cache key includes the token so refreshes rebuild."""
    account = resolve_account(account)
    creds = credentials(account)
    return _cached_service(api, account, (creds.token or "")[-16:])
