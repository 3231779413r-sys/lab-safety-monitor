from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.realtime_bus import publish_realtime_message
from ..models.event import ComplianceEvent
from ..models.inspection_window_patrol import InspectionWindowPatrolRecord
from ..models.person import Person
from ..models.shift_schedule import ShiftSchedule
from ..models.supervision_settings import SupervisionSettings
from ..models.video_source import VideoSource
from .event_service import EventService


INSPECTOR_JOB_TITLE = "巡检人员"


def _parse_camera_ids(value: Optional[str]) -> list[str]:
    if not value:
        return []
    try:
        parsed = json.loads(value)
        if isinstance(parsed, list):
            return [str(item) for item in parsed if str(item).strip()]
    except json.JSONDecodeError:
        pass
    return [item.strip() for item in value.split(",") if item.strip()]


def _parse_person_ids(value: Optional[str]) -> list[str]:
    if not value:
        return []
    try:
        parsed = json.loads(value)
        if isinstance(parsed, list):
            return [str(item) for item in parsed if str(item).strip()]
    except json.JSONDecodeError:
        pass
    return [item.strip() for item in value.split(",") if item.strip()]


def _combine_names(names: list[str]) -> str:
    return "、".join([name for name in names if name.strip()])


@dataclass
class InspectionWindow:
    start: datetime
    end: datetime
    key: str


