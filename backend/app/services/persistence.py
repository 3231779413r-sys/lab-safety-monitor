import logging
import asyncio
from typing import Dict, Any, Optional, List, Tuple
from datetime import datetime
from uuid import uuid4

import numpy as np
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.config import settings
from ..core.danger_events import (
    canonicalize_danger_event_key,
    canonicalize_danger_event_values,
    get_danger_event_label,
    match_danger_event_types,
)
from ..core.websocket import manager as ws_manager
from ..core.realtime_bus import publish_realtime_message
from ..services.event_service import EventService
from ..services.object_storage import get_object_storage
from ..services.person_service import PersonService
from ..services.deduplication import get_deduplication_manager, DeduplicationManager
from ..services.inspection_service import InspectionService
from ..models.video_source import VideoSource


logger = logging.getLogger(__name__)


VIOLATION_LABEL_MAP = {
    "no_goggles": "未佩戴护目镜",
    "no_mask": "未佩戴口罩",
    "no_lab_coat": "未穿实验服",
    "lab_coat": "实验服",
    "no_gloves": "未佩戴防护手套",
    "no_head_mask": "未戴头套",
    "head_mask": "头套",
    "no_safety_vest": "未穿戴安全背心",
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
    normalized = canonicalize_danger_event_key(value)
    if normalized.startswith("action:"):
        normalized = canonicalize_danger_event_key(normalized.split(":", 1)[1])
    if normalized in {
        "hardhat",
        "mask",
        "safety_vest",
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


def to_python_type(value):
    """将numpy类型转换为原生Python类型。"""
    if isinstance(value, np.integer):
        return int(value)
    elif isinstance(value, np.floating):
        return float(value)
    elif isinstance(value, np.ndarray):
        return value.tolist()
    elif isinstance(value, np.bool_):
        return bool(value)
    return value


class PersistenceManager:
    """协调事件和人员记录的持久化，包括去重处理。"""

    def __init__(self, session: AsyncSession):
        self.session = session
        self.event_service = EventService(session)
        self.person_service = PersonService(session)
        self.dedup_manager = get_deduplication_manager()
        self.inspection_service = InspectionService(session)

    async def _capture_person_dataset_frame(
        self,
        *,
        snapshot_frame: Any,
        timestamp: datetime,
        camera_id: Optional[str],
        frame_number: int,
    ) -> None:
        def _upload() -> None:
            storage = get_object_storage()
            object_key = storage.build_person_dataset_key(
                timestamp=timestamp,
                camera_id=camera_id,
                frame_number=frame_number,
            )
            storage.upload_jpeg_frame(
                snapshot_frame,
                object_key=object_key,
                bucket=storage.dataset_bucket,
                quality=85,
            )

        try:
            await asyncio.to_thread(_upload)
        except Exception as exc:
            logger.warning("Failed to upload person dataset frame: %s", exc)

    def _schedule_person_dataset_capture(
        self,
        *,
        snapshot_frame: Any,
        timestamp: datetime,
        camera_id: Optional[str],
        frame_number: int,
    ) -> None:
        task = asyncio.create_task(
            self._capture_person_dataset_frame(
                snapshot_frame=snapshot_frame,
                timestamp=timestamp,
                camera_id=camera_id,
                frame_number=frame_number,
            )
        )

        def _log_failure(finished_task: asyncio.Task) -> None:
            try:
                finished_task.result()
            except Exception as exc:
                logger.warning("Failed to upload person dataset frame: %s", exc)

        task.add_done_callback(_log_failure)

    def _build_cooldown_identity(
        self,
        *,
        person_id: Optional[str],
        face_matched: bool,
        camera_id: Optional[str],
        tracking_key: Optional[str],
        face_embedding: Optional[Any],
        timestamp: datetime,
        cooldown_seconds: int,
    ) -> str:
        if face_matched and person_id:
            base_identity = f"person:{person_id}"
        else:
            camera_part = camera_id or "unknown_camera"
            tracking_part = tracking_key or "unknown_track"
            base_identity = f"unmatched:{camera_part}:{tracking_part}"
        return self.dedup_manager.resolve_identity_key(
            base_identity_key=base_identity,
            embedding=face_embedding,
            timestamp=timestamp,
            cooldown_seconds=cooldown_seconds,
            matched_person_id=person_id if face_matched and person_id else None,
        )

    def _normalize_box(self, box: Any) -> Optional[list[float]]:
        if not isinstance(box, (list, tuple)) or len(box) != 4:
            return None
        normalized = [float(to_python_type(value)) for value in box]
        x1, y1, x2, y2 = normalized
        if x2 <= x1 or y2 <= y1:
            return None
        return normalized

    def _build_snapshot_overlay(
        self,
        *,
        person: Dict[str, Any],
        snapshot_frame: Any,
        missing_ppe: List[str],
        action_violations: List[str],
    ) -> Optional[Dict[str, Any]]:
        if snapshot_frame is None or not hasattr(snapshot_frame, "shape"):
            return None

        frame_height, frame_width = snapshot_frame.shape[:2]
        boxes: list[dict[str, Any]] = []
        person_box = self._normalize_box(person.get("box"))
        if person_box is not None:
            boxes.append(
                {
                    "kind": "person",
                    "label": "人员",
                    "box": person_box,
                }
            )

        seen_boxes: set[tuple[str, tuple[float, float, float, float]]] = set()
        for detection in person.get("ppe_detections", []) or []:
            if not detection.get("is_violation"):
                continue
            violation_key = canonicalize_danger_event_key(str(detection.get("label", "")))
            if violation_key not in missing_ppe:
                continue
            box = self._normalize_box(detection.get("box"))
            if box is None:
                continue
            dedup_key = (violation_key, tuple(box))
            if dedup_key in seen_boxes:
                continue
            seen_boxes.add(dedup_key)
            boxes.append(
                {
                    "kind": "missing_ppe",
                    "label": _format_violation_label(violation_key),
                    "violation_key": violation_key,
                    "box": box,
                }
            )

        for action in person.get("action_violations", []) or []:
            action_name = canonicalize_danger_event_key(str(action.get("action", "")))
            if action_name not in action_violations:
                continue
            box = self._normalize_box(action.get("box"))
            if box is None:
                continue
            dedup_key = (action_name, tuple(box))
            if dedup_key in seen_boxes:
                continue
            seen_boxes.add(dedup_key)
            boxes.append(
                {
                    "kind": "action_violation",
                    "label": _format_violation_label(action_name),
                    "violation_key": action_name,
                    "box": box,
                }
            )

        if not boxes:
            return None

        return {
            "image_width": int(frame_width),
            "image_height": int(frame_height),
            "boxes": boxes,
        }

    async def persist_frame_results(
        self, result: Dict[str, Any], snapshot_frame
    ) -> Dict[str, Any]:
        """
        从帧结果中持久化事件并更新人员记录。

        使用去重处理防止为持续的违规创建重复事件。
        仅在以下情况下创建新事件：
        - 新违规开始
        - 违规类型改变（不同的缺失PPE）

        返回：
            包含'created_events'和'closed_events'计数的字典
        """
        persons = result.get("persons", [])
        frame_number = to_python_type(result.get("frame_number", 0))
        timestamp_str = result.get("timestamp")
        timestamp = (
            datetime.fromisoformat(timestamp_str) if timestamp_str else datetime.now()
        )
        cooldown_seconds = int(
            result.get("alert_cooldown_seconds") or settings.VIOLATION_ALERT_COOLDOWN_SECONDS
        )

        created_events = 0
        created_event_ids: list[str] = []
        closed_events = 0

        camera_name_cache: dict[str, str] = {}
        queued_updates: list[dict[str, Any]] = []
        video_source = result.get("video_source", "unknown")
        camera_id = None
        if video_source and video_source.startswith("camera:"):
            camera_id = video_source.replace("camera:", "")

        if (
            settings.ENABLE_PERSON_DATASET_CAPTURE
            and snapshot_frame is not None
            and persons
        ):
            self._schedule_person_dataset_capture(
                snapshot_frame=snapshot_frame,
                timestamp=timestamp,
                camera_id=camera_id,
                frame_number=frame_number,
            )

        # Process each person in the frame
        for person in persons:
            # Convert track_id to native Python int
            track_id = to_python_type(person.get("track_id"))

            person_id = person.get("person_id")
            tracking_key = person.get("tracking_key") or person_id
            person_name = person.get("person_name") or "未知人员"
            face_embedding = person.get("face_embedding")
            thumbnail_bytes = person.get("thumbnail")
            face_matched = bool(person.get("face_matched"))
            cooldown_identity = self._build_cooldown_identity(
                person_id=person_id,
                face_matched=face_matched,
                camera_id=camera_id,
                tracking_key=tracking_key,
                face_embedding=face_embedding,
                timestamp=timestamp,
                cooldown_seconds=cooldown_seconds,
            )

            if face_matched and person_id:
                await self.person_service.get_or_create_person(
                    person_id,
                    face_embedding,
                    name=person_name,
                    thumbnail=thumbnail_bytes,
                )

            # Get violation info from temporal filter results
            is_stable_violation = person.get("stable_violation", False)
            stable_missing_ppe = canonicalize_danger_event_values(
                person.get("stable_missing_ppe", [])
            )
            action_violations = person.get("action_violations", [])
            action_violations = [
                item
                for item in action_violations
                if canonicalize_danger_event_key(str(item.get("action", "")))
                != "workshop_overcapacity"
            ]

            camera_name = camera_id or video_source or "监控点"
            if camera_id:
                if camera_id not in camera_name_cache:
                    camera = await self.session.get(VideoSource, camera_id)
                    camera_name_cache[camera_id] = (
                        camera.name if camera and camera.name else camera_id
                    )
                camera_name = camera_name_cache[camera_id]

            if face_matched and person_id:
                person_record = await self.person_service.get_person(person_id)
                if person_record and person_record.name:
                    person_name = person_record.name
                await self.inspection_service.mark_inspection_presence(
                    person_id=person_id,
                    camera_id=camera_id,
                    timestamp=timestamp,
                )

            # Special violations like fall detection are immediate violations
            has_action_violation = len(action_violations) > 0

            # Combine missing PPE with action violations for deduplication
            all_violations = list(stable_missing_ppe) if is_stable_violation else []
            for av in action_violations:
                action_name = canonicalize_danger_event_key(
                    str(av.get("action", "unknown"))
                )
                if action_name:
                    all_violations.append(f"action:{action_name}")
            all_violations = canonicalize_danger_event_values(all_violations)

            # Capture active violation state BEFORE dedup (to detect expansions)
            active_before = self.dedup_manager.get_active_violation(
                tracking_key, video_source
            )
            violations_before: set = set()
            if active_before:
                violations_before = active_before.missing_ppe.copy()
                for a in active_before.actions:
                    violations_before.add(f"action:{a}")

            # Use deduplication to determine if we should create an event
            should_create, ended_event_id, reason, final_ppe = (
                self.dedup_manager.should_create_event(
                    person_id=tracking_key,
                    video_source=video_source,
                    missing_ppe=all_violations,
                    frame_number=frame_number,
                )
            )
            alertable_violations = self.dedup_manager.get_alertable_violations(
                cooldown_identity,
                all_violations,
                timestamp,
                cooldown_seconds,
            )
            should_rotate_for_cooldown = (
                reason == "continuing"
                and bool(active_before)
                and bool(alertable_violations)
                and set(all_violations).issubset(violations_before)
            )
            if should_rotate_for_cooldown and active_before:
                should_create = True
                ended_event_id = active_before.event_id
                final_ppe = {
                    "ppe": list(active_before.missing_ppe),
                    "actions": list(active_before.actions),
                }
                reason = "cooldown"

            # Close ended event if any
            if ended_event_id:
                # Extract PPE and actions from final violations dict
                final_ppe_list = []
                final_actions_list = []

                if isinstance(final_ppe, dict):
                    final_ppe_list = final_ppe.get("ppe", [])
                    final_actions_list = final_ppe.get("actions", [])
                elif isinstance(final_ppe, list):
                    # Backward compatibility: if it's still a list, separate them
                    for item in final_ppe:
                        normalized_item = canonicalize_danger_event_key(item)
                        if normalized_item.startswith("action:"):
                            final_actions_list.append(normalized_item.replace("action:", ""))
                        else:
                            final_ppe_list.append(normalized_item)

                await self.event_service.close_event(
                    event_id=ended_event_id,
                    end_frame=frame_number - 1,  # Ended on previous frame
                    end_timestamp=timestamp,
                    final_missing_ppe=final_ppe_list,
                )
                closed_events += 1

            # Create new event if needed
            if should_create and alertable_violations and (is_stable_violation or has_action_violation):
                # Generate event ID
                event_id = str(uuid4())

                # Save snapshot for new violations
                snapshot_object = None
                snapshot_url = None
                if settings.ENABLE_SNAPSHOT_CAPTURE and snapshot_frame is not None:
                    snapshot_object = await self.event_service.save_snapshot(
                        snapshot_frame,
                        event_id=event_id,
                        timestamp=timestamp,
                        camera_id=camera_id,
                    )
                    snapshot_url = (
                        f"/api/events/objects/{snapshot_object.bucket}/{snapshot_object.object_key}"
                    )

                # Prepare action violations for storage
                alertable_missing_ppe = [
                    violation for violation in stable_missing_ppe if violation in alertable_violations
                ]
                alertable_action_violation_names = [
                    canonicalize_danger_event_key(violation.replace("action:", ""))
                    for violation in alertable_violations
                    if violation.startswith("action:")
                ]
                snapshot_overlay = self._build_snapshot_overlay(
                    person=person,
                    snapshot_frame=snapshot_frame,
                    missing_ppe=alertable_missing_ppe,
                    action_violations=alertable_action_violation_names,
                )

                # Create the event
                event_person_id = person_id if face_matched and person_id else None
                event = await self.event_service.create_event(
                    person_id=event_person_id,
                    track_id=track_id,
                    timestamp=timestamp,
                    video_source=video_source,
                    camera_id=camera_id,
                    frame_number=frame_number,
                    detected_ppe=person.get("detected_ppe", []),
                    missing_ppe=alertable_missing_ppe,
                    action_violations=alertable_action_violation_names,
                    danger_event_types=match_danger_event_types(
                        alertable_missing_ppe,
                        alertable_action_violation_names,
                    ),
                    is_violation=True,
                    detection_confidence=person.get("detection_confidence"),
                    snapshot_overlay=snapshot_overlay,
                    snapshot_path=None,
                    snapshot_storage="minio" if snapshot_object else None,
                    snapshot_bucket=snapshot_object.bucket if snapshot_object else None,
                    snapshot_object_key=snapshot_object.object_key if snapshot_object else None,
                    snapshot_content_type=snapshot_object.content_type if snapshot_object else None,
                    snapshot_size_bytes=snapshot_object.size_bytes if snapshot_object else None,
                    start_frame=frame_number,
                    event_id=event_id,
                    person_name=person_name,
                    camera_ids=[camera_id] if camera_id else [],
                    camera_name=camera_name,
                )

                # Register with deduplication manager
                self.dedup_manager.register_event(
                    event_id=event.id,
                    person_id=tracking_key,
                    video_source=video_source,
                    missing_ppe=alertable_violations,
                    frame_number=frame_number,
                    timestamp=timestamp,
                )
                self.dedup_manager.mark_alert_created(
                    cooldown_identity,
                    alertable_violations,
                    timestamp,
                )

                # Update person stats
                if event_person_id:
                    await self.person_service.increment_event_counts(person_id, True)

                chinese_labels = [_format_violation_label(v) for v in alertable_violations]
                violation_type = "、".join(chinese_labels) if chinese_labels else "危险行为"

                await publish_realtime_message(
                    {
                        "type": "violation",
                        "title": violation_type,
                        "message": f"{camera_name} {person_name} {violation_type}",
                        "timestamp": timestamp.isoformat(),
                        "severity": "error",
                        "event_id": event.id,
                        "person_id": event_person_id,
                        "person_name": person_name,
                        "missing_ppe": alertable_violations,
                        "violation_labels": chinese_labels,
                        "snapshot_filename": None,
                        "snapshot_path": snapshot_url,
                        "snapshot_url": snapshot_url,
                        "camera_id": camera_id,
                        "camera_name": camera_name,
                    }
                )

                created_events += 1
                created_event_ids.append(event.id)

            active = self.dedup_manager.get_active_violation(tracking_key, video_source)
            if face_matched and person_id and active:
                current_event = await self.event_service.get_event(active.event_id)
                if current_event and current_event.person_id != person_id:
                    await self.event_service.update_event_person(
                        active.event_id,
                        person_id=person_id,
                        person_name=person_name,
                    )
                event_timestamp = (
                    current_event.timestamp.isoformat()
                    if current_event and current_event.timestamp is not None
                    else timestamp.isoformat()
                )
                chinese_labels = [_format_violation_label(v) for v in all_violations]
                violation_type = "、".join(chinese_labels) if chinese_labels else "危险行为"
                queued_updates.append(
                    {
                        "type": "violation_update",
                        "title": violation_type,
                        "message": f"{camera_name} {person_name} {violation_type}",
                        "timestamp": event_timestamp,
                        "severity": "error",
                        "event_id": active.event_id,
                        "person_id": person_id,
                        "person_name": person_name,
                        "missing_ppe": all_violations,
                        "violation_labels": chinese_labels,
                        "snapshot_filename": None,
                        "snapshot_path": None,
                        "snapshot_url": None,
                        "camera_id": camera_id,
                        "camera_name": camera_name,
                    }
                )

            # When a violation is "continuing" but new violation types appeared,
            # broadcast an update so the frontend shows the expanded violation set.
            if reason == "continuing":
                current_set = set(all_violations)
                if not current_set.issubset(violations_before):
                    if active:
                        current_event = await self.event_service.get_event(active.event_id)
                        event_timestamp = (
                            current_event.timestamp.isoformat()
                            if current_event and current_event.timestamp is not None
                            else timestamp.isoformat()
                        )
                        chinese_labels = [_format_violation_label(v) for v in all_violations]
                        violation_type = "、".join(chinese_labels) if chinese_labels else "危险行为"
                        queued_updates.append(
                            {
                                "type": "violation_update",
                                "title": violation_type,
                                "message": f"{camera_name} {person_name} {violation_type}",
                                "timestamp": event_timestamp,
                                "severity": "error",
                                "event_id": active.event_id,
                                "person_id": person_id,
                                "person_name": person_name,
                                "missing_ppe": all_violations,
                                "violation_labels": chinese_labels,
                                "snapshot_filename": None,
                                "snapshot_path": snapshot_url,
                                "snapshot_url": snapshot_url,
                                "camera_id": camera_id,
                                "camera_name": camera_name,
                            }
                        )

        await self.inspection_service.evaluate_area_missed_inspection(now=timestamp)
        await self.session.commit()
        for update_message in queued_updates:
            await publish_realtime_message(update_message)

        return {
            "created_events": created_events,
            "closed_events": closed_events,
            "created_event_ids": created_event_ids,
        }

    async def finalize_video_processing(self, video_source: str) -> int:
        """
        视频处理完成时，结束所有活跃的违规事件。

        关闭所有未明确结束的持续事件。
        返回关闭的事件数量。
        """
        events_to_close = self.dedup_manager.finalize_video(video_source)

        for event_id, last_frame, final_ppe in events_to_close:
            await self.event_service.close_event(
                event_id=event_id,
                end_frame=last_frame,
                end_timestamp=datetime.now(),
                final_missing_ppe=final_ppe,
            )

        await self.session.commit()
        return len(events_to_close)
