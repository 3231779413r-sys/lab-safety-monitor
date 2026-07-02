import json
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel
from sqlalchemy import asc, desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.danger_events import (
    DANGER_EVENT_TYPES,
    PERSONNEL_SELECTABLE_EVENT_TYPES,
    canonicalize_danger_event_key,
    get_danger_event_label,
)
from ...ml.face_recognition import FaceRecognizer
from ...models.external_person import ExternalPerson
from ...models.person import Person
from ...models.supervision import ExternalPersonnelRegistration, VisitorRegistration
from ...models.supervision_settings import SupervisionSettings
from ...models.video_source import VideoSource
from ...services.face_registry_service import get_face_registry_service
from ...services.object_storage import get_object_storage
from ...services.worker_client import (
    WorkerProxyError,
    raise_http_from_worker_error,
    request_worker_json,
    worker_proxy_enabled,
)
from ..deps import get_database


router = APIRouter(prefix="/supervision", tags=["supervision"])


def _parse_event_scope(value: Optional[str]) -> list[str]:
    if not value:
        return []
    try:
        parsed = json.loads(value)
        if isinstance(parsed, list):
            result: list[str] = []
            for item in parsed:
                normalized = canonicalize_danger_event_key(str(item))
                if normalized and normalized not in result:
                    result.append(normalized)
            return result
    except json.JSONDecodeError:
        pass
    result: list[str] = []
    for item in value.split(","):
        normalized = canonicalize_danger_event_key(item)
        if normalized and normalized not in result:
            result.append(normalized)
    return result


def _encode_event_scope(values: list[str]) -> str:
    result: list[str] = []
    for value in values:
        normalized = canonicalize_danger_event_key(value)
        if normalized and normalized not in result:
            result.append(normalized)
    return json.dumps(result, ensure_ascii=False)


def _parse_camera_ids(value: Optional[str]) -> list[str]:
    if not value:
        return []
    try:
        parsed = json.loads(value)
        if isinstance(parsed, list):
            return [str(item).strip() for item in parsed if str(item).strip()]
    except json.JSONDecodeError:
        pass
    return []


def _encode_camera_ids(values: list[str]) -> str:
    result: list[str] = []
    for value in values:
        normalized = str(value).strip()
        if normalized and normalized not in result:
            result.append(normalized)
    return json.dumps(result, ensure_ascii=False)


def _ensure_valid_time_range(start_time: datetime, end_time: datetime) -> None:
    if end_time <= start_time:
        raise HTTPException(status_code=422, detail="结束时间必须晚于开始时间")


def _ensure_valid_clock_time(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized:
        return None
    try:
        datetime.strptime(normalized, "%H:%M")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="区域漏巡开始时间格式必须为 HH:mm") from exc
    return normalized


class SupervisionEventOption(BaseModel):
    key: str
    label: str


class CameraSimpleResponse(BaseModel):
    id: str
    name: str
    floor: Optional[str] = None
    short_name: Optional[str] = None
    enabled: bool


class VisitorRegistrationBase(BaseModel):
    start_time: datetime
    end_time: datetime
    visiting_company: str
    total_people: int


class VisitorRegistrationCreateRequest(VisitorRegistrationBase):
    pass


class VisitorRegistrationUpdateRequest(BaseModel):
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    visiting_company: Optional[str] = None
    total_people: Optional[int] = None


class VisitorRegistrationResponse(VisitorRegistrationBase):
    id: str
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class ExternalPersonnelBase(BaseModel):
    external_person_id: Optional[str] = None
    name: str
    organization: str
    start_time: datetime
    end_time: datetime
    visit_reason: str
    supervision_events: list[str] = []
    allowed_camera_ids: list[str] = []


class ExternalPersonnelCreateRequest(ExternalPersonnelBase):
    pass


class ExternalPersonnelUpdateRequest(BaseModel):
    external_person_id: Optional[str] = None
    name: Optional[str] = None
    organization: Optional[str] = None
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    visit_reason: Optional[str] = None
    supervision_events: Optional[list[str]] = None
    allowed_camera_ids: Optional[list[str]] = None


