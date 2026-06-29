from __future__ import annotations

import hashlib
import re
from typing import Optional

from ..core.config import settings


_CAMERA_PATH_RE = re.compile(r"^/internal/cameras/([^/]+)/")


def camera_worker_index(camera_id: str, worker_count: int) -> int:
    if worker_count <= 1:
        return 0
    override_map = getattr(settings, "CAMERA_SHARD_OVERRIDES", {}) or {}
    override = override_map.get(camera_id)
    if override is not None:
        try:
            return int(override) % worker_count
        except (TypeError, ValueError):
            pass
    digest = hashlib.sha256(camera_id.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=False) % worker_count


def camera_belongs_to_shard(camera_id: str, shard_index: int, shard_count: int) -> bool:
    if shard_count <= 1:
        return True
    if shard_index < 0 or shard_index >= shard_count:
        return False
    return camera_worker_index(camera_id, shard_count) == shard_index


def extract_camera_id_from_internal_path(path: str) -> Optional[str]:
    match = _CAMERA_PATH_RE.match(path.strip())
    if match is None:
        return None
    camera_id = match.group(1).strip()
    return camera_id or None
