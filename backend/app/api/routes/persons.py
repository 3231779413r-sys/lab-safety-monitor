import json
from datetime import date, datetime, time, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel
from sqlalchemy import and_, delete, desc, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..deps import get_database
from ...core.danger_events import (
    PERSONNEL_SELECTABLE_EVENT_TYPES,
    canonicalize_danger_event_key,
    get_danger_event_label,
)
from ...models.external_person import ExternalPerson
from ...models.job_title_option import JobTitleOption
from ...models.event import ComplianceEvent
from ...models.person import Person
from ...models.shift_schedule import ShiftSchedule
from ...services.face_registry_service import get_face_registry_service
from ...services.object_storage import get_object_storage
from ...services.worker_client import (
    WorkerProxyError,
    raise_http_from_worker_error,
    request_worker_json,
    worker_proxy_enabled,
)


router = APIRouter(prefix="/persons", tags=["persons"])


class PersonManagementRow(BaseModel):
    id: str
    name: Optional[str]
    workshop: Optional[str]
    job_title: Optional[str]
    supervision_scope: list[str]
    supervision_scope_labels: list[str]
    face_registered: bool
    face_image_url: Optional[str] = None
    today_violation_count: int
    seven_day_violation_count: int
    thirty_day_violation_count: int
    first_seen: datetime
    last_seen: datetime


class PersonDetailResponse(PersonManagementRow):
    total_events: int
    violation_count: int
    compliance_rate: float


class PersonListResponse(BaseModel):
    persons: list[PersonManagementRow]
    total: int


class PersonCreateRequest(BaseModel):
    name: str
    workshop: Optional[str] = None
    job_title: Optional[str] = None
    supervision_scope: list[str] = []


class PersonUpdateRequest(BaseModel):
    name: Optional[str] = None
    workshop: Optional[str] = None
    job_title: Optional[str] = None
    supervision_scope: Optional[list[str]] = None


class ShiftScheduleRowResponse(BaseModel):
    id: str
    shift_date: date
    day_person_ids: list[str]
    day_person_names: list[str]
    night_person_ids: list[str]
    night_person_names: list[str]


class ShiftScheduleListResponse(BaseModel):
    items: list[ShiftScheduleRowResponse]
    total: int
    page: int
    page_size: int
    has_more: bool


class ShiftScheduleUpdateRequest(BaseModel):
    shift_date: date
    day_person_ids: list[str] = []
    night_person_ids: list[str] = []


class ShiftScheduleCreateRequest(BaseModel):
    base_shift_date: Optional[date] = None


class PersonDeleteResponse(BaseModel):
    message: str


class ExternalPersonManagementRow(BaseModel):
    id: str
    name: str
    organization: str
    supervision_scope: list[str]
    supervision_scope_labels: list[str]
    allowed_camera_ids: list[str]
    face_registered: bool
    face_image_url: Optional[str] = None
    created_at: datetime
    updated_at: Optional[datetime] = None


class ExternalPersonListResponse(BaseModel):
    persons: list[ExternalPersonManagementRow]
    total: int


class ExternalPersonCreateRequest(BaseModel):
    name: str
    organization: str
    supervision_scope: list[str] = []
    allowed_camera_ids: list[str] = []


class ExternalPersonUpdateRequest(BaseModel):
    name: Optional[str] = None
    organization: Optional[str] = None
    supervision_scope: Optional[list[str]] = None
    allowed_camera_ids: Optional[list[str]] = None


class TopViolatorResponse(BaseModel):
    id: str
    name: Optional[str]
    first_seen: datetime
    last_seen: datetime
    total_events: int
    violation_count: int
    compliance_rate: float


class SupervisionEventOption(BaseModel):
    key: str
    label: str


class JobTitleOptionResponse(BaseModel):
    id: str
    code: str
    name: str
    sort_order: int


def _parse_supervision_scope(value: Optional[str]) -> list[str]:
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

    result = []
    for item in value.split(","):
        normalized = canonicalize_danger_event_key(item)
        if normalized and normalized not in result:
            result.append(normalized)
    return result


def _encode_supervision_scope(values: list[str]) -> str:
    unique_values: list[str] = []
    for value in values:
        normalized = canonicalize_danger_event_key(value)
        if normalized and normalized not in unique_values:
            unique_values.append(normalized)
    return json.dumps(unique_values, ensure_ascii=False)