class ExternalPersonnelResponse(ExternalPersonnelBase):
    id: str
    supervision_event_labels: list[str]
    face_registered: bool
    face_image_url: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class MessageResponse(BaseModel):
    message: str


class SystemSupervisionSettingsRequest(BaseModel):
    other_person_scope: list[str] = []
    area_missed_inspection_enabled: bool = False
    area_missed_inspection_interval_hours: Optional[float] = None
    area_missed_inspection_start_time: Optional[str] = None
    area_missed_inspection_camera_ids: list[str] = []
    blind_spot_stay_enabled: bool = False
    blind_spot_stay_threshold_seconds: Optional[int] = None
    workshop_overcapacity_enabled: bool = False
    workshop_overcapacity_limit: Optional[int] = None
    alert_cooldown_seconds: Optional[int] = None


class SystemSupervisionSettingsResponse(SystemSupervisionSettingsRequest):
    id: str


class FaceMatchCandidate(BaseModel):
    subject_id: str
    subject_type: str
    name: str
    organization: Optional[str] = None
    similarity: float
    cosine_similarity: Optional[float] = None
    face_image_url: Optional[str] = None


class FaceMatchResponse(BaseModel):
    matched: bool
    best_match: Optional[FaceMatchCandidate] = None
    candidates: list[FaceMatchCandidate] = []


def _normalize_db_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone().replace(tzinfo=None)


def _build_visitor_response(model: VisitorRegistration) -> VisitorRegistrationResponse:
    return VisitorRegistrationResponse(
        id=model.id,
        start_time=model.start_time,
        end_time=model.end_time,
        visiting_company=model.visiting_company,
        total_people=model.total_people,
        created_at=model.created_at,
        updated_at=model.updated_at,
    )


def _build_external_response(model: ExternalPersonnelRegistration) -> ExternalPersonnelResponse:
    events = _parse_event_scope(model.supervision_events)
    allowed_camera_ids = _parse_camera_ids(model.allowed_camera_ids)
    return ExternalPersonnelResponse(
        id=model.id,
        external_person_id=getattr(model, "external_person_id", None),
        name=model.name,
        organization=model.organization,
        start_time=model.start_time,
        end_time=model.end_time,
        visit_reason=model.visit_reason,
        supervision_events=events,
        allowed_camera_ids=allowed_camera_ids,
        supervision_event_labels=[get_danger_event_label(item) for item in events],
        face_registered=model.face_image is not None,
        face_image_url=(
            f"/api/events/objects/{model.face_image_bucket}/{model.face_image_object_key}"
            if model.face_image_storage == "minio" and model.face_image_bucket and model.face_image_object_key
            else None
        ),
        created_at=model.created_at,
        updated_at=model.updated_at,
    )


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


async def _get_visitor_or_404(db: AsyncSession, registration_id: str) -> VisitorRegistration:
    registration = await db.get(VisitorRegistration, registration_id)
    if registration is None:
        raise HTTPException(status_code=404, detail="访客登记不存在")
    return registration


async def _get_external_or_404(
    db: AsyncSession, registration_id: str
) -> ExternalPersonnelRegistration:
    registration = await db.get(ExternalPersonnelRegistration, registration_id)
    if registration is None:
        raise HTTPException(status_code=404, detail="外来人员登记不存在")
    return registration


async def _get_or_create_system_settings(db: AsyncSession) -> SupervisionSettings:
    settings_row = (
        await db.execute(
            select(SupervisionSettings).order_by(desc(SupervisionSettings.updated_at)).limit(1)
        )
    ).scalar_one_or_none()
    if settings_row is not None:
        return settings_row

    settings_row = SupervisionSettings(
        other_person_scope=_encode_event_scope([]),
        area_missed_inspection_enabled=0,
        blind_spot_stay_enabled=0,
        workshop_overcapacity_enabled=0,
        alert_cooldown_seconds=300,
    )
    db.add(settings_row)
    await db.commit()
    await db.refresh(settings_row)
    return settings_row


