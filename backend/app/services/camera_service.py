from __future__ import annotations

import asyncio
import json
import ipaddress
import logging
import socket
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Optional

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.config import settings
from ..models.external_person import ExternalPerson
from ..models.inspection_window_patrol import InspectionWindowPatrolRecord
from ..models.supervision import ExternalPersonnelRegistration
from ..models.supervision_settings import SupervisionSettings
from ..models.video_source import VideoSource
from ..services.hikvision_sdk_source import (
    HikvisionSdkError,
    HikvisionSdkSession,
    test_hikvision_sdk_connection,
)

logger = logging.getLogger(__name__)

@dataclass
class CameraTestResult:
    success: bool
    message: str
    device_info: Optional[Dict[str, Any]] = None
    stream_url: Optional[str] = None
    error: Optional[str] = None


@dataclass
class CameraConfigResult:
    success: bool
    message: str
    config: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None


class CameraService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def list_cameras(self) -> list[VideoSource]:
        result = await self.session.execute(
            select(VideoSource).where(VideoSource.source_type == "camera")
        )
        cameras = list(result.scalars().all())
        return sorted(cameras, key=_camera_sort_key)

    async def get_camera(self, camera_id: str) -> Optional[VideoSource]:
        return await self.session.get(VideoSource, camera_id)

    async def create_camera(self, **kwargs) -> VideoSource:
        if kwargs.get("is_default"):
            await self._clear_default_camera()

        kwargs.setdefault("source_type", "camera")
        camera = VideoSource(**kwargs)
        self.session.add(camera)
        await self.session.commit()
        await self.session.refresh(camera)
        return camera

    async def update_camera(self, camera: VideoSource, **kwargs) -> VideoSource:
        if kwargs.get("is_default"):
            await self._clear_default_camera(except_id=camera.id)

        for key, value in kwargs.items():
            setattr(camera, key, value)

        await self.session.commit()
        await self.session.refresh(camera)
        return camera

    async def delete_camera(self, camera: VideoSource) -> None:
        await self.session.execute(
            delete(InspectionWindowPatrolRecord).where(
                InspectionWindowPatrolRecord.camera_id == camera.id
            )
        )
        await self._remove_camera_from_soft_references(camera.id)
        await self.session.delete(camera)
        await self.session.commit()

    async def set_default(self, camera: VideoSource) -> VideoSource:
        await self._clear_default_camera(except_id=camera.id)
        camera.is_default = True
        await self.session.commit()
        await self.session.refresh(camera)
        return camera

    async def get_default_camera(self) -> Optional[VideoSource]:
        result = await self.session.execute(
            select(VideoSource).where(
                VideoSource.source_type == "camera", VideoSource.is_default == True
            )
        )
        return result.scalar_one_or_none()

    async def set_enabled(self, camera: VideoSource, enabled: bool) -> VideoSource:
        camera.enabled = enabled
        await self.session.commit()
        await self.session.refresh(camera)
        return camera

    async def update_test_status(
        self, camera: VideoSource, success: bool, error: Optional[str]
    ) -> None:
        camera.last_test_status = "online" if success else "failed"
        camera.last_test_error = error
        camera.last_seen_at = datetime.utcnow() if success else camera.last_seen_at
        await self.session.commit()

    async def _clear_default_camera(self, except_id: Optional[str] = None) -> None:
        stmt = update(VideoSource).where(
            VideoSource.source_type == "camera", VideoSource.is_default == True
        )
        if except_id:
            stmt = stmt.where(VideoSource.id != except_id)
        await self.session.execute(stmt.values(is_default=False))
        await self.session.flush()

    async def sync_camera_config(self, camera: VideoSource, cfg: Dict[str, Any]) -> VideoSource:
        """Sync camera encoding config from SDK response into the database model."""
        if cfg.get("video_resolution") is not None:
            camera.video_resolution = cfg.get("video_resolution")
        # Hikvision SDK uses frame-rate value 0 for "full frame rate"; keep the
        # previous numeric DB value instead of overwriting it with null.
        if cfg.get("frame_rate") is not None:
            frame_rate = cfg.get("frame_rate")
            if float(frame_rate).is_integer() and frame_rate >= 1:
                camera.frame_rate = int(frame_rate)
        if cfg.get("max_bitrate") is not None:
            camera.max_bitrate = cfg.get("max_bitrate")
        if cfg.get("video_encoding") is not None:
            camera.video_encoding = cfg.get("video_encoding")
        transport = cfg.get("transport_protocol")
        if transport:
            camera.transport_mode = transport
        await self.session.commit()
        await self.session.refresh(camera)
        return camera

    async def _remove_camera_from_soft_references(self, camera_id: str) -> None:
        external_people = list(
            (
                await self.session.execute(
                    select(ExternalPerson).where(ExternalPerson.allowed_camera_ids.is_not(None))
                )
            ).scalars().all()
        )
        for row in external_people:
            updated_ids = _remove_camera_id_from_json_list(row.allowed_camera_ids, camera_id)
            if updated_ids is not None:
                row.allowed_camera_ids = updated_ids

        registrations = list(
            (
                await self.session.execute(
                    select(ExternalPersonnelRegistration).where(
                        ExternalPersonnelRegistration.allowed_camera_ids.is_not(None)
                    )
                )
            ).scalars().all()
        )
        for row in registrations:
            updated_ids = _remove_camera_id_from_json_list(row.allowed_camera_ids, camera_id)
            if updated_ids is not None:
                row.allowed_camera_ids = updated_ids

        settings_rows = list(
            (
                await self.session.execute(
                    select(SupervisionSettings).where(
                        SupervisionSettings.area_missed_inspection_camera_ids.is_not(None)
                    )
                )
            ).scalars().all()
        )
        for row in settings_rows:
            updated_ids = _remove_camera_id_from_json_list(
                row.area_missed_inspection_camera_ids,
                camera_id,
            )
            if updated_ids is not None:
                row.area_missed_inspection_camera_ids = updated_ids