class InspectionService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.event_service = EventService(session)

    async def mark_inspection_presence(
        self,
        *,
        person_id: Optional[str],
        camera_id: Optional[str],
        timestamp: datetime,
    ) -> None:
        if not person_id or not camera_id:
            return
        person = await self.session.get(Person, person_id)
        if person is None or person.job_title != INSPECTOR_JOB_TITLE:
            return
        camera = await self.session.get(VideoSource, camera_id)
        if camera is None or not bool(getattr(camera, "is_patrol_area", False)):
            return

        settings_row = (
            await self.session.execute(
                select(SupervisionSettings).order_by(SupervisionSettings.updated_at.desc()).limit(1)
            )
        ).scalar_one_or_none()
        if settings_row is None or not bool(settings_row.area_missed_inspection_enabled):
            return

        interval_hours = getattr(settings_row, "area_missed_inspection_interval_hours", None)
        start_time_text = getattr(settings_row, "area_missed_inspection_start_time", None)
        if not interval_hours or interval_hours <= 0 or not start_time_text:
            return

        window = self._resolve_window_for_timestamp(timestamp, start_time_text, interval_hours)
        if window is None:
            return

        record = (
            await self.session.execute(
                select(InspectionWindowPatrolRecord).where(
                    InspectionWindowPatrolRecord.camera_id == camera.id,
                    InspectionWindowPatrolRecord.window_start == window.start,
                    InspectionWindowPatrolRecord.window_end == window.end,
                )
            )
        ).scalar_one_or_none()
        if record is None:
            record = InspectionWindowPatrolRecord(
                camera_id=camera.id,
                window_start=window.start,
                window_end=window.end,
                first_patrol_at=timestamp,
                last_patrol_at=timestamp,
                person_id=person.id,
                person_name=person.name,
            )
            self.session.add(record)
        else:
            if record.first_patrol_at is None or timestamp < record.first_patrol_at:
                record.first_patrol_at = timestamp
            if record.last_patrol_at is None or timestamp >= record.last_patrol_at:
                record.last_patrol_at = timestamp
                record.person_id = person.id
                record.person_name = person.name

        if camera.last_patrol_at is None or timestamp >= camera.last_patrol_at:
            camera.last_patrol_at = timestamp
            camera.last_patrol_person_id = person.id
            camera.last_patrol_person_name = person.name

    async def evaluate_area_missed_inspection(self, *, now: Optional[datetime] = None) -> None:
        current = now or datetime.now()
        settings_row = (
            await self.session.execute(
                select(SupervisionSettings).order_by(SupervisionSettings.updated_at.desc()).limit(1)
            )
        ).scalar_one_or_none()
        if settings_row is None or not bool(settings_row.area_missed_inspection_enabled):
            return

        interval_hours = getattr(settings_row, "area_missed_inspection_interval_hours", None)
        start_time_text = getattr(settings_row, "area_missed_inspection_start_time", None)
        if not interval_hours or interval_hours <= 0 or not start_time_text:
            return

        window = self._resolve_last_completed_window(current, start_time_text, interval_hours)
        if window is None:
            return

        cameras = (
            await self.session.execute(
                select(VideoSource)
                .where(
                    VideoSource.source_type == "camera",
                    VideoSource.is_patrol_area == True,
                )
                .order_by(VideoSource.name.asc())
            )
        ).scalars().all()
        if not cameras:
            return
        if all(
            camera.last_patrol_evaluated_window_end is not None
            and camera.last_patrol_evaluated_window_end >= window.end
            for camera in cameras
        ):
            return
        camera_map = {camera.id: camera for camera in cameras}
        ordered_camera_ids = [camera.id for camera in cameras]

        on_duty_persons = await self._get_on_duty_inspectors(current)
        on_duty_names = [person.name for person in on_duty_persons if person.name]
        person_name = _combine_names(on_duty_names) or "未排班"

        patrol_records = (
            await self.session.execute(
                select(InspectionWindowPatrolRecord).where(
                    InspectionWindowPatrolRecord.camera_id.in_(ordered_camera_ids),
                    InspectionWindowPatrolRecord.window_start == window.start,
                    InspectionWindowPatrolRecord.window_end == window.end,
                )
            )
        ).scalars().all()
        inspected_camera_ids = {
            record.camera_id
            for record in patrol_records
            if record.last_patrol_at is not None
        }
        if inspected_camera_ids == set():
            await self._create_window_event(
                event_type="missed_inspection",
                window=window,
                person_name=person_name,
                camera_ids=ordered_camera_ids,
                camera_name="",
                video_source=f"inspection_window:{window.key}:missed",
            )
        else:
            leaked_camera_ids = [camera_id for camera_id in ordered_camera_ids if camera_id not in inspected_camera_ids]
            if leaked_camera_ids:
                leaked_camera_names = [camera_map[camera_id].name or camera_id for camera_id in leaked_camera_ids]
                await self._create_window_event(
                    event_type="area_missed_inspection",
                    window=window,
                    person_name=person_name,
                    camera_ids=leaked_camera_ids,
                    camera_name="、".join(leaked_camera_names),
                    video_source=f"inspection_window:{window.key}:area",
                )

        for camera in cameras:
            camera.last_patrol_evaluated_window_end = window.end

    async def _create_window_event(
        self,
        *,
        event_type: str,
        window: InspectionWindow,
        person_name: str,
        camera_ids: list[str],
        camera_name: str,
        video_source: str,
    ) -> None:
        existing = (
            await self.session.execute(
                select(ComplianceEvent.id).where(ComplianceEvent.video_source == video_source).limit(1)
            )
        ).scalar_one_or_none()
        if existing:
            return

        primary_camera_id = camera_ids[0] if event_type == "area_missed_inspection" and camera_ids else None
        event = await self.event_service.create_event(
            person_id=None,
            track_id=None,
            timestamp=window.end,
            video_source=video_source,
            camera_id=primary_camera_id,
            frame_number=0,
            detected_ppe=[],
            missing_ppe=[],
            action_violations=[event_type],
            danger_event_types=[event_type],
            is_violation=True,
            person_name=person_name,
            camera_ids=camera_ids,
            camera_name=camera_name,
            is_ongoing=False,
            end_timestamp=window.end,
            duration_frames=0,
            start_frame=0,
        )
        message = f"{camera_name} {person_name} 区域漏巡" if event_type == "area_missed_inspection" else f"{person_name} 未巡检"
        await publish_realtime_message(
            {
                "type": "violation",
                "title": "区域漏巡" if event_type == "area_missed_inspection" else "未巡检",
                "message": message.strip(),
                "timestamp": window.end.isoformat(),
                "severity": "error",
                "event_id": event.id,
                "person_id": None,
                "person_name": person_name,
                "missing_ppe": [event_type],
                "violation_labels": ["区域漏巡" if event_type == "area_missed_inspection" else "未巡检"],
                "snapshot_filename": None,
                "snapshot_path": None,
                "snapshot_url": None,
                "camera_id": primary_camera_id,
                "camera_ids": camera_ids,
                "camera_name": camera_name,
            }
        )

    async def _get_on_duty_inspectors(self, current: datetime) -> list[Person]:
        shift_date, shift_type = self._resolve_shift(current)
        schedule = (
            await self.session.execute(
                select(ShiftSchedule).where(ShiftSchedule.shift_date == shift_date).limit(1)
            )
        ).scalar_one_or_none()
        if schedule is None:
            return []
        scheduled_ids = _parse_person_ids(
            schedule.day_person_ids if shift_type == "day" else schedule.night_person_ids
        )
        if not scheduled_ids:
            return []
        result = await self.session.execute(
            select(Person)
            .where(Person.id.in_(scheduled_ids), Person.job_title == INSPECTOR_JOB_TITLE)
        )
        persons = result.scalars().all()
        order = {person_id: index for index, person_id in enumerate(scheduled_ids)}
        return sorted(persons, key=lambda person: order.get(person.id, 10**9))

    def _resolve_shift(self, current: datetime) -> tuple[date, str]:
        now_time = current.time()
        if time(8, 0) <= now_time < time(20, 0):
            return current.date(), "day"
        if now_time >= time(20, 0):
            return current.date(), "night"
        return current.date() - timedelta(days=1), "night"

    def _resolve_last_completed_window(
        self, current: datetime, start_time_text: str, interval_hours: float
    ) -> Optional[InspectionWindow]:
        try:
            hour_text, minute_text = start_time_text.split(":", 1)
            start_clock = time(int(hour_text), int(minute_text))
        except (ValueError, TypeError):
            return None
        base_start = datetime.combine(current.date(), start_clock)
        while base_start > current:
            base_start -= timedelta(days=1)
        interval_seconds = max(1, int(interval_hours * 3600))
        elapsed_seconds = int((current - base_start).total_seconds())
        completed_windows = elapsed_seconds // interval_seconds
        if completed_windows <= 0:
            return None
        window_index = completed_windows - 1
        window_start = base_start + timedelta(seconds=window_index * interval_seconds)
        window_end = window_start + timedelta(seconds=interval_seconds)
        return InspectionWindow(
            start=window_start,
            end=window_end,
            key=f"{window_start.isoformat()}_{window_end.isoformat()}",
        )

    def _resolve_window_for_timestamp(
        self, current: datetime, start_time_text: str, interval_hours: float
    ) -> Optional[InspectionWindow]:
        try:
            hour_text, minute_text = start_time_text.split(":", 1)
            start_clock = time(int(hour_text), int(minute_text))
        except (ValueError, TypeError):
            return None
        base_start = datetime.combine(current.date(), start_clock)
        while base_start > current:
            base_start -= timedelta(days=1)
        interval_seconds = max(1, int(interval_hours * 3600))
        elapsed_seconds = int((current - base_start).total_seconds())
        window_index = elapsed_seconds // interval_seconds
        window_start = base_start + timedelta(seconds=window_index * interval_seconds)
        window_end = window_start + timedelta(seconds=interval_seconds)
        return InspectionWindow(
            start=window_start,
            end=window_end,
            key=f"{window_start.isoformat()}_{window_end.isoformat()}",
        )