def _build_system_settings_response(
    model: SupervisionSettings,
    patrol_camera_ids: Optional[list[str]] = None,
) -> SystemSupervisionSettingsResponse:
    return SystemSupervisionSettingsResponse(
        id=model.id,
        other_person_scope=_parse_event_scope(getattr(model, "other_person_scope", None)),
        area_missed_inspection_enabled=bool(getattr(model, "area_missed_inspection_enabled", 0)),
        area_missed_inspection_interval_hours=getattr(model, "area_missed_inspection_interval_hours", None),
        area_missed_inspection_start_time=getattr(model, "area_missed_inspection_start_time", None),
        area_missed_inspection_camera_ids=(
            patrol_camera_ids
            if patrol_camera_ids is not None
            else _parse_camera_ids(getattr(model, "area_missed_inspection_camera_ids", None))
        ),
        blind_spot_stay_enabled=bool(getattr(model, "blind_spot_stay_enabled", 0)),
        blind_spot_stay_threshold_seconds=getattr(model, "blind_spot_stay_threshold_seconds", None),
        workshop_overcapacity_enabled=bool(getattr(model, "workshop_overcapacity_enabled", 0)),
        workshop_overcapacity_limit=getattr(model, "workshop_overcapacity_limit", None),
        alert_cooldown_seconds=getattr(model, "alert_cooldown_seconds", 300),
    )


def _camera_short_name(camera: VideoSource) -> str:
    name_suffix = (getattr(camera, "name_suffix", None) or "").strip()
    if name_suffix:
        return name_suffix
    floor = (getattr(camera, "floor", None) or "").strip()
    name = (camera.name or "").strip()
    if floor and name.startswith(floor):
        stripped = name[len(floor):].strip()
        if stripped:
            return stripped
    return name


@router.get("/event-options", response_model=list[SupervisionEventOption])
async def get_supervision_event_options():
    return [
        SupervisionEventOption(key=event_type, label=get_danger_event_label(event_type))
        for event_type in DANGER_EVENT_TYPES
    ]


@router.get("/cameras", response_model=list[CameraSimpleResponse])
async def get_supervision_cameras(db: AsyncSession = Depends(get_database)):
    query = (
        select(VideoSource)
        .where(VideoSource.source_type == "camera")
        .order_by(asc(VideoSource.name))
    )
    cameras = list((await db.execute(query)).scalars().all())
    return [
        CameraSimpleResponse(
            id=camera.id,
            name=camera.name,
            floor=getattr(camera, "floor", None),
            short_name=_camera_short_name(camera),
            enabled=bool(camera.enabled),
        )
        for camera in cameras
    ]


@router.get("/settings", response_model=SystemSupervisionSettingsResponse)
async def get_system_supervision_settings(db: AsyncSession = Depends(get_database)):
    settings_row = await _get_or_create_system_settings(db)
    patrol_camera_ids = list(
        (
            await db.execute(
                select(VideoSource.id)
                .where(
                    VideoSource.source_type == "camera",
                    VideoSource.is_patrol_area == True,
                )
                .order_by(VideoSource.name.asc())
            )
        ).scalars().all()
    )
    return _build_system_settings_response(settings_row, patrol_camera_ids)


