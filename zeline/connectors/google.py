"""Google connector (Gmail, Calendar, Sheets, Drive via Google OAuth2).

The operator links it once with ``zeline connect google``. On a machine with
a browser the flow completes automatically through a local callback server;
on a headless box the auth URL is printed and the operator re-runs the
command with ``--code <kode>`` pasted from the browser.
"""
from __future__ import annotations

import base64
import datetime
import email.utils
import secrets
import time
import urllib.parse

import requests

from zeline.connectors import oauth, store
from zeline.connectors.base import BaseConnector

AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"
GMAIL_API = "https://gmail.googleapis.com/gmail/v1"
CALENDAR_API = "https://www.googleapis.com/calendar/v3"
SHEETS_API = "https://sheets.googleapis.com/v4/spreadsheets"
DRIVE_API = "https://www.googleapis.com/drive/v3"

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/spreadsheets.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
]

REDIRECT_URI = "http://127.0.0.1:8765/"
_CALLBACK_PORT = 8765
_TIMEOUT = 30


def _now_utc_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


class GoogleConnector(BaseConnector):
    id = "google"
    name = "Google"
    description = "Gmail, Calendar, Sheets, Drive via Google OAuth2."
    auth_kind = "oauth2"

    def connect(self, client_id: str = "", client_secret: str = "", code: str = "", **kwargs) -> str:
        client_id = (client_id or kwargs.get("client_id") or "").strip()
        client_secret = (client_secret or kwargs.get("client_secret") or "").strip()
        code = (code or kwargs.get("code") or "").strip()
        if not client_id or not client_secret:
            return "ERROR: client_id and client_secret are required."
        if not code:
            auth_url = oauth.build_auth_url(
                AUTH_ENDPOINT, client_id, REDIRECT_URI, SCOPES, secrets.token_urlsafe(16)
            )
            print(f"Open this URL in a browser and approve access:\n{auth_url}\n")
            print("Waiting for the browser callback (timeout 120s)...")
            code = oauth.run_local_callback(_CALLBACK_PORT, timeout=120) or ""
            if not code:
                return (
                    "ERROR: no authorization code received. Open the URL above, "
                    "approve, then re-run with `zeline connect google --code <kode>`."
                )
        try:
            token = oauth.exchange_code(TOKEN_ENDPOINT, client_id, client_secret, code, REDIRECT_URI)
        except Exception as exc:
            return f"ERROR: token exchange failed ({exc})."
        access = token.get("access_token")
        if not access:
            return "ERROR: Google returned no access token."
        try:
            resp = requests.get(
                USERINFO_URL, headers={"Authorization": f"Bearer {access}"}, timeout=_TIMEOUT
            )
        except requests.RequestException as exc:
            return f"ERROR: could not reach Google ({exc})."
        if resp.status_code != 200:
            return f"ERROR: Google rejected the token (HTTP {resp.status_code})."
        email_addr = resp.json().get("email", "?")
        store.save(
            self.id,
            {
                "client_id": client_id,
                "client_secret": client_secret,
                "email": email_addr,
                "token": token,
            },
        )
        return f"Connected to Google as {email_addr}."

    def disconnect(self) -> str:
        if store.delete(self.id):
            return "Google disconnected."
        return "Google was not connected."

    def status(self) -> dict:
        data = store.load(self.id)
        if not data or not (data.get("token") or {}).get("access_token"):
            return {"connected": False, "detail": "not linked"}
        return {"connected": True, "detail": data.get("email", "?")}

    # -- API helpers -----------------------------------------------------

    def _session(self) -> oauth.OAuth2Session:
        data = store.load(self.id) or {}
        token = data.get("token") or {}
        if not token.get("access_token"):
            raise RuntimeError("not connected")
        return oauth.OAuth2Session(
            TOKEN_ENDPOINT,
            data.get("client_id", ""),
            data.get("client_secret", ""),
            token,
        )

    def _api(self, method: str, url: str, **kwargs) -> dict:
        session = self._session()
        kwargs.setdefault("timeout", _TIMEOUT)
        headers = kwargs.pop("headers", {}) or {}
        headers.update(session.auth_header())
        try:
            resp = requests.request(method, url, headers=headers, **kwargs)
        except requests.RequestException as exc:
            raise RuntimeError(f"ERROR: Google API request failed ({exc}).") from exc
        if resp.status_code >= 400:
            raise RuntimeError(f"ERROR: Google API {resp.status_code} on {url}.")
        # Persist a refreshed token so the next call starts fresh.
        if session.token.get("access_token"):
            data = store.load(self.id) or {}
            if data:
                data["token"] = session.token
                store.save(self.id, data)
        return resp.json()

    # -- Gmail ------------------------------------------------------------

    def gmail_search(self, query: str, limit: int = 10) -> str:
        limit = max(1, min(int(limit or 10), 50))
        data = self._api(
            "GET",
            f"{GMAIL_API}/users/me/messages",
            params={"q": query, "maxResults": limit},
        )
        messages = data.get("messages", [])
        if not messages:
            return "No messages found."
        lines = []
        for item in messages[:limit]:
            meta = self._api(
                "GET",
                f"{GMAIL_API}/users/me/messages/{item['id']}",
                params={"format": "metadata", "metadataHeaders": ["Subject", "From", "Date"]},
            )
            headers = {h["name"].lower(): h["value"] for h in meta.get("payload", {}).get("headers", [])}
            lines.append(
                f"{item['id']} | {headers.get('date', '?')} | {headers.get('from', '?')} | "
                f"{headers.get('subject', '(no subject)')}"
            )
        return "\n".join(lines)

    def gmail_read(self, message_id: str) -> str:
        message_id = (message_id or "").strip()
        if not message_id:
            raise RuntimeError("ERROR: message_id is required.")
        msg = self._api(
            "GET", f"{GMAIL_API}/users/me/messages/{message_id}", params={"format": "full"}
        )
        payload = msg.get("payload", {})
        headers = {h["name"].lower(): h["value"] for h in payload.get("headers", [])}
        body = _extract_plain_text(payload)
        head = (
            f"Subject: {headers.get('subject', '(no subject)')}\n"
            f"From: {headers.get('from', '?')}\n"
            f"Date: {headers.get('date', '?')}\n\n"
        )
        return head + body[:2000]

    def gmail_send(self, to: str, subject: str, body: str) -> str:
        to = (to or "").strip()
        if not to:
            raise RuntimeError("ERROR: 'to' is required.")
        mime = (
            f"To: {to}\r\n"
            f"Subject: {subject or ''}\r\n"
            "Content-Type: text/plain; charset=utf-8\r\n"
            "\r\n"
            f"{body or ''}"
        )
        raw = base64.urlsafe_b64encode(mime.encode("utf-8")).decode("ascii")
        result = self._api("POST", f"{GMAIL_API}/users/me/messages/send", json={"raw": raw})
        return f"Email sent (id {result.get('id', '?')})."

    # -- Calendar ----------------------------------------------------------

    def calendar_list(self, time_min: str = "", time_max: str = "", limit: int = 10) -> str:
        limit = max(1, min(int(limit or 10), 50))
        params = {
            "maxResults": limit,
            "singleEvents": True,
            "orderBy": "startTime",
            "timeMin": time_min.strip() or _now_utc_iso(),
        }
        if time_max and time_max.strip():
            params["timeMax"] = time_max.strip()
        data = self._api("GET", f"{CALENDAR_API}/calendars/primary/events", params=params)
        items = data.get("items", [])
        if not items:
            return "No upcoming events."
        lines = []
        for event in items[:limit]:
            start = event.get("start", {})
            when = start.get("dateTime") or start.get("date") or "?"
            lines.append(f"{when} — {event.get('summary', '(no title)')}")
        return "\n".join(lines)

    # -- Sheets ------------------------------------------------------------

    def sheets_read(self, spreadsheet_id: str, range_name: str) -> str:
        spreadsheet_id = (spreadsheet_id or "").strip()
        range_name = (range_name or "").strip()
        if not spreadsheet_id or not range_name:
            raise RuntimeError("ERROR: spreadsheet_id and range_name are required.")
        url = f"{SHEETS_API}/{urllib.parse.quote(spreadsheet_id, safe='')}/values/{urllib.parse.quote(range_name, safe='')}"
        data = self._api("GET", url)
        values = data.get("values", [])
        if not values:
            return "Range is empty."
        rows = []
        for row in values[:20]:
            rows.append("\t".join(str(cell) for cell in row[:10]))
        return "\n".join(rows)

    # -- Drive --------------------------------------------------------------

    def drive_list(self, query: str = "", limit: int = 10) -> str:
        limit = max(1, min(int(limit or 10), 50))
        params = {
            "pageSize": limit,
            "fields": "files(id,name,mimeType,modifiedTime)",
            "orderBy": "modifiedTime desc",
        }
        if query and query.strip():
            params["q"] = query.strip()
        data = self._api("GET", f"{DRIVE_API}/files", params=params)
        files = data.get("files", [])
        if not files:
            return "No files found."
        lines = []
        for item in files[:limit]:
            modified = item.get("modifiedTime", "?")
            try:
                modified = email.utils.format_datetime(
                    datetime.datetime.fromisoformat(modified.replace("Z", "+00:00"))
                )
            except (ValueError, TypeError):
                pass
            lines.append(f"{item.get('name', '?')} ({item.get('mimeType', '?')}, {modified})")
        return "\n".join(lines)


def _extract_plain_text(payload: dict) -> str:
    """Best-effort text/plain body from a Gmail message payload."""
    mime = payload.get("mimeType", "")
    body_data = (payload.get("body") or {}).get("data")
    if mime.startswith("text/plain") and body_data:
        return _b64decode(body_data)
    for part in payload.get("parts", []) or []:
        text = _extract_plain_text(part)
        if text:
            return text
    if body_data:  # single-part non-plain message, still decode something
        return _b64decode(body_data)
    return ""


def _b64decode(data: str) -> str:
    try:
        return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", errors="replace")
    except Exception:
        return ""


def _register() -> GoogleConnector:
    from zeline.connectors import register

    return register(GoogleConnector())


_register()
