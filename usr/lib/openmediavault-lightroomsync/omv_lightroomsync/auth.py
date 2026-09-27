"""Adobe IMS OAuth 2.0 (authorization code flow) and token storage.

Adobe only accepts HTTPS redirect URIs, which a NAS on the LAN usually
cannot serve. The flow therefore redirects to a URI that is registered in
the Adobe Developer Console (default `https://localhost/`); the browser
fails to load it, and the user pastes the address bar URL back into the
plugin, from which the authorization code is extracted.
"""

import json
import os
import threading
import time
import urllib.parse
from typing import Optional

from .http import Transport

AUTHORIZE_URL = "https://ims-na1.adobelogin.com/ims/authorize/v2"
TOKEN_URL = "https://ims-na1.adobelogin.com/ims/token/v3"
SCOPES = "openid,AdobeID,lr_partner_apis,lr_partner_rendition_apis,offline_access"
# IMS default lifetime of a refresh token unless Adobe enabled renewal for
# the API key. Only used to warn the user ahead of time.
REFRESH_TOKEN_LIFETIME = 14 * 24 * 3600
# Refresh the access token this many seconds before it expires.
EXPIRY_MARGIN = 300


class AuthError(Exception):
    pass


def authorize_url(client_id: str, redirect_uri: str) -> str:
    return AUTHORIZE_URL + "?" + urllib.parse.urlencode({
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": SCOPES,
        "response_type": "code",
    })


def extract_code(pasted: str) -> str:
    """Accept either the full redirect URL or just the code."""
    pasted = pasted.strip()
    if not pasted:
        raise AuthError("Nothing was pasted")
    if "://" not in pasted and "?" not in pasted and "=" not in pasted:
        return pasted
    query = urllib.parse.parse_qs(urllib.parse.urlsplit(pasted).query)
    if "error" in query:
        desc = query.get("error_description", [""])[0]
        raise AuthError(f"Adobe returned an error: {query['error'][0]} {desc}".strip())
    if "code" not in query:
        raise AuthError("The pasted URL does not contain an authorization code")
    return query["code"][0]


class TokenStore:
    """Credentials and tokens, persisted as a root-only JSON file."""

    def __init__(self, path: str, transport: Optional[Transport] = None,
                 clock=time.time):
        self.path = path
        self.transport = transport or Transport(timeout=30)
        self.clock = clock
        self._lock = threading.Lock()
        self.data = {}
        if os.path.exists(path):
            with open(path, encoding="utf-8") as fh:
                self.data = json.load(fh)

    @property
    def client_id(self) -> str:
        return self.data.get("client_id", "")

    @property
    def authorized(self) -> bool:
        return bool(self.data.get("refresh_token") or self.data.get("access_token"))

    def save(self):
        os.makedirs(os.path.dirname(self.path), mode=0o700, exist_ok=True)
        tmp = self.path + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(self.data, fh, indent=2)
        os.replace(tmp, self.path)

    def clear(self):
        self.data = {}
        if os.path.exists(self.path):
            os.unlink(self.path)

    def _token_request(self, params: dict) -> dict:
        body = urllib.parse.urlencode(params).encode()
        resp = self.transport.request(
            "POST", TOKEN_URL,
            {"Content-Type": "application/x-www-form-urlencoded"}, body)
        try:
            payload = json.loads(resp.body or b"{}")
        except ValueError:
            payload = {}
        if resp.status != 200 or "access_token" not in payload:
            reason = payload.get("error_description") or payload.get("error") \
                or resp.body[:200].decode(errors="replace")
            raise AuthError(f"Token request failed (HTTP {resp.status}): {reason}")
        return payload

    def _apply(self, payload: dict):
        now = self.clock()
        self.data["access_token"] = payload["access_token"]
        # IMS v3 reports expires_in in seconds.
        self.data["access_expires_at"] = now + int(payload.get("expires_in", 3600))
        if payload.get("refresh_token"):
            if payload["refresh_token"] != self.data.get("refresh_token"):
                self.data["refresh_issued_at"] = now
            self.data["refresh_token"] = payload["refresh_token"]

    def complete(self, client_id: str, client_secret: str, redirect_uri: str,
                 pasted: str):
        code = extract_code(pasted)
        payload = self._token_request({
            "grant_type": "authorization_code",
            "client_id": client_id,
            "client_secret": client_secret,
            "code": code,
            "redirect_uri": redirect_uri,
        })
        self.data = {
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
        }
        self._apply(payload)
        self.save()

    def access_token(self, force_refresh: bool = False) -> str:
        with self._lock:
            if not self.authorized:
                raise AuthError("Not signed in to Adobe; complete the sign-in first")
            fresh = self.clock() < self.data.get("access_expires_at", 0) - EXPIRY_MARGIN
            if fresh and not force_refresh:
                return self.data["access_token"]
            if not self.data.get("refresh_token"):
                raise AuthError("The access token expired and there is no refresh "
                                "token; sign in to Adobe again")
            try:
                payload = self._token_request({
                    "grant_type": "refresh_token",
                    "client_id": self.data["client_id"],
                    "client_secret": self.data["client_secret"],
                    "refresh_token": self.data["refresh_token"],
                })
            except AuthError as e:
                raise AuthError(f"{e}. The Adobe sign-in has probably expired; "
                                "sign in again") from None
            self._apply(payload)
            self.save()
            return self.data["access_token"]

    def refresh_expires_at(self) -> Optional[float]:
        issued = self.data.get("refresh_issued_at")
        return issued + REFRESH_TOKEN_LIFETIME if issued else None
