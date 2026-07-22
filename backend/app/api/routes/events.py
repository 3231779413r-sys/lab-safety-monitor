from datetime import datetime
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel
from sqlalchemy import String, cast, desc, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.config import settings
from ...core.danger_events import (
    expand_danger_event_filter_values,
    get_danger_event_label,
    match_danger_event_types,
    normalize_violation_key,
)
from ...models.event import ComplianceEvent
from ...models.person import Person
from ...models.video_source import VideoSource
from ...services.object_storage import get_object_storage
from ..deps import get_database


VIOLATION_LABEL_MAP = {
    "no_goggles": "未佩戴护目镜",
    "no_mask": "未佩戴口罩",
    "no_lab_coat": "未穿实验服",
    "lab_coat": "实验服",
    "no_gloves": "未佩戴防护手套",
    "no_head_mask": "未戴头套",
    "head_mask": "头套",
    "protective_clothing": "未穿戴防护服",
    "no_protective_clothing": "未穿戴防护服",
    "safety_vest": "未穿戴防护服",
    "no_safety_vest": "未穿戴防护服",
    "work_clothes": "未穿戴防护服",
    "no_work_clothes": "未穿戴防护服",
    "no_hardhat": "未佩戴安全帽",
    "no_safety_shoes": "未穿戴防护鞋",
    "protective_shoes": "未穿戴防护鞋",
    "no_protective_shoes": "未穿戴防护鞋",
    "respirator": "未佩戴防毒口罩",
    "gas_mask": "未佩戴防毒口罩",
    "anti_toxic_mask": "未佩戴防毒口罩",
    "no_respirator": "未佩戴防毒口罩",
    "no_gas_mask": "未佩戴防毒口罩",
    "drinking": "饮水",
    "eating": "进食",
    "fall_detected": "跌倒",
    "missed_inspection": "未巡检",
    "area_missed_inspection": "区域漏巡",
    "unauthorized_intrusion": "违规闯入",
    "overtime_stay": "超时驻留",
    "blind_spot_stay": "盲区驻留",
    "area_overcapacity": "区域超员",
    "workshop_overcapacity": "车间超员",
}


def _format_violation_label(value: str) -> str:
    normalized = normalize_violation_key(value)
    if normalized in {
        "hardhat",
        "mask",
        "protective_clothing",
        "safety_shoes",
        "gloves",
        "goggles",
        "respirator",
        "missed_inspection",
        "area_missed_inspection",
        "unauthorized_intrusion",
        "overtime_stay",
        "blind_spot_stay",
        "area_overcapacity",
        "workshop_overcapacity",
        "fall_detected",
    }:
        return get_danger_event_label(normalized)
    return VIOLATION_LABEL_MAP.get(
        normalized,
        normalized.replace("_", " ") if normalized else "未知违规",
    )


def _build_violation_labels(event: ComplianceEvent) -> List[str]:
    labels = [_format_violation_label(value) for value in (event.missing_ppe or [])]
    labels.extend(_format_violation_label(value) for value in (event.action_violations or []))
    return labels


def _build_danger_event_types(event: ComplianceEvent) -> List[str]:
    if event.danger_event_types:
        return event.danger_event_types
    return match_danger_event_types(event.missing_ppe or [], event.action_violations or [])


def _build_violation_type_filter(violation_type: str):
    normalized_violation_type = normalize_violation_key(violation_type)
    filter_values = expand_danger_event_filter_values(normalized_violation_type)
    conditions = []
    conditions.extend(
        cast(ComplianceEvent.missing_ppe, String).ilike(f'%"{value}"%')
        for value in filter_values
    )
    conditions.extend(
        cast(ComplianceEvent.action_violations, String).ilike(f'%"{value}"%')
        for value in filter_values
    )
    return or_(*conditions)


