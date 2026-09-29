#!/usr/bin/env python3
"""JARVIS Google account linking + verification.

Usage (run with any Python that has google-auth-oauthlib, e.g. the Hermes venv):
  python scripts/google_auth.py link personal skalamera@gmail.com
  python scripts/google_auth.py link work stephen@hadrius.com
  python scripts/google_auth.py verify [personal|work]

Tokens: ~/.hermes/jarvis/google/<account>.json (chmod 600).
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from google.auth.transport.requests import AuthorizedSession, Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

BASE = Path(os.environ.get("JARVIS_GOOGLE_DIR", Path.home() / ".hermes/jarvis/google"))
CLIENT = BASE / "client_secret.json"

SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://mail.google.com/",  # full Gmail: read, draft, send, label, trash, delete
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/documents",
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/contacts",
]

ACCOUNTS = ("personal", "work")


def token_path(account: str) -> Path:
    return BASE / f"{account}.json"


def load(account: str) -> Credentials:
    p = token_path(account)
    creds = Credentials.from_authorized_user_file(str(p))
    if not creds.valid:
        creds.refresh(Request())
        save(account, creds, json.loads(p.read_text()).get("email"))
    return creds


def save(account: str, creds: Credentials, email: str | None) -> None:
    BASE.mkdir(parents=True, exist_ok=True)
    os.chmod(BASE, 0o700)
    data = json.loads(creds.to_json())
    data.update({"account": account, "email": email})
    p = token_path(account)
    p.write_text(json.dumps(data, indent=2))
    os.chmod(p, 0o600)


def link(account: str, email: str) -> None:
    flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT), SCOPES)
    creds = flow.run_local_server(
        host="localhost",
        port=0,
        open_browser=True,
        timeout_seconds=900,
        authorization_prompt_message=f"[{account}] Sign in as {email}: {{url}}",
        success_message=f"J.A.R.V.I.S. linked to {email}. You can close this tab.",
        login_hint=email,
        prompt="consent select_account",
        access_type="offline",
    )
    got = AuthorizedSession(creds).get(
        "https://gmail.googleapis.com/gmail/v1/users/me/profile").json().get("emailAddress")
    if not got or got.lower() != email.lower():
        sys.exit(f"[{account}] ERROR: signed in as {got!r}, expected {email}. Token NOT saved.")
    if not creds.refresh_token:
        sys.exit(f"[{account}] ERROR: no refresh token returned. Token NOT saved.")
    save(account, creds, got)
    print(f"[{account}] LINKED {got} -> {token_path(account)}", flush=True)


CHECKS = {
    # name: (url, statuses that prove the API is enabled + authorized)
    "gmail": ("https://gmail.googleapis.com/gmail/v1/users/me/messages?maxResults=1", {200}),
    "calendar": ("https://www.googleapis.com/calendar/v3/users/me/calendarList?maxResults=1", {200}),
    "drive": ("https://www.googleapis.com/drive/v3/files?pageSize=1", {200}),
    "docs": ("https://docs.googleapis.com/v1/documents/jarvis-api-probe", {404}),
    "sheets": ("https://sheets.googleapis.com/v4/spreadsheets/jarvis-api-probe", {404}),
    "people": ("https://people.googleapis.com/v1/people/me/connections?pageSize=1&personFields=names", {200}),
}


def verify(account: str) -> bool:
    s = AuthorizedSession(load(account))
    ok_all = True
    for name, (url, good) in CHECKS.items():
        r = s.get(url)
        ok = r.status_code in good
        ok_all &= ok
        detail = "" if ok else " " + r.text[:200].replace("\n", " ")
        print(f"[{account}] {name:9} {'OK ' if ok else 'FAIL'} HTTP {r.status_code}{detail}", flush=True)
    return ok_all


def main(argv: list[str]) -> None:
    if len(argv) >= 3 and argv[0] == "link" and argv[1] in ACCOUNTS:
        link(argv[1], argv[2])
        verify(argv[1])
    elif argv and argv[0] == "verify":
        targets = argv[1:] or [a for a in ACCOUNTS if token_path(a).exists()]
        results = [verify(a) for a in targets]
        sys.exit(0 if results and all(results) else 1)
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
