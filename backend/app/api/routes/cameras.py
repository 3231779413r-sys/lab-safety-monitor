from __future__ import annotations

import asyncio
import json
import time
from typing import Optional

import cv2
import numpy as np
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.config import settings
from ...core.database import async_session
from ...core.danger_events import get_danger_event_label, normalize_violation_key
from ...ml.face_recognition import FaceRecognizer
from ...models.external_person import ExternalPerson
from ...models.person import Person
from ...models.supervision import ExternalPersonnelRegistration
from ...models.video_source import VideoSource
from ...services.camera_service import (
    CameraService,
    get_camera_config,
    test_camera_connection,
    update_camera_config,
)
from ...services.worker_client import (
    raise_http_from_worker_error,
    request_worker_json,
    request_all_workers_json,
    stream_worker_response,
    worker_proxy_enabled,
    WorkerProxyError,
)
from ...services.face_registry_service import get_face_registry_service
from ...services.live_preview_store import get_live_preview_store
from ...services.object_storage import get_object_storage
from ..deps import get_database

import logging


router = APIRouter(prefix="/cameras", tags=["cameras"])
logger = logging.getLogger(__name__)

CAMERA_DETECTION_EVENT_OPTIONS = [
    {"key": "hardhat", "label": "未佩戴安全帽"},
    {"key": "mask", "label": "未佩戴口罩"},
    {"key": "protective_clothing", "label": "未穿戴防护服"},
    {"key": "safety_shoes", "label": "未穿戴防护鞋"},
    {"key": "gloves", "label": "未佩戴防护手套"},
    {"key": "goggles", "label": "未佩戴护目镜"},
    {"key": "respirator", "label": "未佩戴防毒口罩"},
    {"key": "unauthorized_intrusion", "label": "违规闯入"},
    {"key": "area_overcapacity", "label": "区域超员"},
]

BACKEND_DETECTION_EVENT_OPTIONS = [
    {"key": "missed_inspection", "label": "未巡检"},
    {"key": "area_missed_inspection", "label": "区域漏巡"},
    {"key": "unauthorized_intrusion", "label": "违规闯入"},
    {"key": "overtime_stay", "label": "超时驻留"},
    {"key": "blind_spot_stay", "label": "盲区驻留"},
    {"key": "workshop_overcapacity", "label": "车间超员"},
    {"key": "fall_detected", "label": "人员跌倒"},
]


CONNECTION_FIELDS = {
    "vendor",
    "host",
    "port",
    "username",
    "password",
    "channel",
    "stream_type",
    "transport_mode",
}


def _default_camera_port() -> int:
    return settings.HIKVISION_SDK_PORT


def _get_camera_runtime_registry():
    from ...services.camera_runtime import camera_runtime_registry

    return camera_runtime_registry


def _get_live_preview_cache():
    return get_live_preview_store()


def _build_preview_placeholder(camera_id: str, *, detail: str | None = None) -> bytes:
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    message = detail or f"Camera {camera_id} preview unavailable"
    cv2.putText(
        frame,
        message[:56],
        (24, 240),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.7,
        (255, 255, 255),
        2,
    )
    ok, buffer = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 75])
    if not ok:
        raise HTTPException(status_code=500, detail="Failed to build preview placeholder")
    return buffer.tobytes()


def _build_manual_preview_error_frame() -> bytes:
    error_frame = np.zeros((480, 640, 3), dtype=np.uint8)
    cv2.putText(
        error_frame,
        "摄像头画面不可用",
        (120, 240),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.9,
        (255, 255, 255),
        2,
    )
    ok, buffer = cv2.imencode(".jpg", error_frame, [cv2.IMWRITE_JPEG_QUALITY, 75])
    if not ok:
        raise HTTPException(status_code=500, detail="Failed to build manual preview placeholder")
    return buffer.tobytes()


def _build_manual_preview_response(camera: VideoSource, *, endpoint: str) -> StreamingResponse:
    async def preview_generator():
        source = None
        frame_index = 0
        preview_fps = min(
            max(1, settings.LIVE_STREAM_DISPLAY_FPS),
            max(1, settings.CAMERA_MONITOR_DISPLAY_FPS),
        )
        delay = 1.0 / preview_fps
        try:
            source = _open_manual_frame_source(camera)
            while True:
                loop_started_at = time.perf_counter()
                frame = source.read()
                read_ms = round((time.perf_counter() - loop_started_at) * 1000.0, 1)
                if frame is None:
                    await asyncio.sleep(delay)
                    continue
                encode_started_at = time.perf_counter()
                ok, buffer = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 75])
                encode_ms = round((time.perf_counter() - encode_started_at) * 1000.0, 1)
                if not ok:
                    await asyncio.sleep(delay)
                    continue
                frame_index += 1
                if read_ms >= 20.0 or encode_ms >= 20.0 or frame_index % 100 == 0:
                    logger.info(
                        "PREVIEW_API_TIMING %s",
                        json.dumps(
                            {
                                "camera_id": camera.id,
                                "endpoint": endpoint,
                                "frame_index": frame_index,
                                "read_ms": read_ms,
                                "encode_ms": encode_ms,
                                "bytes": int(buffer.size),
                                "placeholder": False,
                            },
                            ensure_ascii=False,
                            separators=(",", ":"),
                        ),
                    )
                yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + buffer.tobytes() + b"\r\n"
                await asyncio.sleep(delay)
        except Exception:
            yield (
                b"--frame\r\nContent-Type: image/jpeg\r\n\r\n"
                + _build_manual_preview_error_frame()
                + b"\r\n"
            )
        finally:
            if source is not None:
                source.close()

    return StreamingResponse(
        preview_generator(),
        media_type="multipart/x-mixed-replace; boundary=frame",
    )


