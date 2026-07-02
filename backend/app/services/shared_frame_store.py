from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from dataclasses import dataclass
from typing import Any, Optional
import time
from fcntl import LOCK_EX, LOCK_UN, flock

import numpy as np

from ..core.config import settings

logger = logging.getLogger(__name__)


@dataclass
class SharedFrameReadResult:
    frame: Optional[np.ndarray]
    error_type: Optional[str] = None
    error_detail: Optional[str] = None
    metadata: Optional[dict[str, Any]] = None


@dataclass
class SharedFrameReleaseResult:
    deleted: bool
    remaining_consumers: list[str]
    metadata: Optional[dict[str, Any]]


class SharedFrameStore:
    def __init__(self, base_dir: Path) -> None:
        self.base_dir = base_dir
        self.base_dir.mkdir(parents=True, exist_ok=True)
        logger.info("Shared frame store ready base_dir=%s", self.base_dir)

    def build_frame_path(self, *, camera_id: str, request_id: str) -> Path:
        camera_dir = self.base_dir / camera_id
        camera_dir.mkdir(parents=True, exist_ok=True)
        return camera_dir / f"{request_id}.npy"

    def write_frame(
        self,
        *,
        camera_id: str,
        request_id: str,
        frame: np.ndarray,
        consumers_pending: list[str],
    ) -> str:
        frame_path = self.build_frame_path(camera_id=camera_id, request_id=request_id)
        temp_path = frame_path.with_suffix(".tmp")
        with open(temp_path, "wb") as handle:
            np.save(handle, frame, allow_pickle=False)
        temp_path.replace(frame_path)
        self._write_metadata(
            frame_path,
            {
                "camera_id": camera_id,
                "request_id": request_id,
                "frame_path": str(frame_path),
                "created_at": time.time(),
                "consumers_pending": list(consumers_pending),
                "consumers_done": [],
            },
        )
        return str(frame_path)

    def read_frame(self, frame_path: str | Path | None) -> Optional[np.ndarray]:
        return self.read_frame_result(frame_path).frame

    def read_frame_result(self, frame_path: str | Path | None) -> SharedFrameReadResult:
        if not frame_path:
            return SharedFrameReadResult(
                frame=None,
                error_type="missing_shared_frame",
                error_detail="empty_frame_path",
            )
        path = Path(frame_path)
        metadata = self.get_metadata(path)
        try:
            with open(path, "rb") as handle:
                frame = np.load(handle, allow_pickle=False)
        except FileNotFoundError:
            error_type = "stale_shared_frame" if metadata else "missing_shared_frame"
            logger.warning(
                "Shared frame file is missing path=%s camera=%s request_id=%s error_type=%s delete_reason=%s deleted_at=%s",
                path,
                str((metadata or {}).get("camera_id") or ""),
                str((metadata or {}).get("request_id") or ""),
                error_type,
                str((metadata or {}).get("delete_reason") or ""),
                (metadata or {}).get("deleted_at"),
            )
            return SharedFrameReadResult(
                frame=None,
                error_type=error_type,
                error_detail="file_missing",
                metadata=metadata,
            )
        except Exception:
            logger.exception("Failed to read shared frame file: %s", path)
            return SharedFrameReadResult(
                frame=None,
                error_type="decode_failed",
                error_detail="read_exception",
                metadata=metadata,
            )
        if not isinstance(frame, np.ndarray):
            logger.warning("Shared frame file did not contain an ndarray: %s", path)
            return SharedFrameReadResult(
                frame=None,
                error_type="decode_failed",
                error_detail="payload_not_ndarray",
                metadata=metadata,
            )
        return SharedFrameReadResult(frame=frame, metadata=metadata)

    def frame_exists(self, frame_path: str | Path | None) -> bool:
        if not frame_path:
            return False
        return Path(frame_path).exists()

    def retain_frame(self, frame_path: str | Path | None, consumer: str) -> bool:
        if not frame_path:
            return False
        path = Path(frame_path)
        with self._locked_metadata_file(path, create_if_missing=False) as handle:
            if handle is None:
                return False
            metadata = self._read_metadata_locked(handle)
            if metadata is None:
                return False
            consumers_pending = [
                str(item) for item in list(metadata.get("consumers_pending") or [])
            ]
            if consumer not in consumers_pending:
                consumers_pending.append(consumer)
                metadata["consumers_pending"] = consumers_pending
                self._write_metadata_locked(handle, metadata)
        return True

    def release_frame(
        self,
        frame_path: str | Path | None,
        *,
        consumer: str,
        reason: str,
    ) -> SharedFrameReleaseResult:
        if not frame_path:
            return SharedFrameReleaseResult(
                deleted=False,
                remaining_consumers=[],
                metadata=None,
            )
        path = Path(frame_path)
        metadata_path = self._metadata_path(path)
        deleted = False
        metadata: dict[str, Any] | None
        remaining_consumers: list[str]
        with self._locked_metadata_file(path, create_if_missing=False) as handle:
            if handle is None:
                return SharedFrameReleaseResult(
                    deleted=False,
                    remaining_consumers=[],
                    metadata=None,
                )
            metadata = self._read_metadata_locked(handle)
            if metadata is None:
                return SharedFrameReleaseResult(
                    deleted=False,
                    remaining_consumers=[],
                    metadata=None,
                )
            consumers_pending = [
                str(item) for item in list(metadata.get("consumers_pending") or [])
            ]
            consumers_done = [
                str(item) for item in list(metadata.get("consumers_done") or [])
            ]
            if consumer in consumers_pending:
                consumers_pending.remove(consumer)
            if consumer not in consumers_done:
                consumers_done.append(consumer)
            metadata["consumers_pending"] = consumers_pending
            metadata["consumers_done"] = consumers_done
            metadata["last_release_reason"] = reason
            metadata["last_release_at"] = time.time()
            metadata["last_release_consumer"] = consumer
            remaining_consumers = list(consumers_pending)
            if consumers_pending:
                self._write_metadata_locked(handle, metadata)
            else:
                metadata["deleted_at"] = time.time()
                metadata["delete_reason"] = reason
                self._write_metadata_locked(handle, metadata)
                deleted = True
        logger.debug(
            "Released shared frame camera=%s request_id=%s consumer=%s reason=%s deleted=%s remaining=%s",
            str((metadata or {}).get("camera_id") or ""),
            str((metadata or {}).get("request_id") or ""),
            consumer,
            reason,
            deleted,
            remaining_consumers,
        )
        if deleted:
            self._delete_paths(
                path,
                camera_id=str((metadata or {}).get("camera_id") or ""),
                request_id=str((metadata or {}).get("request_id") or ""),
                reason=reason,
            )
        return SharedFrameReleaseResult(
            deleted=deleted,
            remaining_consumers=remaining_consumers,
            metadata=metadata,
        )

    def delete_frame(self, frame_path: str | Path | None, *, reason: str = "force_delete") -> None:
        if not frame_path:
            return
        path = Path(frame_path)
        metadata = self.get_metadata(path)
        if metadata is not None:
            with self._locked_metadata_file(path) as handle:
                if handle is not None:
                    metadata = self._read_metadata_locked(handle) or metadata
                    metadata["deleted_at"] = time.time()
                    metadata["delete_reason"] = reason
                    self._write_metadata_locked(handle, metadata)
        self._delete_paths(
            path,
            camera_id=str((metadata or {}).get("camera_id") or ""),
            request_id=str((metadata or {}).get("request_id") or ""),
            reason=reason,
        )

    def cleanup_stale_files(self, *, max_age_seconds: float) -> int:
        removed = 0
        if max_age_seconds <= 0:
            return removed
        deadline = time.time() - max_age_seconds
        for path in self.base_dir.glob("*/*.npy"):
            try:
                metadata = self.get_metadata(path)
                created_at = float((metadata or {}).get("created_at") or 0.0)
                modified_at = path.stat().st_mtime
                last_seen_at = max(created_at, modified_at)
                if last_seen_at < deadline:
                    self.delete_frame(path, reason="stale_cleanup")
                    removed += 1
            except FileNotFoundError:
                continue
            except Exception:
                logger.warning("Failed to cleanup shared frame file: %s", path, exc_info=True)
        for metadata_path in self.base_dir.glob("*/*.json"):
            try:
                metadata = self._read_metadata_path(metadata_path)
                if metadata is None:
                    modified_at = metadata_path.stat().st_mtime
                    if modified_at < deadline:
                        metadata_path.unlink(missing_ok=True)
                    continue
                deleted_at = float(
                    metadata.get("deleted_at")
                    or metadata.get("last_release_at")
                    or metadata.get("created_at")
                    or 0.0
                )
                if deleted_at and deleted_at < deadline:
                    metadata_path.unlink(missing_ok=True)
            except FileNotFoundError:
                continue
            except Exception:
                logger.warning("Failed to cleanup shared frame metadata: %s", metadata_path, exc_info=True)
        return removed

    def get_metadata(self, frame_path: str | Path | None) -> Optional[dict[str, Any]]:
        if not frame_path:
            return None
        path = Path(frame_path)
        return self._read_metadata_path(self._metadata_path(path))

    def _read_metadata_path(self, metadata_path: Path) -> Optional[dict[str, Any]]:
        if not metadata_path.exists():
            return None
        with self._locked_metadata_path(metadata_path, create_if_missing=False) as handle:
            if handle is None:
                return None
            return self._read_metadata_locked(handle)

    def _metadata_path(self, frame_path: Path) -> Path:
        return frame_path.with_suffix(".json")

    def _write_metadata(self, frame_path: Path, metadata: dict[str, Any]) -> None:
        with self._locked_metadata_file(frame_path) as handle:
            self._write_metadata_locked(handle, metadata)

    def _locked_metadata_file(self, frame_path: Path, *, create_if_missing: bool = True):
        metadata_path = self._metadata_path(frame_path)
        return self._locked_metadata_path(metadata_path, create_if_missing=create_if_missing)

    def _locked_metadata_path(self, metadata_path: Path, *, create_if_missing: bool = True):
        if not create_if_missing and not metadata_path.exists():
            return _NullLockedMetadataHandle()
        metadata_path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(metadata_path, "a+", encoding="utf-8")
        flock(handle.fileno(), LOCK_EX)
        handle.seek(0)
        return _LockedMetadataHandle(handle)

    def _read_metadata_locked(self, handle) -> Optional[dict[str, Any]]:
        handle.seek(0)
        raw = handle.read().strip()
        if not raw:
            return None
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("Shared frame metadata is invalid JSON")
            return None
        if not isinstance(value, dict):
            return None
        return value

    def _write_metadata_locked(self, handle, metadata: dict[str, Any]) -> None:
        handle.seek(0)
        handle.truncate()
        json.dump(metadata, handle, ensure_ascii=False, separators=(",", ":"))
        handle.flush()
        os.fsync(handle.fileno())

    def _delete_paths(
        self,
        frame_path: Path,
        *,
        camera_id: str,
        request_id: str,
        reason: str,
    ) -> None:
        logger.debug(
            "Deleting shared frame camera=%s request_id=%s frame_path=%s reason=%s",
            camera_id,
            request_id,
            frame_path,
            reason,
        )
        try:
            frame_path.unlink(missing_ok=True)
        except Exception:
            logger.warning("Failed to delete shared frame file: %s", frame_path, exc_info=True)


class _LockedMetadataHandle:
    def __init__(self, handle) -> None:
        self._handle = handle

    def __enter__(self):
        return self._handle

    def __exit__(self, exc_type, exc, tb) -> None:
        try:
            self._handle.flush()
        except Exception:
            pass
        try:
            flock(self._handle.fileno(), LOCK_UN)
        finally:
            self._handle.close()


class _NullLockedMetadataHandle:
    def __enter__(self):
        return None

    def __exit__(self, exc_type, exc, tb) -> None:
        return None


_shared_frame_store: SharedFrameStore | None = None


def get_shared_frame_store() -> SharedFrameStore:
    global _shared_frame_store
    if _shared_frame_store is None:
        _shared_frame_store = SharedFrameStore(Path(settings.SHARED_FRAME_DIR))
    return _shared_frame_store