@router.put("/settings", response_model=SystemSupervisionSettingsResponse)
async def update_system_supervision_settings(
    request: SystemSupervisionSettingsRequest,
    db: AsyncSession = Depends(get_database),
):
    normalized_patrol_camera_ids = list(
        dict.fromkeys(
            [
                camera_id
                for camera_id in (
                    request.area_missed_inspection_camera_ids
                    if request.area_missed_inspection_enabled
                    else []
                )
                if camera_id
            ]
        )
    )
    if request.area_missed_inspection_enabled:
        if request.area_missed_inspection_interval_hours is None or request.area_missed_inspection_interval_hours <= 0:
            raise HTTPException(status_code=422, detail="区域漏巡检查周期必须大于 0")
        normalized_start_time = _ensure_valid_clock_time(request.area_missed_inspection_start_time)
        if not normalized_start_time:
            raise HTTPException(status_code=422, detail="区域漏巡开始时间不能为空")
    else:
        normalized_start_time = None
    if request.blind_spot_stay_enabled:
        raise HTTPException(status_code=422, detail="盲区驻留功能尚未开放")
    if request.workshop_overcapacity_enabled:
        if request.workshop_overcapacity_limit is None or request.workshop_overcapacity_limit < 0:
            raise HTTPException(status_code=422, detail="车间超员人数阈值不能小于 0")
    if request.alert_cooldown_seconds is not None and request.alert_cooldown_seconds <= 0:
        raise HTTPException(status_code=422, detail="告警去重时间必须大于 0")

    settings_row = await _get_or_create_system_settings(db)
    settings_row.other_person_scope = _encode_event_scope(request.other_person_scope)
    settings_row.area_missed_inspection_enabled = 1 if request.area_missed_inspection_enabled else 0
    settings_row.area_missed_inspection_interval_hours = (
        request.area_missed_inspection_interval_hours if request.area_missed_inspection_enabled else None
    )
    settings_row.area_missed_inspection_start_time = (
        normalized_start_time
        if request.area_missed_inspection_enabled
        else None
    )
    settings_row.area_missed_inspection_camera_ids = (
        _encode_camera_ids(normalized_patrol_camera_ids)
        if request.area_missed_inspection_enabled
        else None
    )
    settings_row.blind_spot_stay_enabled = 0
    settings_row.blind_spot_stay_threshold_seconds = None
    settings_row.workshop_overcapacity_enabled = 1 if request.workshop_overcapacity_enabled else 0
    settings_row.workshop_overcapacity_limit = (
        request.workshop_overcapacity_limit if request.workshop_overcapacity_enabled else None
    )
    settings_row.alert_cooldown_seconds = (
        request.alert_cooldown_seconds
        if request.alert_cooldown_seconds is not None
        else settings.VIOLATION_ALERT_COOLDOWN_SECONDS
    )

    cameras = list(
        (
            await db.execute(
                select(VideoSource).where(VideoSource.source_type == "camera")
            )
        ).scalars().all()
    )
    patrol_camera_id_set = set(normalized_patrol_camera_ids)
    for camera in cameras:
        next_patrol_area = camera.id in patrol_camera_id_set
        if bool(getattr(camera, "is_patrol_area", False)) != next_patrol_area:
            camera.is_patrol_area = next_patrol_area
            if not next_patrol_area:
                camera.last_patrol_at = None
                camera.last_patrol_person_id = None
                camera.last_patrol_person_name = None
                camera.last_patrol_evaluated_window_end = None

    await db.commit()
    await db.refresh(settings_row)
    return _build_system_settings_response(settings_row, normalized_patrol_camera_ids)


@router.get("/visitors/active", response_model=list[VisitorRegistrationResponse])
async def list_active_visitors(
    now: Optional[datetime] = Query(None),
    db: AsyncSession = Depends(get_database),
):
    current = _normalize_db_datetime(now) if now is not None else datetime.now()
    query = (
        select(VisitorRegistration)
        .where(VisitorRegistration.end_time >= current)
        .order_by(asc(VisitorRegistration.start_time), asc(VisitorRegistration.created_at))
    )
    items = list((await db.execute(query)).scalars().all())
    return [_build_visitor_response(item) for item in items]


@router.get("/visitors/history", response_model=list[VisitorRegistrationResponse])
async def list_visitor_history(db: AsyncSession = Depends(get_database)):
    query = select(VisitorRegistration).order_by(desc(VisitorRegistration.start_time))
    items = list((await db.execute(query)).scalars().all())
    return [_build_visitor_response(item) for item in items]