async def _resolve_related_names(
    db: AsyncSession, events: List[ComplianceEvent]
) -> tuple[dict[str, str], dict[str, str]]:
    person_ids = sorted({event.person_id for event in events if event.person_id})
    camera_ids = sorted(
        {
            camera_id
            for event in events
            for camera_id in ((event.camera_ids or []) if event.camera_ids else ([event.camera_id] if event.camera_id else []))
            if camera_id
        }
    )

    person_name_map: dict[str, str] = {}
    camera_name_map: dict[str, str] = {}

    if person_ids:
        result = await db.execute(select(Person.id, Person.name).where(Person.id.in_(person_ids)))
        person_name_map = {
            person_id: (name or person_id) for person_id, name in result.all()
        }

    if camera_ids:
        result = await db.execute(select(VideoSource.id, VideoSource.name).where(VideoSource.id.in_(camera_ids)))
        camera_name_map = {
            camera_id: (name or camera_id) for camera_id, name in result.all()
        }

    return person_name_map, camera_name_map


def _parse_iso_time(value: str) -> datetime:
    """Parse an ISO 8601 string, returning a timezone-naive datetime."""
    value = value.strip()
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        import re

        value = re.sub(r"\.\d+", "", value)
        parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is not None:
        parsed = parsed.replace(tzinfo=None)
    return parsed


def _build_snapshot_url(event: ComplianceEvent) -> Optional[str]:
    """Return a browser-usable snapshot URL for MinIO or legacy local files."""
    if (
        event.snapshot_storage == "minio"
        and event.snapshot_bucket
        and event.snapshot_object_key
    ):
        return f"/api/events/objects/{event.snapshot_bucket}/{event.snapshot_object_key}"
    if event.snapshot_path:
        return f"/api/events/snapshots/{Path(event.snapshot_path).name}"
    return None


def _build_video_url(event: ComplianceEvent) -> Optional[str]:
    if (
        event.video_storage == "minio"
        and event.video_bucket
        and event.video_object_key
    ):
        return f"/api/events/objects/{event.video_bucket}/{event.video_object_key}"
    if event.video_path:
        return f"/api/events/videos/{Path(event.video_path).name}"
    return None


def _to_event_response(
    event: ComplianceEvent,
    person_name_map: Optional[dict[str, str]] = None,
    camera_name_map: Optional[dict[str, str]] = None,
) -> "EventResponse":
    camera_ids = [
        str(camera_id)
        for camera_id in ((event.camera_ids or []) if isinstance(event.camera_ids, list) else [])
        if camera_id
    ]
    person_name = event.person_name or (
        (person_name_map or {}).get(event.person_id or "", event.person_id) if event.person_id else None
    )
    if event.person_id and str(event.person_id).startswith("unknown:"):
        person_name = "未知人员"
    if event.camera_name is not None:
        camera_name = event.camera_name
    elif camera_ids:
        resolved_names = [
            (camera_name_map or {}).get(camera_id, camera_id)
            for camera_id in camera_ids
            if camera_id
        ]
        camera_name = "、".join(resolved_names) if resolved_names else None
    else:
        camera_name = (
            (camera_name_map or {}).get(event.camera_id or "", event.camera_id)
            if event.camera_id
            else None
        )

    return EventResponse(
        id=event.id,
        person_id=event.person_id,
        person_name=person_name,
        timestamp=event.timestamp,
        video_source=event.video_source,
        camera_id=event.camera_id,
        camera_ids=camera_ids,
        camera_name=camera_name,
        frame_number=event.frame_number or 0,
        detected_ppe=list(event.detected_ppe or []),
        missing_ppe=list(event.missing_ppe or []),
        action_violations=list(event.action_violations or []),
        violation_labels=_build_violation_labels(event),
        danger_event_types=_build_danger_event_types(event),
        is_violation=bool(event.is_violation),
        start_frame=event.start_frame,
        end_frame=event.end_frame,
        end_timestamp=event.end_timestamp,
        duration_frames=event.duration_frames or 1,
        is_ongoing=bool(event.is_ongoing),
        snapshot_overlay=event.snapshot_overlay,
        snapshot_url=_build_snapshot_url(event),
        video_url=_build_video_url(event),
    )


