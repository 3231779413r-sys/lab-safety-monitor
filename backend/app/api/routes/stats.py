from collections import Counter, defaultdict
from datetime import datetime, timedelta
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.danger_events import (
    DANGER_EVENT_TYPES,
    get_danger_event_label,
    match_danger_event_types,
)
from ...models.event import ComplianceEvent
from ...models.person import Person
from ...models.video_source import VideoSource
from ..deps import get_database

router = APIRouter(prefix="/stats", tags=["statistics"])

INTERNAL_EVENT_PREFIXES = ("inspection_presence:",)

PeriodKey = Literal["today", "7d", "30d"]


class SummaryStatsResponse(BaseModel):
    total_events: int
    today_events: int
    total_violations: int
    today_violations: int
    total_persons: int
    compliance_rate: float
    last_updated: str


class TimelinePoint(BaseModel):
    date: str
    violations: int


class PPEBreakdownItem(BaseModel):
    ppe_type: str
    count: int


class VisualizationTypeStat(BaseModel):
    event_type: str
    label: str
    count: int


class VisualizationViolatorStat(BaseModel):
    person_id: str
    person_name: Optional[str] = None
    violation_count: int


class VisualizationCameraTypeStat(BaseModel):
    event_type: str
    label: str
    count: int


class VisualizationCameraStat(BaseModel):
    camera_id: str
    camera_name: str
    violation_count: int
    type_breakdown: list[VisualizationCameraTypeStat]


class VisualizationStatsResponse(BaseModel):
    today_violation_count: int
    week_violation_count: int
    online_camera_count: int
    last_inspection_time: Optional[str] = None
    last_updated: str
    trend_days: int
    trend: list[TimelinePoint]
    type_period: PeriodKey
    type_breakdown: list[VisualizationTypeStat]
    ranking_period: PeriodKey
    top_violators: list[VisualizationViolatorStat]
    top_cameras: list[VisualizationCameraStat]


def _parse_iso_time(s: str) -> datetime:
    s = s.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        import re

        s = re.sub(r"\.\d+", "", s)
        dt = datetime.fromisoformat(s)
    if dt.tzinfo is not None:
        dt = dt.replace(tzinfo=None)
    return dt


def _apply_filters(query, camera_id: Optional[str], start_time: Optional[str], end_time: Optional[str]):
    for prefix in INTERNAL_EVENT_PREFIXES:
        query = query.where(~ComplianceEvent.video_source.like(f"{prefix}%"))
    if camera_id:
        query = query.where(ComplianceEvent.camera_id == camera_id)
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
    return query


def _period_start(now: datetime, period: PeriodKey) -> datetime:
    if period == "today":
        return now.replace(hour=0, minute=0, second=0, microsecond=0)
    days = 7 if period == "7d" else 30
    return (now - timedelta(days=days - 1)).replace(hour=0, minute=0, second=0, microsecond=0)


def _resolve_danger_event_types(row: ComplianceEvent) -> list[str]:
    if row.danger_event_types:
        return row.danger_event_types
    return match_danger_event_types(row.missing_ppe or [], row.action_violations or [])


async def _load_violation_events(
    db: AsyncSession,
    *,
    start_time: Optional[datetime] = None,
    end_time: Optional[datetime] = None,
) -> list[ComplianceEvent]:
    query = select(ComplianceEvent).where(ComplianceEvent.is_violation == True)
    for prefix in INTERNAL_EVENT_PREFIXES:
        query = query.where(~ComplianceEvent.video_source.like(f"{prefix}%"))
    if start_time:
        query = query.where(ComplianceEvent.timestamp >= start_time)
    if end_time:
        query = query.where(ComplianceEvent.timestamp <= end_time)
    query = query.order_by(ComplianceEvent.timestamp.asc())
    result = await db.execute(query)
    return list(result.scalars().all())


async def _resolve_person_names(db: AsyncSession, person_ids: list[str]) -> dict[str, str]:
    if not person_ids:
        return {}
    result = await db.execute(select(Person.id, Person.name).where(Person.id.in_(person_ids)))
    return {person_id: (name or person_id) for person_id, name in result.all()}


async def _resolve_camera_names(db: AsyncSession, camera_ids: list[str]) -> dict[str, str]:
    if not camera_ids:
        return {}
    result = await db.execute(select(VideoSource.id, VideoSource.name).where(VideoSource.id.in_(camera_ids)))
    return {camera_id: (name or camera_id) for camera_id, name in result.all()}