def _merge_runtime_recommendations(
    shard_payloads: list[dict],
    camera_payloads: list[dict],
) -> tuple[list[dict], list[dict]]:
    if not shard_payloads or not camera_payloads:
        return shard_payloads, camera_payloads
    shard_rank = sorted(
        shard_payloads,
        key=lambda item: float(item.get("p95_latency_ms") or item.get("avg_latency_ms") or 0.0),
    )
    lightest_shard = shard_rank[0] if shard_rank else None
    heaviest_shard = shard_rank[-1] if shard_rank else None
    for camera in camera_payloads:
        hot_camera = bool(camera.get("hot_camera"))
        if not hot_camera:
            camera.setdefault("recommended_target_shard", None)
            continue
        current_shard = camera.get("shard_index")
        target_shard = None
        if lightest_shard is not None and lightest_shard.get("shard_index") != current_shard:
            target_shard = lightest_shard.get("shard_index")
        camera["recommended_target_shard"] = target_shard
        if target_shard is not None and not camera.get("recommended_reason"):
            camera["recommended_reason"] = f"move away from shard-{current_shard} to shard-{target_shard}"
    if heaviest_shard is not None and lightest_shard is not None:
        for shard in shard_payloads:
            shard["recommended_rebalance_target"] = None
            if shard.get("shard_index") == heaviest_shard.get("shard_index") and shard.get("shard_index") != lightest_shard.get("shard_index"):
                shard["recommended_rebalance_target"] = lightest_shard.get("shard_index")
    return shard_payloads, camera_payloads


def _parse_scope(value: Optional[str]) -> list[str]:
    if not value:
        return []
    try:
        parsed = json.loads(value)
        if isinstance(parsed, list):
            result: list[str] = []
            for item in parsed:
                normalized = normalize_violation_key(str(item))
                if normalized and normalized not in result:
                    result.append(normalized)
            return result
    except json.JSONDecodeError:
        pass
    return [normalize_violation_key(item) for item in value.split(",") if normalize_violation_key(item)]


def _encode_scope(values: list[str]) -> str:
    unique_values: list[str] = []
    for value in values:
        normalized = normalize_violation_key(value)
        if normalized and normalized not in unique_values:
            unique_values.append(normalized)
    return json.dumps(unique_values, ensure_ascii=False)


def _parse_polygon(value: Optional[str]) -> list[list[float]]:
    if not value:
        return []
    try:
        parsed = json.loads(value)
        if isinstance(parsed, list):
            result: list[list[float]] = []
            for point in parsed:
                if isinstance(point, list) and len(point) == 2:
                    result.append([float(point[0]), float(point[1])])
            return result
    except json.JSONDecodeError:
        return []
    return []


def _encode_polygon(points: list[list[float]]) -> Optional[str]:
    if not points:
        return None
    normalized_points: list[list[float]] = []
    for point in points:
        if len(point) != 2:
            continue
        normalized_points.append([float(point[0]), float(point[1])])
    return json.dumps(normalized_points, ensure_ascii=False) if normalized_points else None


def _normalize_required_camera_password(password: str) -> str:
    normalized = password.strip()
    if not normalized:
        raise HTTPException(status_code=422, detail="Camera password cannot be empty")
    return normalized


def _normalize_optional_camera_password(password: Optional[str]) -> Optional[str]:
    if password is None:
        return None
    normalized = password.strip()
    return normalized or None


def _normalize_camera_payload(payload: dict, *, is_create: bool) -> dict:
    for key in ("floor", "name_suffix", "vendor", "host", "username", "stream_type", "transport_mode"):
        value = payload.get(key)
        if isinstance(value, str):
            payload[key] = value.strip()

    if "port" in payload:
        port = payload.get("port")
        if port in (None, "", 0):
            payload["port"] = _default_camera_port()
        else:
            payload["port"] = int(port)
    elif is_create:
        payload["port"] = _default_camera_port()

    if is_create:
        payload["password"] = _normalize_required_camera_password(str(payload.get("password", "")))
    elif "password" in payload:
        normalized_password = _normalize_optional_camera_password(payload.get("password"))
        if normalized_password is None:
            payload.pop("password", None)
        else:
            payload["password"] = normalized_password

    return payload


class CameraEventOption(BaseModel):
    key: str
    label: str


class CameraBase(BaseModel):
    floor: str
    name_suffix: str
    vendor: str = "hikvision"
    host: str
    port: int = settings.HIKVISION_SDK_PORT
    username: str
    password: str
    channel: int = 1
    stream_type: str = "main"
    enabled: bool = True
    is_default: bool = False
    video_resolution: Optional[str] = None
    frame_rate: Optional[float] = None
    max_bitrate: Optional[int] = None
    video_encoding: Optional[str] = None
    transport_mode: Optional[str] = None
    camera_detection_scope: list[str] = []
    backend_detection_scope: list[str] = []
    area_overcapacity_polygon: list[list[float]] = []
    area_overcapacity_limit: Optional[int] = None


class CameraCreateRequest(CameraBase):
    pass


