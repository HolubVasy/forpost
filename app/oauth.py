"""Optional Google-OAuth bearer-token verification for the MCP surface.

Primary auth is the static X-API-Key (see auth.py). When OAUTH_ENABLED is set,
the /mcp guard ALSO accepts a Google-issued OAuth access token as a second,
stronger filter:

  1. the token is verified against Google's tokeninfo endpoint;
  2. its audience must equal OAUTH_CLIENT_ID, so a token minted for a different
     Google app cannot be replayed here (confused-deputy guard);
  3. the email must be verified and present in OAUTH_ALLOWED_EMAILS (an exact
     address, or a "@domain" rule that matches anyone at that domain).

Positive results are cached briefly (bounded by the token's own expiry) so we
don't call Google on every MCP request. Verification never raises for a bad
token - it returns None, and the caller rejects with 401.
"""

import os
import threading
import time

import httpx

TOKENINFO_URL = "https://oauth2.googleapis.com/tokeninfo"
_CACHE_TTL = 300  # max seconds a positive verification is trusted without re-check


def oauth_enabled() -> bool:
    return os.environ.get("OAUTH_ENABLED", "").strip().lower() in ("1", "true", "yes")


def _client_id() -> str:
    return os.environ.get("OAUTH_CLIENT_ID", "").strip()


def _allowed_rules() -> list[str]:
    raw = os.environ.get("OAUTH_ALLOWED_EMAILS", "")
    return [e.strip().lower() for e in raw.split(",") if e.strip()]


def _email_allowed(email: str) -> bool:
    email = (email or "").lower()
    for rule in _allowed_rules():
        if rule.startswith("@"):
            if email.endswith(rule):
                return True
        elif email == rule:
            return True
    return False


class _TokenCache:
    """token -> (expiry_epoch, email), pruned lazily on read."""

    def __init__(self):
        self._entries: dict[str, tuple[float, str]] = {}
        self._lock = threading.Lock()

    def get(self, token: str, now: float) -> str | None:
        with self._lock:
            entry = self._entries.get(token)
            if not entry:
                return None
            expiry, email = entry
            if now >= expiry:
                self._entries.pop(token, None)
                return None
            return email

    def put(self, token: str, email: str, expiry: float) -> None:
        with self._lock:
            self._entries[token] = (expiry, email)


_cache = _TokenCache()


def verify_bearer(token: str) -> str | None:
    """Return the verified, allow-listed email for a Google token, or None.

    None means any of: OAuth disabled, token missing/invalid/expired, audience
    mismatch, unverified email, or email not allow-listed. Blocking (one HTTP
    call to Google) - callers on the event loop must offload it to a thread.
    """
    if not token or not oauth_enabled():
        return None
    now = time.time()
    cached = _cache.get(token, now)
    if cached is not None:
        return cached
    try:
        resp = httpx.get(TOKENINFO_URL, params={"access_token": token}, timeout=5.0)
        if resp.status_code != 200:
            return None
        info = resp.json()
    except Exception:
        return None

    if info.get("aud") != _client_id():
        return None
    if str(info.get("email_verified")).lower() != "true":
        return None
    email = info.get("email")
    if not _email_allowed(email):
        return None

    try:
        exp = int(info.get("exp", "0"))
    except (TypeError, ValueError):
        exp = 0
    ttl_until = min(exp, now + _CACHE_TTL) if exp else now + _CACHE_TTL
    _cache.put(token, email, ttl_until)
    return email
