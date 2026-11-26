from __future__ import annotations

import base64
import hmac
import json
import time
from hashlib import sha256

# M17 — the capability in each upload URL `create_build` hands an agent.
# The PUT route that consumes these is public and carries no other auth, so
# the token *is* the credential: HMAC-signed, bound to one job and one file
# slot, and expiring with the job's upload window. Single use is enforced by
# the slot's own state in `webapi`, not here.
#
# Keyed off INTERNAL_API_SECRET with a domain-separation prefix, so a token
# from one scheme can never verify as the other.
#
# Token shape: "<b64url(json{j, s, e})>.<b64url(hmac-sha256)>"


class UploadTokenError(RuntimeError):
    """Raised for a malformed, tampered, or expired upload token."""


def _b64u_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _b64u_decode(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def _key(secret: str) -> bytes:
    return sha256(b"notes-pipeline/upload-url/v1:" + secret.encode("utf-8")).digest()


def sign_upload_token(job_id: str, slot: int, expires_at: float, secret: str) -> str:
    payload = _b64u_encode(json.dumps({"j": job_id, "s": slot, "e": int(expires_at)}, separators=(",", ":")).encode())
    signature = hmac.new(_key(secret), payload.encode("ascii"), sha256).digest()
    return f"{payload}.{_b64u_encode(signature)}"


def verify_upload_token(token: str, secret: str) -> tuple[str, int]:
    """Return `(job_id, slot)` for a valid, unexpired token."""
    parts = token.split(".")
    if len(parts) != 2:
        raise UploadTokenError("Malformed upload token.")
    payload, signature = parts
    try:
        expected = hmac.new(_key(secret), payload.encode("ascii"), sha256).digest()
        if not hmac.compare_digest(_b64u_decode(signature), expected):
            raise UploadTokenError("Upload token signature does not match.")
        data = json.loads(_b64u_decode(payload))
        job_id, slot, expires_at = str(data["j"]), int(data["s"]), int(data["e"])
    except UploadTokenError:
        raise
    except Exception as exc:
        raise UploadTokenError("Malformed upload token.") from exc
    if time.time() > expires_at:
        raise UploadTokenError("Upload token has expired.")
    return job_id, slot
