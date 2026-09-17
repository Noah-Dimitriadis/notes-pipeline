from __future__ import annotations

import base64
import hmac
import time
from hashlib import sha256

# M13 (§14.2) — the trust boundary between `web` and `api` on the same
# Docker-internal network. PLAN.md leaves *how* `web` asserts "this request
# is on behalf of user X" to the implementation; a bare `X-User-Email`
# header would be exactly the "trusts a client-supplied user id" that M13's
# own acceptance criteria rule out, so `web` instead mints one of these
# short-lived signed tokens *after* validating its own Auth.js session, and
# `api` verifies the signature (not just decodes the payload) before
# trusting the email. Both sides share `INTERNAL_API_SECRET` (deploy/.env).
#
# Token shape: "<b64url(email)>.<b64url(issued_at)>.<b64url(hmac-sha256)>"

_DEFAULT_MAX_AGE_SECONDS = 60.0


class InternalAuthError(RuntimeError):
    """Raised for a malformed, expired, or unsigned internal token."""


def _b64u_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _b64u_decode(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding)


def _sign(email: str, issued_at: str, secret: str) -> bytes:
    message = f"{email}.{issued_at}".encode("utf-8")
    return hmac.new(secret.encode("utf-8"), message, sha256).digest()


def sign_internal_token(email: str, secret: str) -> str:
    """Mint a token asserting `email` is the caller, signed with `secret`.
    Called by `web` only after it has independently verified an active
    Auth.js session for that email."""
    issued_at = str(int(time.time()))
    signature = _sign(email, issued_at, secret)
    return ".".join([_b64u_encode(email.encode("utf-8")), _b64u_encode(issued_at.encode("utf-8")), _b64u_encode(signature)])


def verify_internal_token(token: str, secret: str, *, max_age_seconds: float = _DEFAULT_MAX_AGE_SECONDS) -> str:
    """Verify a token minted by `sign_internal_token` and return the email
    it asserts. Raises `InternalAuthError` on a bad signature, malformed
    token, or one older than `max_age_seconds` (replay window)."""
    parts = token.split(".")
    if len(parts) != 3:
        raise InternalAuthError("Malformed internal token.")
    try:
        email = _b64u_decode(parts[0]).decode("utf-8")
        issued_at = _b64u_decode(parts[1]).decode("utf-8")
        signature = _b64u_decode(parts[2])
    except Exception as exc:
        raise InternalAuthError("Malformed internal token.") from exc

    expected = _sign(email, issued_at, secret)
    if not hmac.compare_digest(signature, expected):
        raise InternalAuthError("Internal token signature does not match.")

    age = time.time() - int(issued_at)
    if age > max_age_seconds or age < -5:  # small tolerance for clock skew
        raise InternalAuthError("Internal token has expired.")

    return email
