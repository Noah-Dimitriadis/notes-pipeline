from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .store import Store

_CHUNK_SIZE = 1 << 20  # 1 MiB


def _hash_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(_CHUNK_SIZE), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def cache_key(stage: str, version: str, inputs: list[Path], params: dict) -> str:
    """sha256(stage + stage_version + sorted(params) + hashes of inputs).

    Each stage declares only its own inputs — callers must not pass paths
    that stage does not actually depend on, or unrelated changes will
    needlessly bust its cache entry.
    """
    hasher = hashlib.sha256()
    hasher.update(stage.encode("utf-8"))
    hasher.update(b"\0")
    hasher.update(version.encode("utf-8"))
    hasher.update(b"\0")
    hasher.update(json.dumps(params, sort_keys=True, default=str).encode("utf-8"))
    hasher.update(b"\0")
    for input_path in inputs:
        hasher.update(_hash_file(input_path).encode("utf-8"))
        hasher.update(b"\0")
    return hasher.hexdigest()


class Cache:
    """Content-hash cache. Metadata (which key maps to which file) lives in
    the Store's stage_cache table; the payload itself is a plain file under
    cache_root, so it can be read by hand while debugging."""

    def __init__(self, store: Store, cache_root: Path):
        self.store = store
        self.cache_root = cache_root
        self.cache_root.mkdir(parents=True, exist_ok=True)

    def get(self, key: str) -> Path | None:
        path = self.store.get_cache_entry(key)
        if path is not None and path.exists():
            return path
        return None

    def put(
        self,
        key: str,
        data: bytes | str,
        *,
        stage: str,
        lecture_id: str,
        meta: dict | None = None,
    ) -> Path:
        payload_path = self.cache_root / key
        if isinstance(data, bytes):
            payload_path.write_bytes(data)
        else:
            payload_path.write_text(data, encoding="utf-8")
        self.store.put_cache_entry(
            key,
            stage=stage,
            lecture_id=lecture_id,
            payload_path=payload_path,
            meta=meta or {},
        )
        return payload_path