class CameraUpdateRequest(BaseModel):
    floor: Optional[str] = None
    name_suffix: Optional[str] = None
    vendor: Optional[str] = None
    host: Optional[str] = None
    port: Optional[int] = None
    username: Optional[str] = None
    password: Optional[str] = None
    channel: Optional[int] = None
    stream_type: Optional[str] = None
    enabled: Optional[bool] = None
    is_default: Optional[bool] = None
    video_resolution: Optional[str] = None
    frame_rate: Optional[float] = None
    max_bitrate: Optional[int] = None
    video_encoding: Optional[str] = None
    transport_mode: Optional[str] = None
    camera_detection_scope: Optional[list[str]] = None
    backend_detection_scope: Optional[list[str]] = None
    area_overcapacity_polygon: Optional[list[list[float]]] = None
    area_overcapacity_limit: Optional[int] = None


class CameraResponse(BaseModel):
    id: str
    name: str
    floor: Optional[str]
    name_suffix: Optional[str]
    vendor: Optional[str]
    source_type: str
    host: Optional[str]
    port: Optional[int]
    username: Optional[str]
    password: Optional[str]
    channel: Optional[int]
    stream_type: Optional[str]
    enabled: bool
    is_default: bool
    last_test_status: Optional[str]
    last_test_error: Optional[str]
    last_seen_at: Optional[str]
    created_at: Optional[str]
    video_resolution: Optional[str] = None
    frame_rate: Optional[float] = None
    max_bitrate: Optional[int] = None
    video_encoding: Optional[str] = None
    transport_mode: Optional[str] = None
    camera_detection_scope: list[str] = []
    camera_detection_scope_labels: list[str] = []
    backend_detection_scope: list[str] = []
    backend_detection_scope_labels: list[str] = []
    area_overcapacity_polygon: list[list[float]] = []
    area_overcapacity_limit: Optional[int] = None

    @classmethod
    def from_model(cls, camera: VideoSource) -> "CameraResponse":
        camera_detection_scope = _parse_scope(camera.camera_detection_scope)
        backend_detection_scope = _parse_scope(camera.backend_detection_scope)
        return cls(
            id=camera.id,
            name=camera.name,
            floor=camera.floor,
            name_suffix=camera.name_suffix,
            vendor=camera.vendor,
            source_type=camera.source_type,
            host=camera.host,
            port=camera.port,
            username=camera.username,
            password=camera.password,
            channel=camera.channel,
            stream_type=camera.stream_type,
            enabled=bool(camera.enabled),
            is_default=bool(camera.is_default),
            last_test_status=camera.last_test_status,
            last_test_error=camera.last_test_error,
            last_seen_at=camera.last_seen_at.isoformat() if camera.last_seen_at else None,
            created_at=camera.created_at.isoformat() if camera.created_at else None,
            video_resolution=camera.video_resolution,
            frame_rate=camera.frame_rate,
            max_bitrate=camera.max_bitrate,
            video_encoding=camera.video_encoding,
            transport_mode=camera.transport_mode,
            camera_detection_scope=camera_detection_scope,
            camera_detection_scope_labels=[get_danger_event_label(item) for item in camera_detection_scope],
            backend_detection_scope=backend_detection_scope,
            backend_detection_scope_labels=[get_danger_event_label(item) for item in backend_detection_scope],
            area_overcapacity_polygon=_parse_polygon(camera.area_overcapacity_polygon),
            area_overcapacity_limit=camera.area_overcapacity_limit,
        )


class FloorActivitySnapshotItem(BaseModel):
    floor: str
    camera_id: str
    camera_name: str
    person_count: int
    last_frame_at: Optional[str] = None
    frame_url: Optional[str] = None
    frame_width: int = 0
    frame_height: int = 0
    persons: list[LivePersonOverlay] = []


class FloorActivitySnapshotResponse(BaseModel):
    items: list[FloorActivitySnapshotItem]


class CameraTestResponse(BaseModel):
    success: bool
    message: str
    stream_url: Optional[str] = None
    device_info: Optional[dict] = None
    error: Optional[str] = None


class CameraConfigRequest(BaseModel):
    video_encoding: Optional[str] = Field(None, pattern="^(H\\.265|H\\.264)$")
    video_resolution_width: Optional[int] = Field(None, ge=320, le=4096)
    video_resolution_height: Optional[int] = Field(None, ge=240, le=2160)
    frame_rate: Optional[float] = Field(None, ge=0.0625, le=60)
    max_bitrate: Optional[int] = Field(None, ge=32, le=16384)
    bit_rate: Optional[int] = Field(None, ge=32, le=16384)
    gov_length: Optional[int] = Field(None, ge=1, le=500)
    transport_protocol: Optional[str] = Field(None, pattern="^(UDP|TCP)$")


class CameraConfigResponse(BaseModel):
    success: bool
    message: str
    config: Optional[dict] = None
    error: Optional[str] = None


class FaceMatchCandidate(BaseModel):
    subject_id: str
    subject_type: str
    name: str
    organization: Optional[str] = None
    similarity: float
    cosine_similarity: Optional[float] = None
    face_image_url: Optional[str] = None


class CameraFaceMatchResponse(BaseModel):
    camera_id: str
    matched: bool
    best_match: Optional[FaceMatchCandidate] = None
    candidates: list[FaceMatchCandidate] = []
    face_detected: bool


class LivePersonOverlay(BaseModel):
    track_id: Optional[int] = None
    stable_track_id: Optional[int] = None
    raw_track_id: Optional[int] = None
    person_id: Optional[str] = None
    person_name: str
    box: list[float]


class LivePersonOverlayResponse(BaseModel):
    camera_id: str
    frame_width: int = 0
    frame_height: int = 0
    persons: list[LivePersonOverlay] = []
    last_frame_at: Optional[str] = None