@router.post("/visitors", response_model=VisitorRegistrationResponse)
async def create_visitor_registration(
    request: VisitorRegistrationCreateRequest,
    db: AsyncSession = Depends(get_database),
):
    start_time = _normalize_db_datetime(request.start_time)
    end_time = _normalize_db_datetime(request.end_time)
    _ensure_valid_time_range(start_time, end_time)
    if request.total_people <= 0:
        raise HTTPException(status_code=422, detail="人数必须大于 0")

    registration = VisitorRegistration(
        start_time=start_time,
        end_time=end_time,
        visiting_company=request.visiting_company.strip(),
        total_people=request.total_people,
    )
    db.add(registration)
    await db.commit()
    await db.refresh(registration)
    return _build_visitor_response(registration)


@router.patch("/visitors/{registration_id}", response_model=VisitorRegistrationResponse)
async def update_visitor_registration(
    registration_id: str,
    request: VisitorRegistrationUpdateRequest,
    db: AsyncSession = Depends(get_database),
):
    registration = await _get_visitor_or_404(db, registration_id)
    start_time = (
        _normalize_db_datetime(request.start_time)
        if request.start_time is not None
        else registration.start_time
    )
    end_time = (
        _normalize_db_datetime(request.end_time)
        if request.end_time is not None
        else registration.end_time
    )
    _ensure_valid_time_range(start_time, end_time)

    if request.start_time is not None:
        registration.start_time = start_time
    if request.end_time is not None:
        registration.end_time = end_time
    if request.visiting_company is not None:
        registration.visiting_company = request.visiting_company.strip()
    if request.total_people is not None:
        if request.total_people <= 0:
            raise HTTPException(status_code=422, detail="人数必须大于 0")
        registration.total_people = request.total_people

    await db.commit()
    await db.refresh(registration)
    return _build_visitor_response(registration)


@router.delete("/visitors/{registration_id}", response_model=MessageResponse)
async def delete_visitor_registration(
    registration_id: str,
    db: AsyncSession = Depends(get_database),
):
    registration = await _get_visitor_or_404(db, registration_id)
    await db.delete(registration)
    await db.commit()
    return MessageResponse(message=f"已删除访客登记 {registration_id}")


@router.get("/external/active", response_model=list[ExternalPersonnelResponse])
async def list_active_external_personnel(
    now: Optional[datetime] = Query(None),
    db: AsyncSession = Depends(get_database),
):
    current = _normalize_db_datetime(now) if now is not None else datetime.now()
    query = (
        select(ExternalPersonnelRegistration)
        .where(ExternalPersonnelRegistration.end_time >= current)
        .order_by(asc(ExternalPersonnelRegistration.start_time), asc(ExternalPersonnelRegistration.created_at))
    )
    items = list((await db.execute(query)).scalars().all())
    return [_build_external_response(item) for item in items]


@router.get("/external/history", response_model=list[ExternalPersonnelResponse])
async def list_external_personnel_history(db: AsyncSession = Depends(get_database)):
    query = select(ExternalPersonnelRegistration).order_by(
        desc(ExternalPersonnelRegistration.start_time)
    )
    items = list((await db.execute(query)).scalars().all())
    return [_build_external_response(item) for item in items]


