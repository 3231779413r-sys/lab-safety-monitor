"""Compact binary payload helpers for RabbitMQ frame transport."""

from __future__ import annotations

import json
import struct
from typing import Any


_METADATA_LENGTH = struct.Struct("!I")


def pack_binary_payload(metadata: dict[str, Any], payload: bytes) -> bytes:
    metadata_bytes = json.dumps(
        metadata,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return _METADATA_LENGTH.pack(len(metadata_bytes)) + metadata_bytes + payload


def unpack_binary_payload(body: bytes) -> tuple[dict[str, Any], bytes]:
    if len(body) < _METADATA_LENGTH.size:
        raise ValueError("Binary payload is too short")
    (metadata_size,) = _METADATA_LENGTH.unpack(body[: _METADATA_LENGTH.size])
    metadata_end = _METADATA_LENGTH.size + metadata_size
    if metadata_end > len(body):
        raise ValueError("Binary payload metadata is truncated")
    metadata = json.loads(body[_METADATA_LENGTH.size : metadata_end].decode("utf-8"))
    if not isinstance(metadata, dict):
        raise ValueError("Binary payload metadata must be an object")
    return metadata, body[metadata_end:]