def _candidate_image_url(
    storage: Optional[str],
    bucket: Optional[str],
    object_key: Optional[str],
) -> Optional[str]:
    if storage == "minio" and bucket and object_key:
        return f"/api/events/objects/{bucket}/{object_key}"
    return None


def _similarity_to_score(similarity: float) -> float:
    return FaceRecognizer.similarity_to_score(similarity)


def _select_face_match_candidate(
    candidates: list["FaceMatchCandidate"],
) -> Optional["FaceMatchCandidate"]:
    if not candidates:
        return None
    ordered = sorted(
        candidates,
        key=lambda item: (
            item.cosine_similarity if item.cosine_similarity is not None else -1.0
        ),
        reverse=True,
    )
    best = ordered[0]
    best_similarity = best.cosine_similarity
    if best_similarity is None or best_similarity < settings.FACE_RECOGNITION_THRESHOLD:
        return None
    second_best_similarity = (
        ordered[1].cosine_similarity
        if len(ordered) > 1 and ordered[1].cosine_similarity is not None
        else None
    )
    if (
        second_best_similarity is not None
        and best_similarity - second_best_similarity
        < settings.FACE_RECOGNITION_MIN_MARGIN
    ):
        return None
    return best


def _open_manual_frame_source(camera: VideoSource):
    from ...services.camera_runtime import HikvisionSdkRuntimeSource

    source = HikvisionSdkRuntimeSource(camera)
    source.open()
    return source


async def _get_camera_or_404(service: CameraService, camera_id: str) -> VideoSource:
    camera = await service.get_camera(camera_id)
    if not camera or camera.source_type != "camera":
        raise HTTPException(status_code=404, detail=f"Camera not found: {camera_id}")
    return camera


@router.get("", response_model=list[CameraResponse])
async def list_cameras(db: AsyncSession = Depends(get_database)):
    service = CameraService(db)
    cameras = await service.list_cameras()
    return [CameraResponse.from_model(camera) for camera in cameras]


@router.get("/event-options")
async def get_camera_event_options():
    return {
        "camera_detection": [CameraEventOption(**item) for item in CAMERA_DETECTION_EVENT_OPTIONS],
        "backend_detection": [CameraEventOption(**item) for item in BACKEND_DETECTION_EVENT_OPTIONS],
    }


@router.post("", response_model=CameraResponse)
async def create_camera(
    request: CameraCreateRequest,
    db: AsyncSession = Depends(get_database),
):
    service = CameraService(db)
    payload = _normalize_camera_payload(request.model_dump(), is_create=True)
    floor = payload["floor"]
    name_suffix = payload["name_suffix"]
    payload["floor"] = floor
    payload["name_suffix"] = name_suffix
    payload["name"] = f"{floor}{name_suffix}"
    payload["source_type"] = "camera"
    payload["camera_detection_scope"] = _encode_scope(request.camera_detection_scope)
    payload["backend_detection_scope"] = _encode_scope(request.backend_detection_scope)
    payload["area_overcapacity_polygon"] = _encode_polygon(request.area_overcapacity_polygon)
    camera = await service.create_camera(**payload)
    if camera.enabled:
        if worker_proxy_enabled():
            try:
                await request_worker_json("POST", f"/internal/cameras/{camera.id}/start")
            except WorkerProxyError as exc:
                raise_http_from_worker_error(exc)
        else:
            _get_camera_runtime_registry().start_camera(camera)
    return CameraResponse.from_model(camera)


@router.get("/default", response_model=CameraResponse)
async def get_default_camera(db: AsyncSession = Depends(get_database)):
    service = CameraService(db)
    camera = await service.get_default_camera()
    if not camera:
        raise HTTPException(status_code=404, detail="Default camera is not configured")
    return CameraResponse.from_model(camera)


@router.get("/runtime/all/status")
async def list_camera_runtime_status():
    if worker_proxy_enabled():
        try:
            payloads = await request_all_workers_json("GET", "/internal/cameras/runtime/all/status")
        except WorkerProxyError as exc:
            raise_http_from_worker_error(exc)
        merged: dict[str, dict] = {}
        for payload in payloads:
            for item in list((payload or {}).get("cameras") or []):
                camera_id = str(item.get("camera_id") or "").strip()
                if camera_id:
                    merged[camera_id] = item
        return {"cameras": list(merged.values())}
    return {"cameras": _get_camera_runtime_registry().list_statuses()}


@router.get("/runtime/all/summary")
async def list_camera_runtime_summary():
    if worker_proxy_enabled():
        try:
            payloads = await request_all_workers_json("GET", "/internal/cameras/runtime/all/summary")
        except WorkerProxyError as exc:
            raise_http_from_worker_error(exc)
        shard_payloads: list[dict] = []
        merged_cameras: dict[str, dict] = {}
        for payload in payloads:
            if isinstance(payload, dict) and isinstance(payload.get("shard"), dict):
                shard_payloads.append(payload["shard"])
            for item in list((payload or {}).get("cameras") or []):
                camera_id = str(item.get("camera_id") or "").strip()
                if camera_id:
                    merged_cameras[camera_id] = item
        shard_payloads, camera_payloads = _merge_runtime_recommendations(
            shard_payloads,
            list(merged_cameras.values()),
        )
        return {"shards": shard_payloads, "cameras": camera_payloads}
    registry = _get_camera_runtime_registry()
    shard_payloads, camera_payloads = _merge_runtime_recommendations(
        [registry.shard_summary_snapshot()],
        registry.list_runtime_summaries(),
    )
    return {
        "shards": shard_payloads,
        "cameras": camera_payloads,
    }


