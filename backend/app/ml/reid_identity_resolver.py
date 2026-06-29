from __future__ import annotations

import logging
import time
from typing import Any, Callable, Optional

from ..core.config import settings

logger = logging.getLogger(__name__)


class ReIDIdentityResolver:
    """Encapsulates gallery-based ReID identity resolution."""

    def __init__(
        self,
        *,
        reid_enabled: bool,
        reid_service: Any,
        reid_tracker: Any,
        face_identity_cache: dict[str, dict[str, Any]],
        last_face_tracking_key_by_person: dict[str, str],
        stable_track_id_resolver: Callable[[Optional[str], str], int],
    ) -> None:
        self.reid_enabled = reid_enabled
        self.reid_service = reid_service
        self.reid_tracker = reid_tracker
        self.face_identity_cache = face_identity_cache
        self.last_face_tracking_key_by_person = last_face_tracking_key_by_person
        self.stable_track_id_resolver = stable_track_id_resolver

    def resolve_identities(
        self,
        persons: list[dict[str, Any]],
        *,
        frame_count: int,
        camera_id: str | None,
    ) -> None:
        if not persons:
            return

        assigned_person_ids: dict[str, dict[str, Any]] = {}
        ordered_persons = sorted(
            persons,
            key=lambda item: (
                1 if bool(item.get("face_confirmed_this_frame")) else 0,
                1 if bool(item.get("face_matched")) else 0,
            ),
            reverse=True,
        )

        for person in ordered_persons:
            raw_tracking_key = (
                person.get("raw_tracking_key")
                or person.get("tracking_key")
                or self.build_tracking_key(person)
            )
            person["raw_tracking_key"] = raw_tracking_key
            track_id = person.get("track_id")
            track = None
            if self.reid_enabled and track_id is not None:
                try:
                    track = self.reid_tracker.get_track_by_id(int(track_id))
                except (TypeError, ValueError):
                    track = None

            current_person_id = person.get("person_id")
            face_matched = bool(person.get("face_matched"))
            face_observed = person.get("face_embedding") is not None
            appearance_feature = person.get("appearance_feature")

            if face_matched and current_person_id:
                current_person_id_str = str(current_person_id)
                existing_assignment = assigned_person_ids.get(current_person_id_str)
                existing_tracking_key = (
                    str(existing_assignment.get("tracking_key"))
                    if existing_assignment is not None
                    else None
                )
                if existing_tracking_key and existing_tracking_key != raw_tracking_key:
                    logger.warning(
                        "[FaceRecognition] Frame %s duplicate face identity in same frame: person_id=%s current=%s existing=%s; demoting current track to non-face identity resolution",
                        frame_count,
                        current_person_id_str,
                        raw_tracking_key,
                        existing_tracking_key,
                    )
                    face_matched = False
                    person["face_matched"] = False
                    current_person_id = None
                    person["person_id"] = None
                    person["person_name"] = "未知人员"
                    person["identity_source"] = "unknown"
                    person["subject_type"] = "unknown"
                    person["subject_supervision_scope"] = []
                    person["allowed_camera_ids"] = []
                    person["appointment_start"] = None
                    person["appointment_end"] = None
                    person["external_person_id"] = None
                else:
                    assigned_person_ids[current_person_id_str] = {
                        "tracking_key": raw_tracking_key,
                    }

            if face_matched and current_person_id:
                previous_person_id = track.person_id if track is not None else None
                if (
                    self.reid_service is not None
                    and previous_person_id
                    and previous_person_id != current_person_id
                ):
                    self.reid_service.rename_identity(
                        previous_person_id,
                        str(current_person_id),
                        target_name=person.get("person_name"),
                        target_identity_data=self.identity_metadata_from_person(person),
                    )
                self._upsert_gallery_identity(person, camera_id=camera_id)
                if track is not None:
                    self.reid_tracker.link_person_id(track.track_id, str(current_person_id))
                current_person_id_str = str(current_person_id)
                previous_tracking_key = self.last_face_tracking_key_by_person.get(current_person_id_str)
                if previous_tracking_key and previous_tracking_key != raw_tracking_key:
                    self.face_identity_cache.pop(previous_tracking_key, None)
                self._clear_face_cache_for_person(
                    current_person_id_str,
                    except_tracking_key=raw_tracking_key,
                )
                self.last_face_tracking_key_by_person[current_person_id_str] = raw_tracking_key
                person["identity_source"] = "face"
                person["stable_track_id"] = self.stable_track_id_resolver(
                    str(current_person_id),
                    raw_tracking_key,
                )
                person["tracking_key"] = f"stable:{person['stable_track_id']}"
                self.face_identity_cache[raw_tracking_key] = self._build_identity_cache_entry(
                    person,
                    raw_tracking_key,
                    face_matched=True,
                    frame_count=frame_count,
                )
                continue

            resolved_person_id: Optional[str] = None
            match_type = "unknown"
            match_score = 0.0
            if appearance_feature is not None and self.reid_service is not None:
                gallery_match, match_score = self.reid_service.search(
                    appearance_feature,
                    exclude_person_ids=set(assigned_person_ids.keys()),
                )
                if gallery_match:
                    resolved_person_id = str(gallery_match)
                    match_type = "global"
                    if track is not None:
                        self.reid_tracker.link_person_id(track.track_id, resolved_person_id)
            if (
                resolved_person_id is None
                and not face_observed
                and track is not None
                and track.person_id
            ):
                track_person_id = str(track.person_id)
                existing_assignment = assigned_person_ids.get(track_person_id)
                existing_tracking_key = (
                    str(existing_assignment.get("tracking_key"))
                    if existing_assignment is not None
                    else None
                )
                if (
                    not self.is_unknown_identity(track_person_id)
                    and (existing_tracking_key is None or existing_tracking_key == raw_tracking_key)
                    and self.reid_service is not None
                    and self.reid_service.get_identity(track_person_id)
                ):
                    resolved_person_id = track_person_id
                    match_type = "track"

            if resolved_person_id:
                existing_assignment = assigned_person_ids.get(resolved_person_id)
                existing_tracking_key = (
                    str(existing_assignment.get("tracking_key"))
                    if existing_assignment is not None
                    else None
                )
                if existing_tracking_key and existing_tracking_key != raw_tracking_key:
                    logger.warning(
                        "[FaceRecognition] Frame %s duplicate reid identity in same frame: person_id=%s current=%s existing=%s; keeping current track unknown",
                        frame_count,
                        resolved_person_id,
                        raw_tracking_key,
                        existing_tracking_key,
                    )
                    resolved_person_id = None

            if resolved_person_id:
                self._restore_identity_from_gallery(
                    person,
                    resolved_person_id,
                    match_type,
                    score=match_score if match_type == "global" else None,
                )
                assigned_person_ids[resolved_person_id] = {
                    "tracking_key": raw_tracking_key,
                }
            else:
                stable_unknown_id = str(current_person_id) if current_person_id else (
                    f"{getattr(settings, 'REID_UNKNOWN_ID_PREFIX', 'reid_unknown')}:{track_id if track_id is not None else person.get('id', 0)}"
                )
                person["person_id"] = stable_unknown_id
                person["person_name"] = person.get("person_name") or "未知人员"
                person["face_matched"] = False
                person["identity_source"] = "unknown"
                person.setdefault("subject_type", "unknown")
                person.setdefault("subject_supervision_scope", [])
                person.setdefault("allowed_camera_ids", [])
                person.setdefault("appointment_start", None)
                person.setdefault("appointment_end", None)
                person.setdefault("external_person_id", None)
                resolved_person_id = stable_unknown_id
                if track is not None:
                    self.reid_tracker.link_person_id(track.track_id, resolved_person_id)

            person["stable_track_id"] = self.stable_track_id_resolver(
                person.get("person_id"),
                raw_tracking_key,
            )
            person["tracking_key"] = f"stable:{person['stable_track_id']}"
            self._upsert_gallery_identity(person, camera_id=camera_id)

    def _upsert_gallery_identity(self, person: dict[str, Any], *, camera_id: str | None) -> None:
        if self.reid_service is None:
            return
        person_id = person.get("person_id")
        if not person_id:
            return
        self.reid_service.upsert_identity(
            person_id=str(person_id),
            feature=person.get("appearance_feature"),
            person_name=person.get("person_name"),
            identity_data=self.identity_metadata_from_person(person),
            camera_id=camera_id,
            face_verified=bool(person.get("face_matched")),
            index_identity=bool(person.get("face_matched")) and self.should_index_identity(person),
        )

    def _restore_identity_from_gallery(
        self,
        person: dict[str, Any],
        person_id: str,
        match_type: str,
        score: float | None = None,
    ) -> None:
        if self.reid_service is None:
            return
        record = self.reid_service.get_identity(person_id)
        metadata = dict(record.identity_data) if record and record.identity_data else {}
        person["person_id"] = person_id
        person["person_name"] = (
            record.person_name if record and record.person_name else person.get("person_name")
        ) or "未知人员"
        person["face_matched"] = False
        person["identity_source"] = f"reid_{match_type}"
        if score is not None:
            person["reid_match_score"] = score
        self.apply_identity_metadata(person, metadata)

    def _build_identity_cache_entry(
        self,
        person: dict[str, Any],
        tracking_key: str,
        *,
        face_matched: bool,
        frame_count: int,
    ) -> dict[str, Any]:
        entry = {
            "person_id": person.get("person_id"),
            "person_name": person.get("person_name") or "未知人员",
            "face_matched": face_matched,
            "face_embedding": person.get("face_embedding"),
            "thumbnail": person.get("thumbnail"),
            "tracking_key": tracking_key,
            "identity_source": person.get("identity_source", "unknown"),
            "face_observed_this_frame": bool(person.get("face_observed_this_frame")),
            "face_confirmed_this_frame": bool(person.get("face_confirmed_this_frame")),
            "last_attempt_frame": frame_count,
            "last_seen_at": time.monotonic(),
        }
        entry.update(self.identity_metadata_from_person(person))
        return entry

    def _clear_face_cache_for_person(
        self,
        person_id: Optional[str],
        *,
        except_tracking_key: Optional[str] = None,
    ) -> None:
        if not person_id or self.is_unknown_identity(person_id):
            return
        normalized_person_id = str(person_id)
        stale_keys = [
            tracking_key
            for tracking_key, identity in self.face_identity_cache.items()
            if tracking_key != except_tracking_key
            and str(identity.get("person_id") or "") == normalized_person_id
        ]
        for tracking_key in stale_keys:
            self.face_identity_cache.pop(tracking_key, None)

    @staticmethod
    def build_tracking_key(person: dict[str, Any]) -> str:
        track_id = person.get("track_id")
        fallback_id = person.get("id", 0)
        return f"track:{track_id if track_id is not None else fallback_id}"

    @staticmethod
    def identity_metadata_from_person(person: dict[str, Any]) -> dict[str, Any]:
        return {
            "subject_type": person.get("subject_type", "unknown"),
            "subject_supervision_scope": list(person.get("subject_supervision_scope") or []),
            "allowed_camera_ids": list(person.get("allowed_camera_ids") or []),
            "appointment_start": person.get("appointment_start"),
            "appointment_end": person.get("appointment_end"),
            "external_person_id": person.get("external_person_id"),
        }

    @staticmethod
    def should_index_identity(person: dict[str, Any]) -> bool:
        subject_type = str(person.get("subject_type") or "unknown")
        if subject_type == "unknown":
            return False
        person_id = str(person.get("person_id") or "")
        if not person_id:
            return False
        prefix = str(getattr(settings, "REID_UNKNOWN_ID_PREFIX", "reid_unknown"))
        return not person_id.startswith("unknown:") and not person_id.startswith(f"{prefix}:")

    @staticmethod
    def is_unknown_identity(person_id: Optional[str]) -> bool:
        identity = str(person_id or "")
        if not identity:
            return True
        prefix = str(getattr(settings, "REID_UNKNOWN_ID_PREFIX", "reid_unknown"))
        return identity.startswith("unknown:") or identity.startswith(f"{prefix}:")

    @staticmethod
    def apply_identity_metadata(
        person: dict[str, Any],
        metadata: Optional[dict[str, Any]],
    ) -> None:
        metadata = metadata or {}
        person["subject_type"] = metadata.get("subject_type", "unknown")
        person["subject_supervision_scope"] = list(metadata.get("subject_supervision_scope") or [])
        person["allowed_camera_ids"] = list(metadata.get("allowed_camera_ids") or [])
        person["appointment_start"] = metadata.get("appointment_start")
        person["appointment_end"] = metadata.get("appointment_end")
        person["external_person_id"] = metadata.get("external_person_id")