def _normalize_floor_order(value: Optional[str]) -> tuple[int, str]:
    normalized = (value or "").strip()
    order = {
        "一楼": 1,
        "二楼": 2,
        "三楼": 3,
        "四楼": 4,
        "室外": 5,
        "户外": 5,
    }.get(normalized, 99)
    return order, normalized


def _ip_sort_key(value: Optional[str]) -> tuple[int, str]:
    host = (value or "").strip()
    if not host:
        return (2**32 - 1, "")
    try:
        return (int(ipaddress.ip_address(host)), host)
    except ValueError:
        return (2**32 - 1, host)


def _camera_sort_key(camera: VideoSource) -> tuple[Any, ...]:
    floor_order, floor_name = _normalize_floor_order(camera.floor)
    return (
        floor_order,
        _ip_sort_key(camera.host),
        floor_name,
        (camera.name_suffix or "").strip(),
        (camera.name or "").strip(),
        camera.created_at or datetime.min,
    )


def _remove_camera_id_from_json_list(value: Optional[str], camera_id: str) -> Optional[str]:
    if value is None:
        return None
    try:
        parsed = json.loads(value)
        if isinstance(parsed, list):
            updated = [str(item).strip() for item in parsed if str(item).strip() and str(item).strip() != camera_id]
            return json.dumps(list(dict.fromkeys(updated)), ensure_ascii=False)
    except json.JSONDecodeError:
        pass

    updated = [item.strip() for item in value.split(",") if item.strip() and item.strip() != camera_id]
    return json.dumps(list(dict.fromkeys(updated)), ensure_ascii=False)

def _sdk_stream_url(camera: VideoSource) -> str:
    channel = camera.channel or 1
    stream_type = (camera.stream_type or "sub").lower()
    return (
        f"hikvision_sdk://{camera.host}:{camera.port or settings.HIKVISION_SDK_PORT}"
        f"/channel/{channel}?stream={stream_type}"
    )


def _is_hikvision_camera(camera: VideoSource) -> bool:
    return (camera.vendor or "").lower() == "hikvision"


