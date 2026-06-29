from dataclasses import dataclass
import asyncio
import subprocess
import tempfile
from pathlib import Path
from typing import Any, List, Optional
from datetime import datetime
import logging

import cv2
import numpy as np

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.danger_events import match_danger_event_types
from ..core.config import settings
from ..models.event import ComplianceEvent
from .object_storage import StoredObject, get_object_storage


logger = logging.getLogger(__name__)


@dataclass
class SnapshotSaveResult:
    storage: str
    snapshot_path: Optional[str] = None
    bucket: Optional[str] = None
    object_key: Optional[str] = None
    content_type: Optional[str] = None
    size_bytes: Optional[int] = None


@dataclass
class VideoSaveResult:
    storage: str
    video_path: Optional[str] = None
    bucket: Optional[str] = None
    object_key: Optional[str] = None
    content_type: Optional[str] = None
    size_bytes: Optional[int] = None


class EventService:
    """Service for persisting compliance events."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def create_event(
        self,
        person_id: Optional[str],
        track_id: Optional[int],
        timestamp: datetime,
        video_source: str,
        frame_number: int,
        detected_ppe: List[str],
        missing_ppe: List[str],
        is_violation: bool,
        detection_confidence: Optional[dict] = None,
        snapshot_overlay: Optional[dict[str, Any]] = None,
        snapshot_path: Optional[str] = None,
        snapshot_storage: Optional[str] = None,
        snapshot_bucket: Optional[str] = None,
        snapshot_object_key: Optional[str] = None,
        snapshot_content_type: Optional[str] = None,
        snapshot_size_bytes: Optional[int] = None,
        start_frame: Optional[int] = None,
        action_violations: Optional[List[str]] = None,
        camera_id: Optional[str] = None,
        event_id: Optional[str] = None,
        danger_event_types: Optional[List[str]] = None,
        person_name: Optional[str] = None,
        camera_ids: Optional[List[str]] = None,
        camera_name: Optional[str] = None,
        is_ongoing: bool = True,
        end_timestamp: Optional[datetime] = None,
        duration_frames: int = 1,
    ) -> ComplianceEvent:
        """Create and persist a compliance event."""
        event_data = {
            "person_id": person_id,
            "person_name": person_name,
            "track_id": track_id,
            "timestamp": timestamp,
            "video_source": video_source,
            "camera_id": camera_id,
            "camera_ids": camera_ids or ([] if camera_id is None else [camera_id]),
            "camera_name": camera_name,
            "frame_number": frame_number,
            "detected_ppe": detected_ppe,
            "missing_ppe": missing_ppe,
            "action_violations": action_violations or [],
            "danger_event_types": danger_event_types if danger_event_types is not None else match_danger_event_types(missing_ppe, action_violations or []),
            "is_violation": is_violation,
            "detection_confidence": detection_confidence or {},
            "snapshot_overlay": snapshot_overlay,
            "snapshot_path": snapshot_path,
            "snapshot_storage": snapshot_storage,
            "snapshot_bucket": snapshot_bucket,
            "snapshot_object_key": snapshot_object_key,
            "snapshot_content_type": snapshot_content_type,
            "snapshot_size_bytes": snapshot_size_bytes,
            "start_frame": start_frame or frame_number,
            "is_ongoing": is_ongoing,
            "end_timestamp": end_timestamp,
            "duration_frames": duration_frames,
        }
        if event_id is not None:
            event_data["id"] = event_id

        event = ComplianceEvent(**event_data)
        self.session.add(event)
        return event

    async def close_event(
        self,
        event_id: str,
        end_frame: int,
        end_timestamp: datetime,
        final_missing_ppe: Optional[List[str]] = None,
    ) -> Optional[ComplianceEvent]:
        """
        Close an ongoing event when the violation ends.

        Updates the event with end_frame, end_timestamp, duration,
        and optionally updates the missing_ppe list with the accumulated union.
        """
        result = await self.session.execute(
            select(ComplianceEvent).where(ComplianceEvent.id == event_id)
        )
        event = result.scalar_one_or_none()

        if event:
            event.end_frame = end_frame
            event.end_timestamp = end_timestamp
            event.is_ongoing = False

            # Update missing_ppe with the full accumulated set if provided
            if final_missing_ppe:
                event.missing_ppe = final_missing_ppe
                event.danger_event_types = match_danger_event_types(final_missing_ppe, event.action_violations or [])

            if event.start_frame is not None:
                event.duration_frames = end_frame - event.start_frame + 1
            else:
                event.duration_frames = end_frame - event.frame_number + 1

        return event

    async def get_event(self, event_id: str) -> Optional[ComplianceEvent]:
        """Get an event by ID."""
        result = await self.session.execute(
            select(ComplianceEvent).where(ComplianceEvent.id == event_id)
        )
        return result.scalar_one_or_none()

    async def update_event_person(
        self,
        event_id: str,
        *,
        person_id: str,
        person_name: Optional[str] = None,
    ) -> Optional[ComplianceEvent]:
        """Backfill a previously created event with a resolved person identity."""
        event = await self.get_event(event_id)
        if event is None:
            return None
        event.person_id = person_id
        if person_name is not None:
            event.person_name = person_name
        return event

    async def save_snapshot(
        self,
        frame,
        *,
        event_id: str,
        timestamp: datetime,
        camera_id: Optional[str],
        quality: int = 85,
    ) -> SnapshotSaveResult:
        """Upload a violation snapshot to MinIO, falling back to local storage."""
        try:
            storage = get_object_storage()
            object_key = storage.build_snapshot_key(
                event_id=event_id,
                timestamp=timestamp,
                camera_id=camera_id,
            )
            stored = storage.upload_jpeg_frame(frame, object_key=object_key, quality=quality)
            return SnapshotSaveResult(
                storage="minio",
                bucket=stored.bucket,
                object_key=stored.object_key,
                content_type=stored.content_type,
                size_bytes=stored.size_bytes,
            )
        except Exception as exc:
            logger.warning("MinIO snapshot upload failed, falling back to local file: %s", exc)
            settings.SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)
            filename = f"{timestamp:%Y%m%d_%H%M%S}_{event_id}.jpg"
            file_path = settings.SNAPSHOTS_DIR / filename
            ok = cv2.imwrite(str(file_path), frame, [cv2.IMWRITE_JPEG_QUALITY, quality])
            if not ok:
                raise RuntimeError("Failed to save snapshot to local storage") from exc
            return SnapshotSaveResult(
                storage="local",
                snapshot_path=str(file_path),
                content_type="image/jpeg",
                size_bytes=Path(file_path).stat().st_size,
            )

    def _encode_video_clip(self, frames: list[np.ndarray], fps: int, output_path: str) -> None:
        if not frames:
            raise RuntimeError("No frames available for video clip")
        first_frame = np.asarray(frames[0])
        if first_frame.ndim != 3 or first_frame.shape[2] != 3:
            raise RuntimeError("Invalid frame shape for video clip")
        height, width = first_frame.shape[:2]

        cmd = [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "bgr24",
            "-s",
            f"{width}x{height}",
            "-r",
            str(max(1, fps)),
            "-i",
            "pipe:0",
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-pix_fmt",
            "yuv420p",
            output_path,
        ]
        process = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        assert process.stdin is not None
        try:
            for frame in frames:
                array = np.asarray(frame)
                if array.shape[:2] != (height, width):
                    array = cv2.resize(array, (width, height), interpolation=cv2.INTER_LINEAR)
                process.stdin.write(np.ascontiguousarray(array).tobytes())
            process.stdin.close()
            stderr = process.stderr.read() if process.stderr is not None else b""
            return_code = process.wait()
        finally:
            if process.stdin and not process.stdin.closed:
                process.stdin.close()
        if return_code != 0:
            raise RuntimeError(f"Failed to encode event video clip: {stderr.decode('utf-8', errors='ignore').strip()}")

    async def save_event_video(
        self,
        *,
        frames: list[np.ndarray],
        event_id: str,
        timestamp: datetime,
        camera_id: Optional[str],
        fps: int = 10,
    ) -> Optional[VideoSaveResult]:
        if not frames:
            return None

        storage = get_object_storage()
        object_key = storage.build_video_key(
            event_id=event_id,
            timestamp=timestamp,
            camera_id=camera_id,
        )

        def _build_and_upload() -> VideoSaveResult:
            with tempfile.NamedTemporaryFile(suffix=".mp4", delete=True) as temp_file:
                self._encode_video_clip(frames, fps, temp_file.name)
                stored = storage.upload_file_path(
                    temp_file.name,
                    object_key=object_key,
                    content_type="video/mp4",
                    bucket=storage.video_bucket,
                )
                return VideoSaveResult(
                    storage="minio",
                    bucket=stored.bucket,
                    object_key=stored.object_key,
                    content_type=stored.content_type,
                    size_bytes=stored.size_bytes,
                )

        try:
            return await asyncio.to_thread(_build_and_upload)
        except Exception as exc:
            logger.warning("MinIO event video upload failed, falling back to local file: %s", exc)

            def _build_local_file() -> VideoSaveResult:
                settings.VIDEOS_DIR.mkdir(parents=True, exist_ok=True)
                filename = f"{timestamp:%Y%m%d_%H%M%S}_{event_id}.mp4"
                file_path = settings.VIDEOS_DIR / filename
                self._encode_video_clip(frames, fps, str(file_path))
                return VideoSaveResult(
                    storage="local",
                    video_path=str(file_path),
                    content_type="video/mp4",
                    size_bytes=Path(file_path).stat().st_size,
                )

            return await asyncio.to_thread(_build_local_file)

    async def update_event_video(
        self,
        event_id: str,
        *,
        video_path: Optional[str] = None,
        video_storage: Optional[str] = None,
        video_bucket: Optional[str] = None,
        video_object_key: Optional[str] = None,
        video_content_type: Optional[str] = None,
        video_size_bytes: Optional[int] = None,
    ) -> Optional[ComplianceEvent]:
        event = await self.get_event(event_id)
        if event is None:
            return None
        event.video_path = video_path
        event.video_storage = video_storage
        event.video_bucket = video_bucket
        event.video_object_key = video_object_key
        event.video_content_type = video_content_type
        event.video_size_bytes = video_size_bytes
        return event