@router.get("/{camera_id}/runtime/status")
async def get_camera_runtime_status(camera_id: str):
    if worker_proxy_enabled():
        try:
            return await request_worker_json("GET", f"/internal/cameras/{camera_id}/runtime/status")
        except WorkerProxyError as exc:
            raise_http_from_worker_error(exc)
    return _get_camera_runtime_registry().get_status(camera_id)


@router.get("/{camera_id}", response_model=CameraResponse)
async def get_camera(camera_id: str, db: AsyncSession = Depends(get_database)):
    service = CameraService(db)
    camera = await _get_camera_or_404(service, camera_id)
    return CameraResponse.from_model(camera)


@router.patch("/{camera_id}", response_model=CameraResponse)
async def update_camera(
    camera_id: str,
    request: CameraUpdateRequest,
    db: AsyncSession = Depends(get_database),
):
    service = CameraService(db)
    camera = await _get_camera_or_404(service, camera_id)
    updates = {key: value for key, value in request.model_dump().items() if value is not None}
    updates = _normalize_camera_payload(updates, is_create=False)
    floor = updates.get("floor", camera.floor or "")
    name_suffix = updates.get("name_suffix", camera.name_suffix or "")
    if "floor" in updates or "name_suffix" in updates:
        updates["name"] = f"{floor}{name_suffix}"
    if "camera_detection_scope" in updates:
        updates["camera_detection_scope"] = _encode_scope(updates["camera_detection_scope"])
    if "backend_detection_scope" in updates:
        updates["backend_detection_scope"] = _encode_scope(updates["backend_detection_scope"])
    if "area_overcapacity_polygon" in updates:
        updates["area_overcapacity_polygon"] = _encode_polygon(updates["area_overcapacity_polygon"])

    updated = await service.update_camera(camera, **updates)
    if updated.enabled:
        if worker_proxy_enabled():
            try:
                if CONNECTION_FIELDS.intersection(updates):
                    await request_worker_json("POST", f"/internal/cameras/{camera_id}/restart")
                else:
                    await request_worker_json("POST", f"/internal/cameras/{camera_id}/start")
            except WorkerProxyError as exc:
                raise_http_from_worker_error(exc)
        else:
            if CONNECTION_FIELDS.intersection(updates):
                _get_camera_runtime_registry().restart_camera(updated)
            else:
                _get_camera_runtime_registry().start_camera(updated)
    else:
        if worker_proxy_enabled():
            try:
                await request_worker_json("POST", f"/internal/cameras/{camera_id}/stop")
            except WorkerProxyError as exc:
                raise_http_from_worker_error(exc)
        else:
            _get_camera_runtime_registry().stop_camera(camera_id)
    return CameraResponse.from_model(updated)


@router.delete("/{camera_id}")
async def delete_camera(camera_id: str, db: AsyncSession = Depends(get_database)):
    service = CameraService(db)
    camera = await _get_camera_or_404(service, camera_id)
    try:
        await service.delete_camera(camera)
    except IntegrityError as exc:
        logger.exception("Failed to delete camera %s because of database references", camera_id)
        raise HTTPException(
            status_code=409,
            detail="Camera is still referenced by other records and could not be deleted",
        ) from exc
    if worker_proxy_enabled():
        try:
            await request_worker_json("POST", f"/internal/cameras/{camera_id}/stop")
        except WorkerProxyError as exc:
            raise_http_from_worker_error(exc)
    else:
        _get_camera_runtime_registry().stop_camera(camera_id)
    return {"message": f"Deleted camera {camera_id}"}


@router.post("/{camera_id}/test", response_model=CameraTestResponse)
async def test_camera(camera_id: str, db: AsyncSession = Depends(get_database)):
    service = CameraService(db)
    camera = await _get_camera_or_404(service, camera_id)
    if worker_proxy_enabled():
        try:
            result = await request_worker_json("POST", f"/internal/cameras/{camera_id}/test")
            await service.update_test_status(camera, result.get("success", False), result.get("error"))
            return CameraTestResponse(
                success=result.get("success", False),
                message=result.get("message", ""),
                stream_url=result.get("stream_url"),
                device_info=result.get("device_info"),
                error=result.get("error"),
            )
        except WorkerProxyError as exc:
            raise_http_from_worker_error(exc)
    result = await test_camera_connection(camera)
    await service.update_test_status(camera, result.success, result.error)
    refreshed = await _get_camera_or_404(service, camera_id)
    return CameraTestResponse(
        success=result.success,
        message=result.message,
        stream_url=result.stream_url,
        device_info=result.device_info,
        error=refreshed.last_test_error,
    )


@router.post("/{camera_id}/enable", response_model=CameraResponse)
async def enable_camera(camera_id: str, db: AsyncSession = Depends(get_database)):
    service = CameraService(db)
    camera = await _get_camera_or_404(service, camera_id)
    logger.info(
        "[CameraEnable] request camera_id=%s name=%s enabled_before=%s host=%s",
        camera.id,
        camera.name,
        camera.enabled,
        camera.host,
    )
    updated = await service.set_enabled(camera, True)
    if worker_proxy_enabled():
        try:
            await request_worker_json("POST", f"/internal/cameras/{camera_id}/start")
        except WorkerProxyError as exc:
            raise_http_from_worker_error(exc)
    else:
        _get_camera_runtime_registry().start_camera(updated)
    logger.info(
        "[CameraEnable] started camera_id=%s name=%s enabled_after=%s runtime=%s",
        updated.id,
        updated.name,
        updated.enabled,
        (await request_worker_json("GET", f"/internal/cameras/{updated.id}/runtime/status"))
        if worker_proxy_enabled()
        else _get_camera_runtime_registry().get_status(updated.id),
    )
    return CameraResponse.from_model(updated)