class EventResponse(BaseModel):
    class SnapshotOverlayBox(BaseModel):
        kind: str
        label: str
        box: List[float]
        violation_key: Optional[str] = None

    class SnapshotOverlay(BaseModel):
        image_width: int
        image_height: int
        boxes: List["EventResponse.SnapshotOverlayBox"] = []

    id: str
    person_id: Optional[str]
    person_name: Optional[str] = None
    timestamp: datetime
    video_source: Optional[str]
    camera_id: Optional[str] = None
    camera_ids: List[str] = []
    camera_name: Optional[str] = None
    frame_number: int = 0
    detected_ppe: List[str]
    missing_ppe: List[str]
    action_violations: List[str] = []
    violation_labels: List[str] = []
    danger_event_types: List[str] = []
    is_violation: bool
    start_frame: Optional[int] = None
    end_frame: Optional[int] = None
    end_timestamp: Optional[datetime] = None
    duration_frames: int = 1
    is_ongoing: bool = True
    snapshot_overlay: Optional["EventResponse.SnapshotOverlay"] = None
    snapshot_url: Optional[str] = None
    video_url: Optional[str] = None

    class Config:
        from_attributes = True


class EventsListResponse(BaseModel):
    events: List[EventResponse]
    total: int
    page: int
    page_size: int


class GalleryItem(BaseModel):
    id: str
    snapshot_url: Optional[str]
    timestamp: datetime
    camera_id: Optional[str] = None
    person_id: Optional[str] = None
    missing_ppe: List[str] = []
    message: str


class GalleryResponse(BaseModel):
    items: List[GalleryItem]
    total: int
    page: int
    page_size: int


router = APIRouter(prefix="/events", tags=["events"])


