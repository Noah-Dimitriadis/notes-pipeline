from __future__ import annotations

import base64
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_NONCE_BYTES = 12
_KEY_BYTES = 32  # AES-256


class KeyEncryptionError(RuntimeError):
    """Raised when the master key is misconfigured, or a ciphertext won't decrypt."""


def generate_master_key() -> str:
    """A fresh base64-encoded 32-byte key, for populating `deploy/.env`."""
    return base64.b64encode(os.urandom(_KEY_BYTES)).decode("ascii")


def _load_key(master_key: str) -> bytes:
    try:
        raw = base64.b64decode(master_key, validate=True)
    except Exception as exc:
        raise KeyEncryptionError("Master key must be base64-encoded.") from exc
    if len(raw) != _KEY_BYTES:
        raise KeyEncryptionError(
            f"Master key must decode to exactly {_KEY_BYTES} bytes (AES-256); "
            f"got {len(raw)}. Generate one with crypto.generate_master_key()."
        )
    return raw


def encrypt_key(plaintext: str, master_key: str) -> tuple[str, str]:
    """Encrypt a user's Anthropic API key for storage. Returns
    (ciphertext, nonce), both base64-encoded strings — the shape `users.
    encrypted_anthropic_key` / `users.key_nonce` store directly (§14.3)."""
    key = _load_key(master_key)
    nonce = os.urandom(_NONCE_BYTES)
    ciphertext = AESGCM(key).encrypt(nonce, plaintext.encode("utf-8"), None)
    return (
        base64.b64encode(ciphertext).decode("ascii"),
        base64.b64encode(nonce).decode("ascii"),
    )


def decrypt_key(ciphertext: str, nonce: str, master_key: str) -> str:
    """Inverse of `encrypt_key`. Raises `KeyEncryptionError` on a bad master
    key or tampered/corrupt ciphertext rather than returning garbage."""
    key = _load_key(master_key)
    try:
        plaintext = AESGCM(key).decrypt(
            base64.b64decode(nonce), base64.b64decode(ciphertext), None
        )
    except Exception as exc:
        raise KeyEncryptionError("Failed to decrypt stored API key.") from exc
    return plaintext.decode("utf-8")
