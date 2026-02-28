from __future__ import annotations

import hashlib
from typing import Protocol


_SEED_BYTES = 8
_PART_SEPARATOR = b"|"


class _HashUpdater(Protocol):
    def update(self, data: bytes) -> object: ...


def _framed_part_bytes(part: object) -> bytes:
    return repr(part).encode("utf-8") + _PART_SEPARATOR


def _update_hash_with_part(seed_hash: _HashUpdater, part: object) -> None:
    # Keep the exact repr+separator framing so seeds remain stable across callers.
    seed_hash.update(_framed_part_bytes(part))


def _seed_from_digest_prefix(seed_digest: bytes) -> int:
    digest_prefix = seed_digest[:_SEED_BYTES]
    return int.from_bytes(digest_prefix, "big", signed=False)


def _stable_seed(*parts: object) -> int:
    seed_hash = hashlib.sha1()
    for part in parts:
        _update_hash_with_part(seed_hash, part)
    return _seed_from_digest_prefix(seed_hash.digest())