@router.get("", response_model=EventsListResponse)
async def get_events(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    camera_id: Optional[str] = None,
    person_id: Optional[str] = None,
    person_name: Optional[str] = None,
    violations_only: bool = False,
    violation_type: Optional[str] = None,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
    db: AsyncSession = Depends(get_database),
):
    query = select(ComplianceEvent)

    if camera_id:
        query = query.where(ComplianceEvent.camera_id == camera_id)
    if person_id:
        query = query.where(ComplianceEvent.person_id == person_id)
    if person_name:
        pattern = f"%{person_name.strip()}%"
        if pattern != "%%":
            query = query.where(
                or_(
                    ComplianceEvent.person_name.ilike(pattern),
                    ComplianceEvent.person_id.in_(
                        select(Person.id).where(Person.name.ilike(pattern))
                    ),
                )
            )
    if violations_only:
        query = query.where(ComplianceEvent.is_violation == True)
    if violation_type:
        query = query.where(_build_violation_type_filter(violation_type))
    if start_time:
        try:
            query = query.where(ComplianceEvent.timestamp >= _parse_iso_time(start_time))
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid start_time format, use ISO 8601")
    if end_time:
        try:
            query = query.where(ComplianceEvent.timestamp <= _parse_iso_time(end_time))
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid end_time format, use ISO 8601")

    count_query = select(func.count()).select_from(query.subquery())
    total = await db.scalar(count_query) or 0

    query = query.order_by(desc(ComplianceEvent.timestamp))
    query = query.offset((page - 1) * page_size).limit(page_size)

    result = await db.execute(query)
    events = result.scalars().all()
    person_name_map, camera_name_map = await _resolve_related_names(db, events)
    return EventsListResponse(
        events=[_to_event_response(event, person_name_map, camera_name_map) for event in events],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/recent/violations", response_model=List[EventResponse])
async def get_recent_violations(
    limit: int = Query(10, ge=1, le=50),
    db: AsyncSession = Depends(get_database),
):
    query = (
        select(ComplianceEvent)
        .where(ComplianceEvent.is_violation == True)
        .order_by(desc(ComplianceEvent.timestamp))
        .limit(limit)
    )
    result = await db.execute(query)
    events = result.scalars().all()
    person_name_map, camera_name_map = await _resolve_related_names(db, events)
    return [_to_event_response(event, person_name_map, camera_name_map) for event in events]


@router.get("/snapshots/{filename}")
async def get_event_snapshot(filename: str):
    """Serve a legacy local snapshot image by filename."""
    if ".." in filename or "/" in filename or "\\" in filename:
        raise HTTPException(status_code=400, detail="Invalid filename")

    file_path = settings.SNAPSHOTS_DIR / filename
    if not file_path.is_file():
        raise HTTPException(status_code=404, detail="Snapshot not found")

    return FileResponse(str(file_path), media_type="image/jpeg")


@router.get("/videos/{filename}")
async def get_event_video(filename: str):
    """Serve a legacy local event video by filename."""
    if ".." in filename or "/" in filename or "\\" in filename:
        raise HTTPException(status_code=400, detail="Invalid filename")

    file_path = settings.VIDEOS_DIR / filename
    if not file_path.is_file():
        raise HTTPException(status_code=404, detail="Video not found")

    return FileResponse(str(file_path), media_type="video/mp4")


@router.get("/objects/{bucket}/{object_key:path}")
async def get_event_object(bucket: str, object_key: str):
    storage = get_object_storage()
    try:
        response = storage.client.get_object(bucket, object_key)
    except Exception as exc:
        raise HTTPException(status_code=404, detail=f"Object not found: {exc}") from exc

    media_type = getattr(response, "headers", {}).get("Content-Type", "application/octet-stream")

    def _iter_stream():
        try:
            for chunk in response.stream(32 * 1024):
                yield chunk
        finally:
            response.close()
            response.release_conn()

    return StreamingResponse(_iter_stream(), media_type=media_type)


@router.get("/violations/gallery", response_model=GalleryResponse)
async def get_violations_gallery(
    page: int = Query(1, ge=1),
    page_size: int = Query(12, ge=1, le=50),
    camera_id: Optional[str] = None,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
    db: AsyncSession = Depends(get_database),
):
    query = select(ComplianceEvent).where(
        ComplianceEvent.is_violation == True,
        (ComplianceEvent.snapshot_object_key != None)
        | (ComplianceEvent.snapshot_path != None),
    )
    if camera_id:
        query = query.where(ComplianceEvent.camera_id == camera_id)
    if start_time:
        try:
            query = query.where(ComplianceEvent.timestamp >= _parse_iso_time(start_time))
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid start_time format")
    if end_time:
        try:
            query = query.where(ComplianceEvent.timestamp <= _parse_iso_time(end_time))
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid end_time format")

    count_query = select(func.count()).select_from(query.subquery())
    total = await db.scalar(count_query) or 0

    query = query.order_by(desc(ComplianceEvent.timestamp))
    query = query.offset((page - 1) * page_size).limit(page_size)

    result = await db.execute(query)
    events = result.scalars().all()

    items = []
    for event in events:
        missing_ppe = event.missing_ppe or []
        action_violations = event.action_violations or []
        all_violations = list(missing_ppe) + [
            f"action:{action}" for action in action_violations
        ]
        labels = [value.replace("_", " ") for value in all_violations]
        person_label = event.person_id or "unknown person"
        message = (
            f"{person_label} missing {', '.join(labels)}"
            if labels
            else f"{person_label} violation"
        )

        items.append(
            GalleryItem(
                id=event.id,
                snapshot_url=_build_snapshot_url(event),
                timestamp=event.timestamp,
                camera_id=event.camera_id,
                person_id=event.person_id,
                missing_ppe=missing_ppe,
                message=message,
            )
        )
    return GalleryResponse(items=items, total=total, page=page, page_size=page_size)


@router.get("/{event_id}", response_model=EventResponse)
async def get_event(event_id: str, db: AsyncSession = Depends(get_database)):
    query = select(ComplianceEvent).where(ComplianceEvent.id == event_id)
    result = await db.execute(query)
    event = result.scalar_one_or_none()
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    person_name_map, camera_name_map = await _resolve_related_names(db, [event])
    return _to_event_response(event, person_name_map, camera_name_map)