@router.post("/external", response_model=ExternalPersonnelResponse)
async def create_external_personnel_registration(
    request: ExternalPersonnelCreateRequest,
    db: AsyncSession = Depends(get_database),
):
    start_time = _normalize_db_datetime(request.start_time)
    end_time = _normalize_db_datetime(request.end_time)
    _ensure_valid_time_range(start_time, end_time)
    normalized_events = _parse_event_scope(_encode_event_scope(request.supervision_events))
    normalized_camera_ids = list(dict.fromkeys(request.allowed_camera_ids))
    external_person = None
    if request.external_person_id:
        external_person = await db.get(ExternalPerson, request.external_person_id)
        if external_person is None:
            raise HTTPException(status_code=404, detail="关联的外来人员不存在")
        if not normalized_events:
            normalized_events = _parse_event_scope(external_person.supervision_scope)
        if not normalized_camera_ids:
            normalized_camera_ids = _parse_camera_ids(getattr(external_person, "allowed_camera_ids", None))
    if "unauthorized_intrusion" in normalized_events and not normalized_camera_ids:
        raise HTTPException(status_code=422, detail="选择违规闯入时必须设置允许出现的监控画面")

    registration = ExternalPersonnelRegistration(
        external_person_id=request.external_person_id,
        name=(external_person.name if external_person is not None else request.name).strip(),
        organization=(external_person.organization if external_person is not None else request.organization).strip(),
        start_time=start_time,
        end_time=end_time,
        visit_reason=request.visit_reason.strip(),
        supervision_events=_encode_event_scope(normalized_events),
        allowed_camera_ids=_encode_camera_ids(normalized_camera_ids),
    )
    db.add(registration)
    await db.commit()
    await db.refresh(registration)
    if external_person is not None and external_person.face_image_bucket and external_person.face_image_object_key:
        registration.face_embedding = external_person.face_embedding
        registration.face_image_storage = external_person.face_image_storage
        registration.face_image_bucket = external_person.face_image_bucket
        registration.face_image_object_key = external_person.face_image_object_key
        registration.face_image_content_type = external_person.face_image_content_type
        registration.face_image_size_bytes = external_person.face_image_size_bytes
        await db.commit()
        await db.refresh(registration)
    return _build_external_response(registration)


@router.patch("/external/{registration_id}", response_model=ExternalPersonnelResponse)
async def update_external_personnel_registration(
    registration_id: str,
    request: ExternalPersonnelUpdateRequest,
    db: AsyncSession = Depends(get_database),
):
    registration = await _get_external_or_404(db, registration_id)
    external_person = None
    if request.external_person_id:
        external_person = await db.get(ExternalPerson, request.external_person_id)
        if external_person is None:
            raise HTTPException(status_code=404, detail="关联的外来人员不存在")
    start_time = (
        _normalize_db_datetime(request.start_time)
        if request.start_time is not None
        else registration.start_time
    )
    end_time = (
        _normalize_db_datetime(request.end_time)
        if request.end_time is not None
        else registration.end_time
    )
    _ensure_valid_time_range(start_time, end_time)

    supervision_events = (
        _parse_event_scope(_encode_event_scope(request.supervision_events))
        if request.supervision_events is not None
        else (
            _parse_event_scope(external_person.supervision_scope)
            if external_person is not None
            else _parse_event_scope(registration.supervision_events)
        )
    )
    allowed_camera_ids = (
        list(dict.fromkeys(request.allowed_camera_ids))
        if request.allowed_camera_ids is not None
        else (
            _parse_camera_ids(getattr(external_person, "allowed_camera_ids", None))
            if external_person is not None
            else _parse_camera_ids(registration.allowed_camera_ids)
        )
    )
    if "unauthorized_intrusion" in supervision_events and not allowed_camera_ids:
        raise HTTPException(status_code=422, detail="选择违规闯入时必须设置允许出现的监控画面")

    if request.external_person_id is not None:
        registration.external_person_id = request.external_person_id
        if external_person is not None:
            registration.name = external_person.name.strip()
            registration.organization = external_person.organization.strip()
    if request.name is not None and external_person is None:
        registration.name = request.name.strip()
    if request.organization is not None and external_person is None:
        registration.organization = request.organization.strip()
    if request.start_time is not None:
        registration.start_time = start_time
    if request.end_time is not None:
        registration.end_time = end_time
    if request.visit_reason is not None:
        registration.visit_reason = request.visit_reason.strip()
    if request.supervision_events is not None or external_person is not None:
        registration.supervision_events = _encode_event_scope(supervision_events)
    if request.allowed_camera_ids is not None or external_person is not None:
        registration.allowed_camera_ids = _encode_camera_ids(allowed_camera_ids)
    if external_person is not None and external_person.face_image_bucket and external_person.face_image_object_key:
        registration.face_embedding = external_person.face_embedding
        registration.face_image_storage = external_person.face_image_storage
        registration.face_image_bucket = external_person.face_image_bucket
        registration.face_image_object_key = external_person.face_image_object_key
        registration.face_image_content_type = external_person.face_image_content_type
        registration.face_image_size_bytes = external_person.face_image_size_bytes

    await db.commit()
    await db.refresh(registration)
    return _build_external_response(registration)