@router.get("/summary", response_model=SummaryStatsResponse)
async def get_summary_stats(
    camera_id: Optional[str] = None,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
    db: AsyncSession = Depends(get_database),
):
    today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)

    total_events_query = _apply_filters(select(func.count(ComplianceEvent.id)), camera_id, start_time, end_time)
    total_events = await db.scalar(total_events_query) or 0

    today_events_query = _apply_filters(select(func.count(ComplianceEvent.id)), camera_id, start_time, end_time)
    today_events_query = today_events_query.where(ComplianceEvent.timestamp >= today_start)
    today_events = await db.scalar(today_events_query) or 0

    total_violations_query = _apply_filters(select(func.count(ComplianceEvent.id)), camera_id, start_time, end_time).where(ComplianceEvent.is_violation == True)
    total_violations = await db.scalar(total_violations_query) or 0

    today_violations_query = _apply_filters(select(func.count(ComplianceEvent.id)), camera_id, start_time, end_time).where(
        ComplianceEvent.is_violation == True,
        ComplianceEvent.timestamp >= today_start,
    )
    today_violations = await db.scalar(today_violations_query) or 0

    total_persons_query = select(func.count(func.distinct(ComplianceEvent.person_id))).where(ComplianceEvent.person_id.is_not(None))
    total_persons_query = _apply_filters(total_persons_query, camera_id, start_time, end_time)
    total_persons = await db.scalar(total_persons_query) or 0

    compliance_rate = 100.0
    if total_events > 0:
        compliance_rate = ((total_events - total_violations) / total_events) * 100

    return SummaryStatsResponse(
        total_events=total_events,
        today_events=today_events,
        total_violations=total_violations,
        today_violations=today_violations,
        total_persons=total_persons,
        compliance_rate=round(compliance_rate, 1),
        last_updated=datetime.now().isoformat(),
    )


@router.get("/timeline", response_model=list[TimelinePoint])
async def get_violation_timeline(
    days: int = 7,
    camera_id: Optional[str] = None,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
    db: AsyncSession = Depends(get_database),
):
    start_date = datetime.now() - timedelta(days=days)
    query = select(func.date(ComplianceEvent.timestamp).label("date"), func.count(ComplianceEvent.id).label("count")).where(
        ComplianceEvent.is_violation == True,
        ComplianceEvent.timestamp >= start_date,
    )
    query = _apply_filters(query, camera_id, start_time, end_time)
    query = query.group_by(func.date(ComplianceEvent.timestamp)).order_by(func.date(ComplianceEvent.timestamp))
    result = await db.execute(query)
    rows = result.all()
    return [TimelinePoint(date=str(row.date), violations=row.count) for row in rows]


@router.get("/by-ppe", response_model=list[PPEBreakdownItem])
async def get_violations_by_ppe(
    camera_id: Optional[str] = None,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
    db: AsyncSession = Depends(get_database),
):
    query = select(ComplianceEvent).where(ComplianceEvent.is_violation == True)
    query = _apply_filters(query, camera_id, start_time, end_time)

    result = await db.execute(query)
    rows = result.scalars().all()

    violation_counts: dict[str, int] = {}
    for row in rows:
        for event_type in _resolve_danger_event_types(row):
            violation_counts[event_type] = violation_counts.get(event_type, 0) + 1

    return [
        PPEBreakdownItem(ppe_type=item, count=count)
        for item, count in sorted(violation_counts.items(), key=lambda pair: -pair[1])
    ]


