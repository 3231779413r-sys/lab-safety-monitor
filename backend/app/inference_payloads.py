"""Helpers for serializing inference tasks and results."""

from __future__ import annotations

import base64
from datetime import datetime
from typing import Any
from .telemetry import clone_telemetry

import cv2
import numpy as np

from .core.config import settings


def encode_frame_to_jpeg_bytes(
    frame: np.ndarray,
    *,
    quality: int | None = None,
) -> bytes:
    jpeg_quality = int(
        quality
        if quality is not None
        else getattr(settings, "INFERENCE_FRAME_JPEG_QUALITY", 80)
    )
    ok, encoded = cv2.imencode(
        ".jpg",
        frame,
        [cv2.IMWRITE_JPEG_QUALITY, max(10, min(100, jpeg_quality))],
    )
    if not ok:
        raise ValueError("Failed to encode frame as JPEG")
    return encoded.tobytes()


def decode_frame_from_jpeg_bytes(data: bytes) -> np.ndarray | None:
    if not data:
        return None
    array = np.frombuffer(data, dtype=np.uint8)
    frame = cv2.imdecode(array, cv2.IMREAD_COLOR)
    return frame


def encode_bytes(data: bytes | None) -> str | None:
    if not data:
        return None
    return base64.b64encode(data).decode("ascii")


def decode_bytes(data: str | None) -> bytes | None:
    if not data:
        return None
    return base64.b64decode(data.encode("ascii"))


def build_frame_task_metadata(
    *,
    request_id: str,
    camera_id: str,
    video_source: str,
    frame_path: str | None,
    frame_jpeg: bytes | None = None,
    inference_options: dict[str, Any] | None = None,
    result_queue: str | None = None,
    submitted_at: datetime | None = None,
    telemetry: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "request_id": request_id,
        "camera_id": camera_id,
        "video_source": video_source,
        "frame_path": frame_path or "",
        "frame_jpeg_b64": encode_bytes(frame_jpeg),
        "inference_options": dict(inference_options or {}),
        "result_queue": result_queue or settings.RABBITMQ_RESULT_QUEUE,
        "submitted_at": (submitted_at or datetime.now()).isoformat(),
        "telemetry": clone_telemetry(telemetry),
    }


def build_frame_task_message(
    *,
    request_id: str,
    camera_id: str,
    video_source: str,
    frame_path: str | None,
    frame_jpeg: bytes | None = None,
    inference_options: dict[str, Any] | None = None,
    result_queue: str | None = None,
    submitted_at: datetime | None = None,
    telemetry: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return build_frame_task_metadata(
        request_id=request_id,
        camera_id=camera_id,
        video_source=video_source,
        frame_path=frame_path,
        frame_jpeg=frame_jpeg,
        inference_options=inference_options,
        result_queue=result_queue,
        submitted_at=submitted_at,
        telemetry=telemetry,
    )


def parse_frame_task_message(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "request_id": str(payload.get("request_id") or ""),
        "camera_id": str(payload.get("camera_id") or ""),
        "video_source": str(payload.get("video_source") or ""),
        "frame_path": str(payload.get("frame_path") or ""),
        "frame_jpeg": decode_bytes(payload.get("frame_jpeg_b64")),
        "inference_options": dict(payload.get("inference_options") or {}),
        "result_queue": str(payload.get("result_queue") or settings.RABBITMQ_RESULT_QUEUE),
        "submitted_at": payload.get("submitted_at"),
        "telemetry": clone_telemetry(payload.get("telemetry")),
    }


def build_result_payload(
    *,
    request_id: str,
    camera_id: str,
    video_source: str,
    detections: dict[str, Any],
    submitted_at: str | None = None,
    completed_at: datetime | None = None,
    error: str | None = None,
    error_type: str | None = None,
    error_detail: str | None = None,
    frame_path: str | None = None,
    telemetry: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "request_id": request_id,
        "camera_id": camera_id,
        "video_source": video_source,
        "detections": detections,
        "frame_path": frame_path or "",
        "completed_at": (completed_at or datetime.now()).isoformat(),
        "telemetry": clone_telemetry(telemetry),
    }
    if submitted_at:
        payload["submitted_at"] = submitted_at
    if error:
        payload["error"] = error
    if error_type:
        payload["error_type"] = error_type
    if error_detail:
        payload["error_detail"] = error_detail
    return payload
