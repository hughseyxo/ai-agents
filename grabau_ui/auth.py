"""Shared-passcode session auth for the Villa Grabau PWA.

Replaces Traefik HTTP basic auth on this host. Basic auth is unusable here in
practice: iOS in-app browsers (WhatsApp/Instagram/Mail webviews) never render
the credentials prompt and just show Traefik's bare "401 Unauthorized", and an
installed Home Screen PWA in standalone mode has the same problem. A normal
HTML form works everywhere and can be saved to the iOS keychain.

One shared passcode (``GRABAU_PASSCODE``) for everyone; there are no accounts.
On success the browser gets an HMAC-signed, HttpOnly session cookie.

**Fails closed:** with no passcode configured the app serves 503 rather than
serving the budget to the open internet.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time
from collections import defaultdict

COOKIE_NAME = "grabau_session"
PASSCODE_ENV = "GRABAU_PASSCODE"
SECRET_ENV = "GRABAU_SECRET"

MAX_AGE_SECONDS = 365 * 24 * 3600  # long-lived: friends shouldn't re-login monthly

# Brute-force throttle. The passcode is short and human-shareable, and the site
# is on the public internet, so cap guesses per client IP.
MAX_ATTEMPTS = 10
THROTTLE_WINDOW_SECONDS = 15 * 60

_attempts: dict[str, list[float]] = defaultdict(list)

# Process-lifetime fallback secret, used only when GRABAU_SECRET is unset. A
# restart then invalidates existing sessions, which is survivable (users just
# log in again) and strictly better than shipping a hardcoded default.
_fallback_secret = secrets.token_bytes(32)


def get_passcode() -> str | None:
    """The configured shared passcode, or None if the app is unconfigured."""
    return os.environ.get(PASSCODE_ENV) or None


def get_secret() -> bytes:
    configured = os.environ.get(SECRET_ENV)
    return configured.encode() if configured else _fallback_secret


def _sign(secret: bytes, issued_at: int) -> str:
    return hmac.new(secret, str(issued_at).encode(), hashlib.sha256).hexdigest()


def make_token(secret: bytes, issued_at: int | None = None) -> str:
    issued_at = int(time.time()) if issued_at is None else issued_at
    return f"{issued_at}.{_sign(secret, issued_at)}"


def verify_token(secret: bytes, token: str | None) -> bool:
    if not token or "." not in token:
        return False
    raw_issued, _, signature = token.partition(".")
    try:
        issued_at = int(raw_issued)
    except ValueError:
        return False
    if not hmac.compare_digest(signature, _sign(secret, issued_at)):
        return False
    return 0 < time.time() - issued_at < MAX_AGE_SECONDS


def check_passcode(candidate: str) -> bool:
    passcode = get_passcode()
    if not passcode:
        return False
    return hmac.compare_digest(candidate.strip(), passcode)


# --- throttle ---

def _prune(client_ip: str, now: float) -> list[float]:
    recent = [t for t in _attempts[client_ip] if now - t < THROTTLE_WINDOW_SECONDS]
    _attempts[client_ip] = recent
    return recent


def is_throttled(client_ip: str) -> bool:
    return len(_prune(client_ip, time.time())) >= MAX_ATTEMPTS


def record_failure(client_ip: str) -> None:
    _attempts[client_ip].append(time.time())


def clear_failures(client_ip: str) -> None:
    _attempts.pop(client_ip, None)


def reset_throttle() -> None:
    _attempts.clear()


def client_ip(request) -> str:
    """Real client IP behind Cloudflare + Traefik."""
    for header in ("cf-connecting-ip", "x-forwarded-for"):
        value = request.headers.get(header)
        if value:
            return value.split(",")[0].strip()
    return request.client.host if request.client else "unknown"