@router.get("/visualization", response_model=VisualizationStatsResponse)
async def get_visualization_stats(
    trend_days: int = Query(7, ge=7, le=30),
    type_period: PeriodKey = Query("today"),
    ranking_period: PeriodKey = Query("today"),
    camera_period: PeriodKey = Query("7d"),
    db: AsyncSession = Depends(get_database),
):
    if trend_days not in {7, 30}:
        raise HTTPException(status_code=400, detail="trend_days must be 7 or 30")

    now = datetime.now()
    today_start = _period_start(now, "today")
    week_start = (today_start - timedelta(days=today_start.weekday())).replace(
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    )
    trend_start = _period_start(now, "7d" if trend_days == 7 else "30d")
    type_start = _period_start(now, type_period)
    ranking_start = _period_start(now, ranking_period)
    camera_start = _period_start(now, camera_period)
    fetch_start = min(today_start, week_start, trend_start, type_start, ranking_start, camera_start)

    rows = await _load_violation_events(db, start_time=fetch_start)

    danger_rows = []
    for row in rows:
        danger_event_types = _resolve_danger_event_types(row)
        if danger_event_types:
            danger_rows.append(
                {
                    "id": row.id,
                    "person_id": row.person_id,
                    "camera_id": row.camera_id,
                    "camera_name": row.camera_name,
                    "timestamp": row.timestamp,
                    "danger_event_types": danger_event_types,
                }
            )

    trend_counts: dict[str, int] = defaultdict(int)
    for row in danger_rows:
        if row["timestamp"] >= trend_start:
            trend_counts[row["timestamp"].date().isoformat()] += 1

    trend: list[TimelinePoint] = []
    current_date = trend_start.date()
    end_date = now.date()
    while current_date <= end_date:
        key = current_date.isoformat()
        trend.append(TimelinePoint(date=key, violations=trend_counts.get(key, 0)))
        current_date += timedelta(days=1)

    type_counter: Counter[str] = Counter()
    for row in danger_rows:
        if row["timestamp"] >= type_start:
            type_counter.update(row["danger_event_types"])

    type_breakdown = [
        VisualizationTypeStat(
            event_type=event_type,
            label=get_danger_event_label(event_type),
            count=type_counter.get(event_type, 0),
        )
        for event_type in DANGER_EVENT_TYPES
    ]

    violator_counter: Counter[str] = Counter()
    for row in danger_rows:
        person_id = row["person_id"]
        if person_id and row["timestamp"] >= ranking_start:
            violator_counter[person_id] += 1

    top_person_ids = [
        person_id
        for person_id, _count in sorted(
            violator_counter.items(),
            key=lambda item: (-item[1], item[0]),
        )[:5]
    ]
    person_name_map = await _resolve_person_names(db, top_person_ids)
    top_violators = [
        VisualizationViolatorStat(
            person_id=person_id,
            person_name=person_name_map.get(person_id),
            violation_count=violator_counter[person_id],
        )
        for person_id in top_person_ids
    ]

    camera_type_counter: dict[str, Counter[str]] = defaultdict(Counter)
    camera_name_candidates: dict[str, str] = {}
    for row in danger_rows:
        camera_id = row["camera_id"]
        if not camera_id or row["timestamp"] < camera_start:
            continue
        camera_type_counter[camera_id].update(row["danger_event_types"])
        if row["camera_name"]:
            camera_name_candidates[camera_id] = row["camera_name"]

    top_camera_ids = [
        camera_id
        for camera_id, _count in sorted(
            (
                (camera_id, sum(type_counter.values()))
                for camera_id, type_counter in camera_type_counter.items()
            ),
            key=lambda item: (-item[1], item[0]),
        )[:5]
    ]
    camera_name_map = await _resolve_camera_names(db, top_camera_ids)
    top_cameras = [
        VisualizationCameraStat(
            camera_id=camera_id,
            camera_name=camera_name_candidates.get(camera_id) or camera_name_map.get(camera_id, camera_id),
            violation_count=sum(camera_type_counter[camera_id].values()),
            type_breakdown=[
                VisualizationCameraTypeStat(
                    event_type=event_type,
                    label=get_danger_event_label(event_type),
                    count=camera_type_counter[camera_id].get(event_type, 0),
                )
                for event_type in DANGER_EVENT_TYPES
                if camera_type_counter[camera_id].get(event_type, 0) > 0
            ],
        )
        for camera_id in top_camera_ids
    ]

    today_violation_count = sum(1 for row in danger_rows if row["timestamp"] >= today_start)
    week_violation_count = sum(1 for row in danger_rows if row["timestamp"] >= week_start)
    online_camera_count = await db.scalar(
        select(func.count(VideoSource.id)).where(
            VideoSource.source_type == "camera",
            VideoSource.enabled == True,
        )
    ) or 0
    last_inspection_time = await db.scalar(
        select(func.max(VideoSource.last_patrol_at)).where(VideoSource.source_type == "camera")
    )

    return VisualizationStatsResponse(
        today_violation_count=today_violation_count,
        week_violation_count=week_violation_count,
        online_camera_count=online_camera_count,
        last_inspection_time=last_inspection_time.isoformat() if last_inspection_time else None,
        last_updated=now.isoformat(),
        trend_days=trend_days,
        trend=trend,
        type_period=type_period,
        type_breakdown=type_breakdown,
        ranking_period=ranking_period,
        top_violators=top_violators,
        top_cameras=top_cameras,
    )