def _parse_camera_ids(value: Optional[str]) -> list[str]:
    if not value:
        return []
    try:
        parsed = json.loads(value)
        if isinstance(parsed, list):
            return [str(item).strip() for item in parsed if str(item).strip()]
    except json.JSONDecodeError:
        pass
    return [item.strip() for item in value.split(",") if item.strip()]


def _encode_camera_ids(values: list[str]) -> str:
    unique_values: list[str] = []
    for value in values:
        clean_value = str(value).strip()
        if clean_value and clean_value not in unique_values:
            unique_values.append(clean_value)
    return json.dumps(unique_values, ensure_ascii=False)


def _parse_shift_person_ids(value: Optional[str]) -> list[str]:
    if not value:
        return []
    try:
        parsed = json.loads(value)
        if isinstance(parsed, list):
            return [str(item).strip() for item in parsed if str(item).strip()]
    except json.JSONDecodeError:
        pass
    return [item.strip() for item in value.split(",") if item.strip()]


def _encode_shift_person_ids(values: list[str]) -> str:
    unique_values: list[str] = []
    for value in values:
        clean_value = str(value).strip()
        if clean_value and clean_value not in unique_values:
            unique_values.append(clean_value)
    return json.dumps(unique_values, ensure_ascii=False)


def _scope_labels(values: list[str]) -> list[str]:
    return [get_danger_event_label(value) for value in values]


async def _validate_job_title(db: AsyncSession, job_title: Optional[str]) -> Optional[str]:
    if job_title is None:
        return None
    normalized = job_title.strip()
    if not normalized:
        return None
    option = (
        await db.execute(
            select(JobTitleOption).where(
                JobTitleOption.name == normalized,
                JobTitleOption.is_active == True,
            )
        )
    ).scalar_one_or_none()
    if option is None:
        raise HTTPException(status_code=422, detail=f"无效岗位: {normalized}")
    return option.name


def _schedule_primary_id(values: list[str]) -> Optional[str]:
    return values[0] if values else None


def _face_image_url(
    storage: Optional[str],
    bucket: Optional[str],
    object_key: Optional[str],
) -> Optional[str]:
    if storage == "minio" and bucket and object_key:
        return f"/api/events/objects/{bucket}/{object_key}"
    return None


def _build_person_row(
    person: Person,
    violation_counts: dict[str, dict[str, int]],
) -> PersonManagementRow:
    counts = violation_counts.get(person.id, {})
    supervision_scope = _parse_supervision_scope(person.supervision_scope)
    return PersonManagementRow(
        id=person.id,
        name=person.name,
        workshop=person.workshop,
        job_title=person.job_title,
        supervision_scope=supervision_scope,
        supervision_scope_labels=_scope_labels(supervision_scope),
        face_registered=person.face_embedding is not None or person.thumbnail is not None,
        face_image_url=_face_image_url(
            getattr(person, "face_image_storage", None),
            getattr(person, "face_image_bucket", None),
            getattr(person, "face_image_object_key", None),
        ),
        today_violation_count=counts.get("today", 0),
        seven_day_violation_count=counts.get("7d", 0),
        thirty_day_violation_count=counts.get("30d", 0),
        first_seen=person.first_seen,
        last_seen=person.last_seen,
    )


def _build_external_person_row(person: ExternalPerson) -> ExternalPersonManagementRow:
    supervision_scope = _parse_supervision_scope(person.supervision_scope)
    allowed_camera_ids = _parse_camera_ids(getattr(person, "allowed_camera_ids", None))
    return ExternalPersonManagementRow(
        id=person.id,
        name=person.name,
        organization=person.organization,
        supervision_scope=supervision_scope,
        supervision_scope_labels=_scope_labels(supervision_scope),
        allowed_camera_ids=allowed_camera_ids,
        face_registered=(
            person.face_embedding is not None
            or person.thumbnail is not None
            or (
                getattr(person, "face_image_storage", None) == "minio"
                and getattr(person, "face_image_bucket", None)
                and getattr(person, "face_image_object_key", None)
            )
        ),
        face_image_url=_face_image_url(
            getattr(person, "face_image_storage", None),
            getattr(person, "face_image_bucket", None),
            getattr(person, "face_image_object_key", None),
        ),
        created_at=person.created_at,
        updated_at=person.updated_at,
    )


