from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Any, Optional

import cv2
import numpy as np

from ..core.config import settings

logger = logging.getLogger(__name__)


@dataclass
class _PreviewPublishState:
    published_at: float = 0.0
    raw_signature: tuple[int, int] | None = None
    annotated_signature: tuple[int, int] | None = None
    people_payload: str | None = None
    status_payload: str | None = None


class LivePreviewStore:
    """Shared filesystem cache for live preview frames and overlays."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._state_lock = Lock()
        self._publish_state: dict[str, _PreviewPublishState] = {}

    def read_frame(self, camera_id: str, *, raw: bool = False) -> bytes | None:
        path = self._raw_frame_path(camera_id) if raw else self._annotated_frame_path(camera_id)
        if not path.exists() and not raw:
            path = self._raw_frame_path(camera_id)
        if not path.exists():
            return None
        try:
            return path.read_bytes()
        except OSError:
            return None

    def read_people(self, camera_id: str) -> dict[str, Any] | None:
        return self._read_json(self._people_path(camera_id))

    def read_status(self, camera_id: str) -> dict[str, Any] | None:
        return self._read_json(self._status_path(camera_id))

    def publish_snapshot(
        self,
        *,
        camera_id: str,
        raw_frame: np.ndarray | None,
        raw_jpeg: bytes | None,
        annotated_jpeg: bytes | None,
        people_payload: dict[str, Any],
        status_payload: dict[str, Any],
    ) -> dict[str, float | int]:
        started_at = time.perf_counter()
        camera_dir = self._camera_dir(camera_id)
        camera_dir.mkdir(parents=True, exist_ok=True)
        metrics: dict[str, float | int] = {
            "raw_encode_ms": 0.0,
            "raw_write_ms": 0.0,
            "annotated_write_ms": 0.0,
            "people_write_ms": 0.0,
            "status_write_ms": 0.0,
            "raw_bytes": 0,
            "annotated_bytes": len(annotated_jpeg or b""),
            "publish_skipped": 0,
        }
        min_interval_seconds = max(
            0.0,
            float(getattr(settings, "LIVE_PREVIEW_MIN_INTERVAL_SECONDS", 0.0)),
        )
        with self._state_lock:
            state = self._publish_state.setdefault(camera_id, _PreviewPublishState())
            if (
                min_interval_seconds > 0.0
                and state.published_at > 0.0
                and started_at - state.published_at < min_interval_seconds
            ):
                metrics["publish_skipped"] = 1
                metrics["publish_snapshot_ms"] = round((time.perf_counter() - started_at) * 1000.0, 1)
                return metrics

        raw_bytes = raw_jpeg
        if raw_bytes is None and raw_frame is not None and raw_frame.size > 0:
            encode_started_at = time.perf_counter()
            raw_bytes = self._encode_jpeg(raw_frame)
            metrics["raw_encode_ms"] = round((time.perf_counter() - encode_started_at) * 1000.0, 1)
        raw_signature = self._bytes_signature(raw_bytes)
        annotated_signature = self._bytes_signature(annotated_jpeg)
        people_payload_json = json.dumps(people_payload, ensure_ascii=False, separators=(",", ":"))
        status_payload_json = json.dumps(status_payload, ensure_ascii=False, separators=(",", ":"))

        with self._state_lock:
            state = self._publish_state.setdefault(camera_id, _PreviewPublishState())
            write_raw = raw_bytes is not None and raw_signature != state.raw_signature
            write_annotated = annotated_jpeg is not None and annotated_signature != state.annotated_signature
            write_people = people_payload_json != state.people_payload
            write_status = status_payload_json != state.status_payload

        if write_raw and raw_bytes is not None:
            write_started_at = time.perf_counter()
            self._write_bytes_atomic(self._raw_frame_path(camera_id), raw_bytes)
            metrics["raw_write_ms"] = round((time.perf_counter() - write_started_at) * 1000.0, 1)
            metrics["raw_bytes"] = len(raw_bytes)
        if write_annotated and annotated_jpeg is not None:
            annotated_started_at = time.perf_counter()
            self._write_bytes_atomic(self._annotated_frame_path(camera_id), annotated_jpeg)
            metrics["annotated_write_ms"] = round((time.perf_counter() - annotated_started_at) * 1000.0, 1)

        if write_people:
            people_started_at = time.perf_counter()
            self._write_text_atomic(self._people_path(camera_id), people_payload_json)
            metrics["people_write_ms"] = round((time.perf_counter() - people_started_at) * 1000.0, 1)
        if write_status:
            status_started_at = time.perf_counter()
            self._write_text_atomic(self._status_path(camera_id), status_payload_json)
            metrics["status_write_ms"] = round((time.perf_counter() - status_started_at) * 1000.0, 1)

        finished_at = time.perf_counter()
        with self._state_lock:
            state = self._publish_state.setdefault(camera_id, _PreviewPublishState())
            state.published_at = finished_at
            if raw_signature is not None:
                state.raw_signature = raw_signature
            if annotated_signature is not None:
                state.annotated_signature = annotated_signature
            if write_people:
                state.people_payload = people_payload_json
            if write_status:
                state.status_payload = status_payload_json
        metrics["publish_snapshot_ms"] = round((finished_at - started_at) * 1000.0, 1)
        return metrics

    def _camera_dir(self, camera_id: str) -> Path:
        return self.root / camera_id

    def _raw_frame_path(self, camera_id: str) -> Path:
        return self._camera_dir(camera_id) / "raw.jpg"

    def _annotated_frame_path(self, camera_id: str) -> Path:
        return self._camera_dir(camera_id) / "annotated.jpg"

    def _people_path(self, camera_id: str) -> Path:
        return self._camera_dir(camera_id) / "people.json"

    def _status_path(self, camera_id: str) -> Path:
        return self._camera_dir(camera_id) / "status.json"

    def _encode_jpeg(self, frame: np.ndarray) -> bytes | None:
        ok, encoded = cv2.imencode(
            ".jpg",
            frame,
            [cv2.IMWRITE_JPEG_QUALITY, max(10, min(100, int(settings.LIVE_FRAME_JPEG_QUALITY)))],
        )
        if not ok:
            return None
        return encoded.tobytes()

    def _read_json(self, path: Path) -> dict[str, Any] | None:
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    def _write_json_atomic(self, path: Path, payload: dict[str, Any]) -> None:
        self._write_text_atomic(
            path,
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        )

    def _bytes_signature(self, data: bytes | None) -> tuple[int, int] | None:
        if data is None:
            return None
        return (len(data), hash(data))

    def _write_bytes_atomic(self, path: Path, data: bytes) -> None:
        tmp_path = path.with_suffix(f"{path.suffix}.tmp")
        with open(tmp_path, "wb") as handle:
            handle.write(data)
        os.replace(tmp_path, path)

    def _write_text_atomic(self, path: Path, data: str) -> None:
        tmp_path = path.with_suffix(f"{path.suffix}.tmp")
        with open(tmp_path, "w", encoding="utf-8") as handle:
            handle.write(data)
        os.replace(tmp_path, path)


_live_preview_store: Optional[LivePreviewStore] = None


def get_live_preview_store() -> LivePreviewStore:
    global _live_preview_store
    if _live_preview_store is None:
        _live_preview_store = LivePreviewStore(settings.LIVE_PREVIEW_DIR)
    return _live_preview_store