async def test_camera_connection(camera: VideoSource, timeout: int = 8) -> CameraTestResult:
    try:
        socket.gethostbyname(camera.host or "")
    except Exception as exc:
        return CameraTestResult(
            success=False,
            message="Cannot reach camera IP address. Check the host/IP and network.",
            error=f"Host resolution failed: {exc}",
        )

    loop = asyncio.get_running_loop()
    try:
        device_info = await loop.run_in_executor(
            None,
            lambda: test_hikvision_sdk_connection(
                sdk_dir=settings.HIKVISION_SDK_DIR,
                host=camera.host or "",
                username=camera.username or "",
                password=camera.password or "",
                port=camera.port or settings.HIKVISION_SDK_PORT,
                channel=camera.channel or 1,
                stream_type=(camera.stream_type or "sub").lower(),
                timeout=timeout,
            ),
        )
    except HikvisionSdkError as exc:
        return CameraTestResult(
            success=False,
            message="Hikvision SDK connection failed. Check SDK path, camera port, credentials, channel, and stream type.",
            stream_url=_sdk_stream_url(camera),
            error=str(exc),
        )
    except Exception as exc:
        logger.exception("Unexpected Hikvision SDK connection failure")
        return CameraTestResult(
            success=False,
            message="Unexpected Hikvision SDK connection failure.",
            stream_url=_sdk_stream_url(camera),
            error=str(exc),
        )

    return CameraTestResult(
        success=True,
        message="Camera connection succeeded through Hikvision SDK.",
        device_info=device_info,
        stream_url=_sdk_stream_url(camera),
    )


def _open_sdk_session(camera: VideoSource) -> HikvisionSdkSession:
    return HikvisionSdkSession(
        sdk_dir=settings.HIKVISION_SDK_DIR,
        host=camera.host or "",
        username=camera.username or "",
        password=camera.password or "",
        port=camera.port or settings.HIKVISION_SDK_PORT,
    )


def _get_camera_config_sync(camera: VideoSource) -> dict:
    with _open_sdk_session(camera) as session:
        return session.get_compression_config(
            camera.channel or 1,
            (camera.stream_type or "sub").lower(),
        )


def _update_camera_config_sync(camera: VideoSource, config: Dict[str, Any]) -> dict:
    with _open_sdk_session(camera) as session:
        return session.update_compression_config(
            camera.channel or 1,
            (camera.stream_type or "sub").lower(),
            config,
        )


async def get_camera_config(camera: VideoSource, timeout: int = 8) -> CameraConfigResult:
    if not _is_hikvision_camera(camera):
        return CameraConfigResult(
            success=False,
            message="Camera SDK config is only implemented for Hikvision cameras.",
            error="Unsupported camera vendor",
        )

    loop = asyncio.get_running_loop()
    try:
        config = await asyncio.wait_for(
            loop.run_in_executor(None, lambda: _get_camera_config_sync(camera)),
            timeout=timeout,
        )
        return CameraConfigResult(
            success=True,
            message="Config retrieved through Hikvision SDK.",
            config=config,
        )
    except HikvisionSdkError as exc:
        logger.error("Failed to get camera config through Hikvision SDK: %s", exc)
        return CameraConfigResult(
            success=False,
            message="Failed to retrieve camera config through Hikvision SDK.",
            error=str(exc),
        )
    except Exception as exc:
        logger.exception("Unexpected SDK config retrieval failure")
        return CameraConfigResult(
            success=False,
            message="Unexpected SDK config retrieval failure.",
            error=str(exc),
        )


async def update_camera_config(
    camera: VideoSource,
    config: Dict[str, Any],
    timeout: int = 8,
) -> CameraConfigResult:
    if not _is_hikvision_camera(camera):
        return CameraConfigResult(
            success=False,
            message="Camera SDK config is only implemented for Hikvision cameras.",
            error="Unsupported camera vendor",
        )

    loop = asyncio.get_running_loop()
    try:
        updated = await asyncio.wait_for(
            loop.run_in_executor(None, lambda: _update_camera_config_sync(camera, config)),
            timeout=timeout,
        )
        return CameraConfigResult(
            success=True,
            message="Config updated through Hikvision SDK.",
            config=updated,
        )
    except HikvisionSdkError as exc:
        logger.error("Failed to update camera config through Hikvision SDK: %s", exc)
        return CameraConfigResult(
            success=False,
            message="Failed to update camera config through Hikvision SDK.",
            error=str(exc),
        )
    except Exception as exc:
        logger.exception("Unexpected SDK config update failure")
        return CameraConfigResult(
            success=False,
            message="Unexpected SDK config update failure.",
            error=str(exc),
        )