async def _resolve_person_names(db: AsyncSession, person_ids: list[str]) -> dict[str, str]:
    if not person_ids:
        return {}
    result = await db.execute(select(Person.id, Person.name).where(Person.id.in_(person_ids)))
    return {person_id: (name or person_id) for person_id, name in result.all()}


async def _collect_violation_counts(
    db: AsyncSession,
    person_ids: list[str],
) -> dict[str, dict[str, int]]:
    if not person_ids:
        return {}

    now = datetime.now()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    seven_day_start = (now - timedelta(days=6)).replace(hour=0, minute=0, second=0, microsecond=0)
    thirty_day_start = (now - timedelta(days=29)).replace(hour=0, minute=0, second=0, microsecond=0)

    query = (
        select(ComplianceEvent.person_id, ComplianceEvent.timestamp)
        .where(
            ComplianceEvent.is_violation == True,
            ComplianceEvent.person_id.in_(person_ids),
            ComplianceEvent.timestamp >= thirty_day_start,
        )
    )
    rows = (await db.execute(query)).all()

    counts: dict[str, dict[str, int]] = {
        person_id: {"today": 0, "7d": 0, "30d": 0} for person_id in person_ids
    }
    for person_id, timestamp in rows:
        if person_id is None:
            continue
        if timestamp >= thirty_day_start:
            counts[person_id]["30d"] += 1
        if timestamp >= seven_day_start:
            counts[person_id]["7d"] += 1
        if timestamp >= today_start:
            counts[person_id]["today"] += 1
    return counts


async def _get_person_or_404(db: AsyncSession, person_id: str) -> Person:
    person = await db.get(Person, person_id)
    if not person or not person.is_employee:
        raise HTTPException(status_code=404, detail="员工不存在")
    return person


async def _get_external_person_or_404(db: AsyncSession, person_id: str) -> ExternalPerson:
    person = await db.get(ExternalPerson, person_id)
    if person is None:
        raise HTTPException(status_code=404, detail="外来人员不存在")
    return person


async def _cleanup_invalid_employees(db: AsyncSession) -> None:
    employees = list(
        (
            await db.execute(
                select(Person).where(Person.is_employee == True)
            )
        ).scalars().all()
    )
    invalid_ids: list[str] = []
    for person in employees:
        name = (person.name or "").strip()
        lower_name = name.lower()
        if (
            not name
            or name == person.id
            or lower_name.startswith("track_")
            or lower_name.startswith("unknown")
            or lower_name.startswith("person_")
        ):
            invalid_ids.append(person.id)

    if invalid_ids:
        await db.execute(delete(Person).where(Person.id.in_(invalid_ids)))
    await db.commit()


async def _ensure_today_schedule(db: AsyncSession) -> ShiftSchedule:
    today = date.today()
    schedule = (
        await db.execute(select(ShiftSchedule).where(ShiftSchedule.shift_date == today))
    ).scalar_one_or_none()
    if schedule is not None:
        return schedule

    if datetime.now().time() >= time(hour=12):
        schedule = ShiftSchedule(
            shift_date=today,
            day_person_ids=_encode_shift_person_ids([]),
            night_person_ids=_encode_shift_person_ids([]),
            day_person_id=None,
            night_person_id=None,
        )
        db.add(schedule)
        await db.commit()
        await db.refresh(schedule)
        return schedule

    return ShiftSchedule(
        id=f"schedule-{today.isoformat()}",
        shift_date=today,
        day_person_ids=_encode_shift_person_ids([]),
        night_person_ids=_encode_shift_person_ids([]),
        day_person_id=None,
        night_person_id=None,
    )


def _build_schedule_row(
    schedule: ShiftSchedule,
    person_name_map: dict[str, str],
) -> ShiftScheduleRowResponse:
    day_person_ids = _parse_shift_person_ids(schedule.day_person_ids) or (
        [schedule.day_person_id] if schedule.day_person_id else []
    )
    night_person_ids = _parse_shift_person_ids(schedule.night_person_ids) or (
        [schedule.night_person_id] if schedule.night_person_id else []
    )
    return ShiftScheduleRowResponse(
        id=schedule.id,
        shift_date=schedule.shift_date,
        day_person_ids=day_person_ids,
        day_person_names=[person_name_map.get(person_id, person_id) for person_id in day_person_ids],
        night_person_ids=night_person_ids,
        night_person_names=[person_name_map.get(person_id, person_id) for person_id in night_person_ids],
    )