@router.post("/{camera_id}/disable", response_model=CameraResponse)
async def disable_camera(camera_id: str, db: AsyncSession = Depends(get_database)):
    service = CameraService(db)
    camera = await _get_camera_or_404(service, camera_id)
    logger.info(
        "[CameraDisable] request camera_id=%s name=%s enabled_before=%s",
        camera.id,
        camera.name,
        camera.enabled,
    )
    updated = await service.set_enabled(camera, False)
    if worker_proxy_enabled():
        try:
            await request_worker_json("POST", f"/internal/cameras/{camera_id}/stop")
        except WorkerProxyError as exc:
            raise_http_from_worker_error(exc)
    else:
        _get_camera_runtime_registry().stop_camera(camera_id)
    logger.info(
        "[CameraDisable] stopped camera_id=%s name=%s enabled_after=%s",
        updated.id,
        updated.name,
        updated.enabled,
    )
    return CameraResponse.from_model(updated)


@router.post("/{camera_id}/set-default", response_model=CameraResponse)
async def set_default_camera(camera_id: str, db: AsyncSession = Depends(get_database)):
    service = CameraService(db)
    camera = await _get_camera_or_404(service, camera_id)
    updated = await service.set_default(camera)
    return CameraResponse.from_model(updated)


@router.get("/{camera_id}/live/feed")
async def live_camera_feed(
    camera_id: str,
    raw: bool = False,
):
    async with async_session() as db:
        service = CameraService(db)
        camera = await _get_camera_or_404(service, camera_id)
    if not camera.enabled:
        raise HTTPException(status_code=400, detail="Camera is disabled")

    if worker_proxy_enabled():
        try:
            upstream = stream_worker_response(
                f"/internal/cameras/{camera_id}/live/feed{'?raw=true' if raw else ''}"
            )
        except WorkerProxyError as exc:
            raise_http_from_worker_error(exc)

        def iter_stream():
            try:
                for chunk in upstream.iter_content(chunk_size=8192):
                    if chunk:
                        yield chunk
            finally:
                upstream.close()

        return StreamingResponse(
            iter_stream(),
            media_type=upstream.headers.get("content-type", "multipart/x-mixed-replace; boundary=frame"),
        )
    elif _get_camera_runtime_registry().get_status(camera_id)["status"] == "stopped":
        _get_camera_runtime_registry().start_camera(camera)

    async def cached_frame_generator():
        preview_cache = _get_live_preview_cache()
        stream_fps = min(
            max(1, settings.LIVE_STREAM_DISPLAY_FPS),
            max(1, settings.CAMERA_MONITOR_DISPLAY_FPS),
        )
        delay = 1.0 / stream_fps
        frame_index = 0
        while True:
            loop_started_at = time.perf_counter()
            frame_bytes = preview_cache.read_frame(camera_id, raw=raw)
            read_ms = round((time.perf_counter() - loop_started_at) * 1000.0, 1)
            placeholder = False
            if frame_bytes is None:
                status = preview_cache.read_status(camera_id) or {}
                detail = status.get("status") or "preview unavailable"
                frame_bytes = _build_preview_placeholder(camera_id, detail=detail)
                placeholder = True
            frame_index += 1
            if placeholder or read_ms >= 20.0 or frame_index % 100 == 0:
                logger.info(
                    "PREVIEW_API_TIMING %s",
                    json.dumps(
                        {
                            "camera_id": camera_id,
                            "endpoint": "live_feed",
                            "raw": raw,
                            "frame_index": frame_index,
                            "read_ms": read_ms,
                            "bytes": len(frame_bytes),
                            "placeholder": placeholder,
                        },
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                )
            yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + frame_bytes + b"\r\n"
            await asyncio.sleep(delay)

    return StreamingResponse(
        cached_frame_generator(),
        media_type="multipart/x-mixed-replace; boundary=frame",
    )


@router.get("/{camera_id}/live/people", response_model=LivePersonOverlayResponse)
async def get_live_camera_people(camera_id: str):
    if worker_proxy_enabled():
        try:
            payload = await request_worker_json("GET", f"/internal/cameras/{camera_id}/live/people")
        except WorkerProxyError as exc:
            raise_http_from_worker_error(exc)
    else:
        payload = _get_live_preview_cache().read_people(camera_id)
        if payload is None:
            payload = _get_camera_runtime_registry().get_latest_person_overlays(camera_id)
    return LivePersonOverlayResponse(**payload)


@router.get("/{camera_id}/live/frame.jpg")
async def get_live_camera_frame_image(
    camera_id: str,
    raw: bool = False,
):
    async with async_session() as db:
        service = CameraService(db)
        camera = await _get_camera_or_404(service, camera_id)
    if not camera.enabled:
        raise HTTPException(status_code=400, detail="Camera is disabled")

    if worker_proxy_enabled():
        try:
            upstream = stream_worker_response(
                f"/internal/cameras/{camera_id}/live/frame.jpg{'?raw=true' if raw else ''}"
            )
        except WorkerProxyError as exc:
            raise_http_from_worker_error(exc)
        try:
            body = upstream.content
            media_type = upstream.headers.get("content-type", "image/jpeg")
        finally:
            upstream.close()
        logger.info(
            "PREVIEW_API_TIMING %s",
            json.dumps(
                {
                    "camera_id": camera_id,
                    "endpoint": "live_frame_proxy_raw" if raw else "live_frame_proxy",
                    "read_ms": 0.0,
                    "bytes": len(body),
                    "placeholder": False,
                },
                ensure_ascii=False,
                separators=(",", ":"),
            ),
        )
        return Response(content=body, media_type=media_type)
    elif _get_camera_runtime_registry().get_status(camera_id)["status"] == "stopped":
        _get_camera_runtime_registry().start_camera(camera)

    read_started_at = time.perf_counter()
    frame_bytes = _get_live_preview_cache().read_frame(camera_id, raw=raw)
    read_ms = round((time.perf_counter() - read_started_at) * 1000.0, 1)
    if frame_bytes is None:
        raise HTTPException(status_code=404, detail="Frame not available")
    logger.info(
        "PREVIEW_API_TIMING %s",
        json.dumps(
            {
                "camera_id": camera_id,
                "endpoint": "live_frame_raw" if raw else "live_frame",
                "read_ms": read_ms,
                "bytes": len(frame_bytes),
                "placeholder": False,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ),
    )
    return Response(content=frame_bytes, media_type="image/jpeg")


@router.get("/live/floor-activity", response_model=FloorActivitySnapshotResponse)
async def get_live_floor_activity(
    floors: Optional[str] = None,
    db: AsyncSession = Depends(get_database),
):
    floor_list = [
        item.strip()
        for item in (floors.split(",") if floors else ["一楼", "二楼", "三楼", "四楼"])
        if item.strip()
    ]
    cameras = (await db.execute(select(VideoSource).where(VideoSource.enabled == True))).scalars().all()
    camera_map = {camera.id: camera for camera in cameras}
    live_people_by_camera: dict[str, dict] = {}

    if worker_proxy_enabled():
        try:
            payloads = await request_all_workers_json("GET", "/internal/cameras/live/people")
        except WorkerProxyError:
            payloads = []
        for item in payloads:
            if not isinstance(item, dict):
                continue
            cameras_payload = item.get("cameras")
            if not isinstance(cameras_payload, dict):
                continue
            for camera_id, payload in cameras_payload.items():
                if isinstance(payload, dict):
                    live_people_by_camera[str(camera_id)] = payload

    items: list[FloorActivitySnapshotItem] = []
    for floor in floor_list:
        floor_candidates: list[FloorActivitySnapshotItem] = []
        for camera in cameras:
            if (camera.floor or "").strip() != floor:
                continue
            payload = live_people_by_camera.get(camera.id)
            if payload is None and worker_proxy_enabled():
                try:
                    payload = await request_worker_json("GET", f"/internal/cameras/{camera.id}/live/people")
                except WorkerProxyError:
                    continue
            elif payload is None:
                payload = _get_live_preview_cache().read_people(camera.id)
                if payload is None:
                    payload = _get_camera_runtime_registry().get_latest_person_overlays(camera.id)
            person_count = len(payload.get("persons", []))
            if person_count <= 0:
                continue
            floor_candidates.append(
                FloorActivitySnapshotItem(
                    floor=floor,
                    camera_id=camera.id,
                    camera_name=camera.name or camera.id,
                    person_count=person_count,
                    last_frame_at=payload.get("last_frame_at"),
                    frame_url=f"/api/cameras/{camera.id}/live/frame.jpg?raw=true",
                    frame_width=int(payload.get("frame_width") or 0),
                    frame_height=int(payload.get("frame_height") or 0),
                    persons=payload.get("persons", []),
                )
            )

        floor_candidates.sort(
            key=lambda item: (
                -item.person_count,
                item.last_frame_at or "",
                camera_map[item.camera_id].name or item.camera_id,
            ),
            reverse=True,
        )
        if floor_candidates:
            items.append(floor_candidates[0])

    return FloorActivitySnapshotResponse(items=items)


@router.get("/{camera_id}/face-preview/feed")
async def live_camera_face_preview(
    camera_id: str,
    raw: bool = True,
):
    return await live_camera_feed(camera_id=camera_id, raw=raw)

 

@router.get("/{camera_id}/preview/feed")
async def live_camera_preview_feed(
    camera_id: str,
    raw: bool = True,
):
    return await live_camera_feed(camera_id=camera_id, raw=raw)


@router.get("/{camera_id}/face-match", response_model=CameraFaceMatchResponse)
async def match_face_from_camera_frame(
    camera_id: str,
    db: AsyncSession = Depends(get_database),
):
    if worker_proxy_enabled():
        try:
            result = await request_worker_json("GET", f"/internal/cameras/{camera_id}/face-match")
            return CameraFaceMatchResponse(**result)
        except WorkerProxyError as exc:
            raise_http_from_worker_error(exc)

    service = CameraService(db)
    camera = await _get_camera_or_404(service, camera_id)

    frame = None
    source = None
    try:
        source = _open_manual_frame_source(camera)
        for _ in range(10):
            frame = source.read()
            if frame is not None:
                break
            await asyncio.sleep(0.1)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"无法打开摄像头画面: {exc}") from exc
    finally:
        if source is not None:
            source.close()

    if frame is None:
        raise HTTPException(status_code=503, detail="暂未获取到摄像头画面，请稍后重试")

    recognizer = get_face_registry_service().recognizer
    try:
        detections = recognizer.detect_faces(frame)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    if not detections:
        return CameraFaceMatchResponse(
            camera_id=camera_id,
            matched=False,
            best_match=None,
            candidates=[],
            face_detected=False,
        )

    best_face = max(detections, key=lambda item: float(item.get("score", 0.0)))
    embedding = best_face.get("embedding")
    if embedding is None:
        return CameraFaceMatchResponse(
            camera_id=camera_id,
            matched=False,
            best_match=None,
            candidates=[],
            face_detected=False,
        )

    candidates: list[FaceMatchCandidate] = []

    employee_rows = list(
        (
            await db.execute(
                select(Person).where(
                    Person.face_embedding.isnot(None),
                    Person.is_employee == True,
                )
            )
        ).scalars().all()
    )
    for person in employee_rows:
        stored_embedding = FaceRecognizer.deserialize_embedding(person.face_embedding)
        similarity = recognizer.compare_embeddings(embedding, stored_embedding)
        candidates.append(
            FaceMatchCandidate(
                subject_id=person.id,
                subject_type="employee",
                name=person.name or person.id,
                organization=person.workshop,
                similarity=round(_similarity_to_score(similarity), 1),
                cosine_similarity=round(similarity, 4),
                face_image_url=_candidate_image_url(
                    getattr(person, "face_image_storage", None),
                    getattr(person, "face_image_bucket", None),
                    getattr(person, "face_image_object_key", None),
                ),
            )
        )

    external_rows = list(
        (
            await db.execute(
                select(ExternalPerson).where(ExternalPerson.face_embedding.isnot(None))
            )
        ).scalars().all()
    )
    for person in external_rows:
        stored_embedding = FaceRecognizer.deserialize_embedding(person.face_embedding)
        similarity = recognizer.compare_embeddings(embedding, stored_embedding)
        candidates.append(
            FaceMatchCandidate(
                subject_id=person.id,
                subject_type="external_person",
                name=person.name,
                organization=person.organization,
                similarity=round(_similarity_to_score(similarity), 1),
                cosine_similarity=round(similarity, 4),
                face_image_url=_candidate_image_url(
                    getattr(person, "face_image_storage", None),
                    getattr(person, "face_image_bucket", None),
                    getattr(person, "face_image_object_key", None),
                ),
            )
        )

    registration_rows = list(
        (
            await db.execute(
                select(ExternalPersonnelRegistration).where(
                    ExternalPersonnelRegistration.face_embedding.isnot(None)
                )
            )
        ).scalars().all()
    )
    for person in registration_rows:
        stored_embedding = FaceRecognizer.deserialize_embedding(person.face_embedding)
        similarity = recognizer.compare_embeddings(embedding, stored_embedding)
        candidates.append(
            FaceMatchCandidate(
                subject_id=person.id,
                subject_type="external_registration",
                name=person.name,
                organization=person.organization,
                similarity=round(_similarity_to_score(similarity), 1),
                cosine_similarity=round(similarity, 4),
                face_image_url=_candidate_image_url(
                    getattr(person, "face_image_storage", None),
                    getattr(person, "face_image_bucket", None),
                    getattr(person, "face_image_object_key", None),
                ),
            )
        )

    candidates.sort(key=lambda item: item.similarity, reverse=True)
    best_match = _select_face_match_candidate(candidates)
    return CameraFaceMatchResponse(
        camera_id=camera_id,
        matched=best_match is not None,
        best_match=best_match,
        candidates=candidates[:5],
        face_detected=True,
    )


@router.get("/{camera_id}/config", response_model=CameraConfigResponse)
async def get_cam_config(camera_id: str, db: AsyncSession = Depends(get_database)):
    service = CameraService(db)
    camera = await _get_camera_or_404(service, camera_id)
    if worker_proxy_enabled():
        try:
            result = await request_worker_json("GET", f"/internal/cameras/{camera_id}/config")
            if result.get("success") and result.get("config"):
                await service.sync_camera_config(camera, result["config"])
            return CameraConfigResponse(
                success=result.get("success", False),
                message=result.get("message", ""),
                config=result.get("config"),
                error=result.get("error"),
            )
        except WorkerProxyError as exc:
            raise_http_from_worker_error(exc)
    result = await get_camera_config(camera)
    if result.success and result.config:
        await service.sync_camera_config(camera, result.config)
    return CameraConfigResponse(
        success=result.success,
        message=result.message,
        config=result.config,
        error=result.error,
    )


@router.put("/{camera_id}/config", response_model=CameraConfigResponse)
async def put_cam_config(
    camera_id: str,
    request: CameraConfigRequest,
    db: AsyncSession = Depends(get_database),
):
    service = CameraService(db)
    camera = await _get_camera_or_404(service, camera_id)
    config = {key: value for key, value in request.model_dump().items() if value is not None}
    if request.video_resolution_width and request.video_resolution_height:
        camera.video_resolution = f"{request.video_resolution_width}x{request.video_resolution_height}"
    if worker_proxy_enabled():
        try:
            result = await request_worker_json(
                "PUT",
                f"/internal/cameras/{camera_id}/config",
                json_body=config,
            )
            if result.get("success") and result.get("config"):
                await service.sync_camera_config(camera, result["config"])
            return CameraConfigResponse(
                success=result.get("success", False),
                message=result.get("message", ""),
                config=result.get("config"),
                error=result.get("error"),
            )
        except WorkerProxyError as exc:
            raise_http_from_worker_error(exc)
    result = await update_camera_config(camera, config)
    if result.success and result.config:
        await service.sync_camera_config(camera, result.config)
    return CameraConfigResponse(
        success=result.success,
        message=result.message,
        config=result.config,
        error=result.error,
    )