@router.post("/external/{registration_id}/face", response_model=ExternalPersonnelResponse)
async def upload_external_personnel_face(
    registration_id: str,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_database),
):
    if worker_proxy_enabled():
        content = await file.read()
        if not content:
            raise HTTPException(status_code=400, detail="上传文件为空")
        try:
            result = await request_worker_json(
                "POST",
                f"/internal/supervision/external/{registration_id}/face",
                files={
                    "file": (
                        file.filename or f"{registration_id}.jpg",
                        content,
                        file.content_type or "application/octet-stream",
                    )
                },
            )
            return ExternalPersonnelResponse(**result)
        except WorkerProxyError as exc:
            raise_http_from_worker_error(exc)

    registration = await _get_external_or_404(db, registration_id)
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="上传文件为空")

    try:
        result = get_face_registry_service().register_face_image(
            content,
            subject_type="external_registration",
            subject_id=registration.id,
            filename=file.filename or f"{registration.id}.jpg",
            content_type=file.content_type,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    registration.face_image = content
    registration.face_embedding = result.embedding_bytes
    registration.face_image_storage = "minio"
    registration.face_image_bucket = result.stored_object.bucket
    registration.face_image_object_key = result.stored_object.object_key
    registration.face_image_content_type = result.stored_object.content_type
    registration.face_image_size_bytes = result.stored_object.size_bytes
    await db.commit()
    await db.refresh(registration)
    return _build_external_response(registration)


@router.delete("/external/{registration_id}", response_model=MessageResponse)
async def delete_external_personnel_registration(
    registration_id: str,
    db: AsyncSession = Depends(get_database),
):
    registration = await _get_external_or_404(db, registration_id)
    await db.delete(registration)
    await db.commit()
    return MessageResponse(message=f"已删除外来人员登记 {registration_id}")


@router.post("/face-match", response_model=FaceMatchResponse)
async def compare_face_against_registry(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_database),
):
    if worker_proxy_enabled():
        content = await file.read()
        if not content:
            raise HTTPException(status_code=400, detail="上传文件为空")
        try:
            result = await request_worker_json(
                "POST",
                "/internal/supervision/face-match",
                files={
                    "file": (
                        file.filename or "face-match.jpg",
                        content,
                        file.content_type or "application/octet-stream",
                    )
                },
            )
            return FaceMatchResponse(**result)
        except WorkerProxyError as exc:
            raise_http_from_worker_error(exc)

    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="上传文件为空")

    try:
        embedding, _ = get_face_registry_service().recognizer.extract_embedding_from_image_bytes(content)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    candidates: list[FaceMatchCandidate] = []

    employee_rows = list(
        (await db.execute(select(Person).where(Person.face_embedding.isnot(None), Person.is_employee == True))).scalars().all()
    )
    for person in employee_rows:
        stored_embedding = FaceRecognizer.deserialize_embedding(person.face_embedding)
        similarity = get_face_registry_service().recognizer.compare_embeddings(embedding, stored_embedding)
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

    external_person_rows = list(
        (await db.execute(select(ExternalPerson).where(ExternalPerson.face_embedding.isnot(None)))).scalars().all()
    )
    for person in external_person_rows:
        stored_embedding = FaceRecognizer.deserialize_embedding(person.face_embedding)
        similarity = get_face_registry_service().recognizer.compare_embeddings(embedding, stored_embedding)
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
        (await db.execute(select(ExternalPersonnelRegistration).where(ExternalPersonnelRegistration.face_embedding.isnot(None)))).scalars().all()
    )
    for person in registration_rows:
        stored_embedding = FaceRecognizer.deserialize_embedding(person.face_embedding)
        similarity = get_face_registry_service().recognizer.compare_embeddings(embedding, stored_embedding)
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
    return FaceMatchResponse(
        matched=best_match is not None,
        best_match=best_match,
        candidates=candidates[:10],
    )