@router.get("", response_model=PersonListResponse)
async def get_persons(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    search: Optional[str] = None,
    db: AsyncSession = Depends(get_database),
):
    await _cleanup_invalid_employees(db)
    base_query = select(Person).where(Person.is_employee == True)
    count_query = select(func.count(Person.id)).where(Person.is_employee == True)

    if search:
        like_value = f"%{search.strip()}%"
        base_query = base_query.where(Person.name.ilike(like_value))
        count_query = count_query.where(Person.name.ilike(like_value))

    total = await db.scalar(count_query) or 0
    query = (
        base_query.order_by(desc(Person.last_seen), desc(Person.first_seen))
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    persons = list((await db.execute(query)).scalars().all())
    violation_counts = await _collect_violation_counts(db, [person.id for person in persons])

    return PersonListResponse(
        persons=[_build_person_row(person, violation_counts) for person in persons],
        total=total,
    )


@router.get("/external", response_model=ExternalPersonListResponse)
async def get_external_persons(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    search: Optional[str] = None,
    db: AsyncSession = Depends(get_database),
):
    base_query = select(ExternalPerson)
    count_query = select(func.count(ExternalPerson.id))

    if search:
        like_value = f"%{search.strip()}%"
        base_query = base_query.where(ExternalPerson.name.ilike(like_value))
        count_query = count_query.where(ExternalPerson.name.ilike(like_value))

    total = await db.scalar(count_query) or 0
    query = (
        base_query.order_by(desc(ExternalPerson.updated_at), desc(ExternalPerson.created_at))
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    persons = list((await db.execute(query)).scalars().all())
    return ExternalPersonListResponse(
        persons=[_build_external_person_row(person) for person in persons],
        total=total,
    )


@router.get("/supervision-events", response_model=list[SupervisionEventOption])
async def get_supervision_events():
    return [
        SupervisionEventOption(key=event_type, label=get_danger_event_label(event_type))
        for event_type in PERSONNEL_SELECTABLE_EVENT_TYPES
    ]


@router.get("/job-titles", response_model=list[JobTitleOptionResponse])
async def get_job_title_options(db: AsyncSession = Depends(get_database)):
    rows = list(
        (
            await db.execute(
                select(JobTitleOption)
                .where(JobTitleOption.is_active == True)
                .order_by(JobTitleOption.sort_order.asc(), JobTitleOption.created_at.asc())
            )
        ).scalars().all()
    )
    return [
        JobTitleOptionResponse(
            id=row.id,
            code=row.code,
            name=row.name,
            sort_order=row.sort_order,
        )
        for row in rows
    ]


@router.get("/top/violators", response_model=list[TopViolatorResponse])
async def get_top_violators(
    limit: int = Query(5, ge=1, le=50),
    db: AsyncSession = Depends(get_database),
):
    query = (
        select(Person)
        .where(Person.is_employee == True)
        .order_by(desc(Person.violation_count), desc(Person.last_seen))
        .limit(limit)
    )
    persons = list((await db.execute(query)).scalars().all())
    return [
        TopViolatorResponse(
            id=person.id,
            name=person.name,
            first_seen=person.first_seen,
            last_seen=person.last_seen,
            total_events=person.total_events or 0,
            violation_count=person.violation_count or 0,
            compliance_rate=person.compliance_rate,
        )
        for person in persons
    ]


@router.post("", response_model=PersonDetailResponse)
async def create_person(
    request: PersonCreateRequest,
    db: AsyncSession = Depends(get_database),
):
    job_title = await _validate_job_title(db, request.job_title)
    person = Person(
        name=request.name.strip(),
        is_employee=True,
        workshop=request.workshop,
        job_title=job_title,
        supervision_scope=_encode_supervision_scope(request.supervision_scope),
        first_seen=datetime.now(),
        last_seen=datetime.now(),
        total_events=0,
        violation_count=0,
    )
    db.add(person)
    await db.commit()
    await db.refresh(person)
    row = _build_person_row(person, {person.id: {"today": 0, "7d": 0, "30d": 0}})
    return PersonDetailResponse(
        **row.model_dump(),
        total_events=person.total_events or 0,
        violation_count=person.violation_count or 0,
        compliance_rate=person.compliance_rate,
    )


@router.post("/external", response_model=ExternalPersonManagementRow)
async def create_external_person(
    request: ExternalPersonCreateRequest,
    db: AsyncSession = Depends(get_database),
):
    normalized_scope = _encode_supervision_scope(request.supervision_scope)
    parsed_scope = _parse_supervision_scope(normalized_scope)
    if "unauthorized_intrusion" in parsed_scope and not request.allowed_camera_ids:
        raise HTTPException(status_code=422, detail="选择违规闯入时必须设置允许出现的监控画面")
    person = ExternalPerson(
        name=request.name.strip(),
        organization=request.organization.strip(),
        supervision_scope=normalized_scope,
        allowed_camera_ids=_encode_camera_ids(request.allowed_camera_ids),
    )
    db.add(person)
    await db.commit()
    await db.refresh(person)
    return _build_external_person_row(person)


@router.get("/schedule/today", response_model=ShiftScheduleRowResponse)
async def get_today_schedule(db: AsyncSession = Depends(get_database)):
    schedule = await _ensure_today_schedule(db)

    day_person_ids = _parse_shift_person_ids(schedule.day_person_ids) or (
        [schedule.day_person_id] if schedule.day_person_id else []
    )
    night_person_ids = _parse_shift_person_ids(schedule.night_person_ids) or (
        [schedule.night_person_id] if schedule.night_person_id else []
    )
    person_name_map = await _resolve_person_names(db, day_person_ids + night_person_ids)
    return _build_schedule_row(schedule, person_name_map)


@router.get("/schedule/history", response_model=ShiftScheduleListResponse)
async def get_schedule_history(
    page: int = Query(1, ge=1),
    page_size: int = Query(30, ge=1, le=100),
    db: AsyncSession = Depends(get_database),
):
    await _ensure_today_schedule(db)
    count_query = select(func.count(ShiftSchedule.id))
    total = await db.scalar(count_query) or 0
    query = (
        select(ShiftSchedule)
        .order_by(desc(ShiftSchedule.shift_date))
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    schedules = list((await db.execute(query)).scalars().all())

    person_ids: list[str] = []
    for schedule in schedules:
        person_ids.extend(_parse_shift_person_ids(schedule.day_person_ids))
        person_ids.extend(_parse_shift_person_ids(schedule.night_person_ids))
        if schedule.day_person_id:
            person_ids.append(schedule.day_person_id)
        if schedule.night_person_id:
            person_ids.append(schedule.night_person_id)

    person_name_map = await _resolve_person_names(db, list(dict.fromkeys(person_ids)))
    items = [_build_schedule_row(schedule, person_name_map) for schedule in schedules]
    return ShiftScheduleListResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        has_more=page * page_size < total,
    )


@router.put("/schedule", response_model=ShiftScheduleRowResponse)
async def upsert_schedule(
    request: ShiftScheduleUpdateRequest,
    db: AsyncSession = Depends(get_database),
):
    normalized_day_ids = list(dict.fromkeys([person_id for person_id in request.day_person_ids if person_id]))
    normalized_night_ids = list(dict.fromkeys([person_id for person_id in request.night_person_ids if person_id]))

    schedule = (
        await db.execute(select(ShiftSchedule).where(ShiftSchedule.shift_date == request.shift_date))
    ).scalar_one_or_none()

    if schedule is None:
        schedule = ShiftSchedule(
            shift_date=request.shift_date,
            day_person_ids=_encode_shift_person_ids(normalized_day_ids),
            night_person_ids=_encode_shift_person_ids(normalized_night_ids),
            day_person_id=_schedule_primary_id(normalized_day_ids),
            night_person_id=_schedule_primary_id(normalized_night_ids),
        )
        db.add(schedule)
    else:
        schedule.day_person_ids = _encode_shift_person_ids(normalized_day_ids)
        schedule.night_person_ids = _encode_shift_person_ids(normalized_night_ids)
        schedule.day_person_id = _schedule_primary_id(normalized_day_ids)
        schedule.night_person_id = _schedule_primary_id(normalized_night_ids)

    await db.commit()
    await db.refresh(schedule)
    person_name_map = await _resolve_person_names(db, normalized_day_ids + normalized_night_ids)
    return _build_schedule_row(schedule, person_name_map)


@router.post("/schedule/next", response_model=ShiftScheduleRowResponse)
async def create_next_schedule(
    request: ShiftScheduleCreateRequest,
    db: AsyncSession = Depends(get_database),
):
    await _ensure_today_schedule(db)
    latest_schedule = (
        await db.execute(select(ShiftSchedule).order_by(desc(ShiftSchedule.shift_date)).limit(1))
    ).scalar_one_or_none()

    if request.base_shift_date is not None:
        next_date = request.base_shift_date + timedelta(days=1)
    elif latest_schedule is not None:
        next_date = max(latest_schedule.shift_date, date.today()) + timedelta(days=1)
    else:
        next_date = date.today() + timedelta(days=1)

    existing = (
        await db.execute(select(ShiftSchedule).where(ShiftSchedule.shift_date == next_date))
    ).scalar_one_or_none()
    if existing is None:
        existing = ShiftSchedule(
            shift_date=next_date,
            day_person_ids=_encode_shift_person_ids([]),
            night_person_ids=_encode_shift_person_ids([]),
            day_person_id=None,
            night_person_id=None,
        )
        db.add(existing)
        await db.commit()
        await db.refresh(existing)

    return ShiftScheduleRowResponse(
        id=existing.id,
        shift_date=existing.shift_date,
        day_person_ids=[],
        day_person_names=[],
        night_person_ids=[],
        night_person_names=[],
    )


@router.get("/{person_id}", response_model=PersonDetailResponse)
async def get_person(person_id: str, db: AsyncSession = Depends(get_database)):
    person = await _get_person_or_404(db, person_id)
    counts = await _collect_violation_counts(db, [person.id])
    row = _build_person_row(person, counts)
    return PersonDetailResponse(
        **row.model_dump(),
        total_events=person.total_events or 0,
        violation_count=person.violation_count or 0,
        compliance_rate=person.compliance_rate,
    )


@router.patch("/{person_id}", response_model=PersonDetailResponse)
async def update_person(
    person_id: str,
    update: PersonUpdateRequest,
    db: AsyncSession = Depends(get_database),
):
    person = await _get_person_or_404(db, person_id)

    if update.name is not None:
        person.name = update.name.strip() if update.name else None
    if update.workshop is not None:
        person.workshop = update.workshop
    if update.job_title is not None:
        person.job_title = await _validate_job_title(db, update.job_title)
    if update.supervision_scope is not None:
        person.supervision_scope = _encode_supervision_scope(update.supervision_scope)

    await db.commit()
    await db.refresh(person)
    counts = await _collect_violation_counts(db, [person.id])
    row = _build_person_row(person, counts)
    return PersonDetailResponse(
        **row.model_dump(),
        total_events=person.total_events or 0,
        violation_count=person.violation_count or 0,
        compliance_rate=person.compliance_rate,
    )


@router.patch("/external/{person_id}", response_model=ExternalPersonManagementRow)
async def update_external_person(
    person_id: str,
    update: ExternalPersonUpdateRequest,
    db: AsyncSession = Depends(get_database),
):
    person = await _get_external_person_or_404(db, person_id)

    if update.name is not None:
        person.name = update.name.strip()
    if update.organization is not None:
        person.organization = update.organization.strip()
    if update.supervision_scope is not None:
        person.supervision_scope = _encode_supervision_scope(update.supervision_scope)
    if update.allowed_camera_ids is not None:
        person.allowed_camera_ids = _encode_camera_ids(update.allowed_camera_ids)

    scope = _parse_supervision_scope(person.supervision_scope)
    allowed_camera_ids = _parse_camera_ids(getattr(person, "allowed_camera_ids", None))
    if "unauthorized_intrusion" in scope and not allowed_camera_ids:
        raise HTTPException(status_code=422, detail="选择违规闯入时必须设置允许出现的监控画面")

    await db.commit()
    await db.refresh(person)
    return _build_external_person_row(person)


@router.post("/{person_id}/face", response_model=PersonDetailResponse)
async def upload_person_face(
    person_id: str,
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
                f"/internal/persons/{person_id}/face",
                files={
                    "file": (
                        file.filename or f"{person_id}.jpg",
                        content,
                        file.content_type or "application/octet-stream",
                    )
                },
            )
            return PersonDetailResponse(**result)
        except WorkerProxyError as exc:
            raise_http_from_worker_error(exc)

    person = await _get_person_or_404(db, person_id)
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="上传文件为空")

    try:
        result = get_face_registry_service().register_face_image(
            content,
            subject_type="employee",
            subject_id=person.id,
            filename=file.filename or f"{person.id}.jpg",
            content_type=file.content_type,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    person.thumbnail = result.thumbnail_bytes
    person.face_embedding = result.embedding_bytes
    person.face_image_storage = "minio"
    person.face_image_bucket = result.stored_object.bucket
    person.face_image_object_key = result.stored_object.object_key
    person.face_image_content_type = result.stored_object.content_type
    person.face_image_size_bytes = result.stored_object.size_bytes
    await db.commit()
    await db.refresh(person)
    counts = await _collect_violation_counts(db, [person.id])
    row = _build_person_row(person, counts)
    return PersonDetailResponse(
        **row.model_dump(),
        total_events=person.total_events or 0,
        violation_count=person.violation_count or 0,
        compliance_rate=person.compliance_rate,
    )


@router.post("/external/{person_id}/face", response_model=ExternalPersonManagementRow)
async def upload_external_person_face(
    person_id: str,
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
                f"/internal/persons/external/{person_id}/face",
                files={
                    "file": (
                        file.filename or f"{person_id}.jpg",
                        content,
                        file.content_type or "application/octet-stream",
                    )
                },
            )
            return ExternalPersonManagementRow(**result)
        except WorkerProxyError as exc:
            raise_http_from_worker_error(exc)

    person = await _get_external_person_or_404(db, person_id)
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="上传文件为空")

    try:
        result = get_face_registry_service().register_face_image(
            content,
            subject_type="external_person",
            subject_id=person.id,
            filename=file.filename or f"{person.id}.jpg",
            content_type=file.content_type,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    person.thumbnail = result.thumbnail_bytes
    person.face_embedding = result.embedding_bytes
    person.face_image_storage = "minio"
    person.face_image_bucket = result.stored_object.bucket
    person.face_image_object_key = result.stored_object.object_key
    person.face_image_content_type = result.stored_object.content_type
    person.face_image_size_bytes = result.stored_object.size_bytes
    await db.commit()
    await db.refresh(person)
    return _build_external_person_row(person)


@router.delete("/{person_id}", response_model=PersonDeleteResponse)
async def delete_person(person_id: str, db: AsyncSession = Depends(get_database)):
    person = await _get_person_or_404(db, person_id)
    schedules = (await db.execute(select(ShiftSchedule))).scalars().all()
    for schedule in schedules:
        day_ids = _parse_shift_person_ids(schedule.day_person_ids)
        night_ids = _parse_shift_person_ids(schedule.night_person_ids)
        next_day_ids = [value for value in day_ids if value != person_id]
        next_night_ids = [value for value in night_ids if value != person_id]

        if next_day_ids != day_ids:
            schedule.day_person_ids = _encode_shift_person_ids(next_day_ids)
            if getattr(schedule, "day_person_id", None) == person_id:
                schedule.day_person_id = next_day_ids[0] if next_day_ids else None

        if next_night_ids != night_ids:
            schedule.night_person_ids = _encode_shift_person_ids(next_night_ids)
            if getattr(schedule, "night_person_id", None) == person_id:
                schedule.night_person_id = next_night_ids[0] if next_night_ids else None

    events = (
        await db.execute(select(ComplianceEvent).where(ComplianceEvent.person_id == person_id))
    ).scalars().all()
    for event in events:
        if not event.person_name:
            event.person_name = person.name or person_id
        event.person_id = None

    await db.delete(person)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail="该员工仍有关联数据，暂时无法删除") from exc
    return PersonDeleteResponse(message=f"已删除员工 {person_id}")


@router.delete("/external/{person_id}", response_model=PersonDeleteResponse)
async def delete_external_person(person_id: str, db: AsyncSession = Depends(get_database)):
    person = await _get_external_person_or_404(db, person_id)
    await db.delete(person)
    await db.commit()
    return PersonDeleteResponse(message=f"已删除外来人员 {person_id}")
