"""
Event Deduplication Service

Prevents creating duplicate events for ongoing violations.
Only creates new events when:
- A new violation starts (person was compliant, now has violations)
- The violation type changes significantly (different PPE items or actions)
- A violation ends (person becomes compliant)

Updates existing events when violations end with end_frame and duration.

Improved logic:
- Tracks PPE and action violations separately
- Merges them into a single event per person
- Handles transitions smoothly without creating duplicates
"""

from typing import Any, Dict, List, Optional, Set, Tuple
from dataclasses import dataclass
from datetime import datetime, timedelta

import numpy as np

from ..ml.face_recognition import get_face_recognizer


@dataclass
class ActiveViolation:
    """Tracks an active ongoing violation."""

    event_id: str
    person_id: str
    missing_ppe: Set[str]  # PPE items missing
    actions: Set[str]  # Action violations (eating, drinking)
    start_frame: int
    start_timestamp: datetime
    last_frame: int
    video_source: str


@dataclass
class RecentIdentitySignature:
    identity_key: str
    embedding: np.ndarray
    last_seen_at: datetime


class DeduplicationManager:
    """
    Manages event deduplication to prevent flooding the database
    with duplicate events for the same ongoing violation.
    """

    def __init__(self):
        # Track active violations: (person_id, video_source) -> ActiveViolation
        self.active_violations: Dict[Tuple[str, str], ActiveViolation] = {}
        # Track recent alert cooldowns globally: (identity_key, violation_type) -> timestamp
        self.recent_alerts: Dict[Tuple[str, str], datetime] = {}
        self.identity_aliases: Dict[str, str] = {}
        self.recent_identity_signatures: List[RecentIdentitySignature] = []

    def should_create_event(
        self,
        person_id: str,
        video_source: str,
        missing_ppe: List[str],
        frame_number: int,
    ) -> Tuple[bool, Optional[str], Optional[str], Optional[Dict[str, List[str]]]]:
        """
        Determine if a new event should be created.

        Args:
            person_id: Unique person identifier
            video_source: Video file or camera source
            missing_ppe: Combined list of violations (PPE + actions with 'action:' prefix)
            frame_number: Current frame number

        Returns:
            (should_create, ended_event_id, reason, final_violations)
            where final_violations is {"ppe": [...], "actions": [...]}
        """
        key = (person_id, video_source)

        # Separate PPE violations from action violations
        current_ppe = set()
        current_actions = set()

        for item in missing_ppe:
            if item.startswith("action:"):
                current_actions.add(item.replace("action:", ""))
            else:
                current_ppe.add(item)

        active = self.active_violations.get(key)
        has_current_violation = bool(current_ppe or current_actions)

        # Case 1: No active violation and no current violation
        if not active and not has_current_violation:
            return False, None, None, None

        # Case 2: No active violation but new violation detected
        if not active and has_current_violation:
            return True, None, "new", None

        # Case 3: Active violation but person is now compliant
        if active and not has_current_violation:
            ended_event_id = active.event_id
            final_violations = {
                "ppe": list(active.missing_ppe),
                "actions": list(active.actions)
            }
            del self.active_violations[key]
            return False, ended_event_id, "ended", final_violations

        # Case 4: Active violation and still has violations
        if active and has_current_violation:
            # Check if violations have changed significantly
            ppe_changed = not self._is_similar_violation(current_ppe, active.missing_ppe)
            actions_changed = current_actions != active.actions

            # Use more lenient logic: only create new event if BOTH types change completely
            # or if one was empty and now has violations (significant change)
            significant_change = False

            # Significant change scenarios:
            # 1. Had no PPE violations, now has PPE violations (AND it's not a subset)
            # 2. Had PPE violations, now has completely different PPE violations
            # 3. Action violations changed (these are instant, so any change is significant)

            if ppe_changed and current_ppe and active.missing_ppe:
                # Both had PPE, but they're completely different
                significant_change = True

            if significant_change:
                # End old event, create new one
                ended_event_id = active.event_id
                final_violations = {
                    "ppe": list(active.missing_ppe),
                    "actions": list(active.actions)
                }
                del self.active_violations[key]
                return True, ended_event_id, "changed", final_violations
            else:
                # Continue same event, update the violations
                # Use union to track all violations that occurred
                active.missing_ppe = active.missing_ppe.union(current_ppe)
                active.actions = current_actions  # Actions are instant, use current
                active.last_frame = frame_number
                return False, None, "continuing", None

        return False, None, None, None

    def _is_similar_violation(self, set1: Set[str], set2: Set[str]) -> bool:
        """
        Check if two PPE violation sets are similar enough to be the same event.

        Similar if:
        - They're equal
        - One is a subset of the other (handles occlusions)
        - They overlap significantly (at least 50%)
        """
        if not set1 and not set2:
            return True
        if not set1 or not set2:
            return False
        if set1 == set2:
            return True
        if set1.issubset(set2) or set1.issuperset(set2):
            return True

        # Check overlap percentage
        overlap = len(set1.intersection(set2))
        smaller = min(len(set1), len(set2))
        overlap_ratio = overlap / smaller if smaller > 0 else 0

        return overlap_ratio >= 0.5

    def register_event(
        self,
        event_id: str,
        person_id: str,
        video_source: str,
        missing_ppe: List[str],
        frame_number: int,
        timestamp: datetime,
    ) -> None:
        """Register a newly created event as an active violation."""
        key = (person_id, video_source)

        # Separate PPE from actions
        ppe_items = set()
        actions = set()

        for item in missing_ppe:
            if item.startswith("action:"):
                actions.add(item.replace("action:", ""))
            else:
                ppe_items.add(item)

        self.active_violations[key] = ActiveViolation(
            event_id=event_id,
            person_id=person_id,
            missing_ppe=ppe_items,
            actions=actions,
            start_frame=frame_number,
            start_timestamp=timestamp,
            last_frame=frame_number,
            video_source=video_source,
        )

    def get_alertable_violations(
        self,
        identity_key: str,
        violations: List[str],
        timestamp: datetime,
        cooldown_seconds: int,
    ) -> List[str]:
        """Filter out violations that are still inside the global cooldown window."""
        if not identity_key or cooldown_seconds <= 0:
            return list(violations)
        identity_key = self._resolve_alias(identity_key)
        self._prune_recent_alerts(timestamp, cooldown_seconds)
        alertable: List[str] = []
        for violation in violations:
            key = (identity_key, violation)
            last_alert_at = self.recent_alerts.get(key)
            if last_alert_at is None:
                alertable.append(violation)
                continue
            if (timestamp - last_alert_at).total_seconds() >= cooldown_seconds:
                alertable.append(violation)
        return alertable

    def mark_alert_created(
        self,
        identity_key: str,
        violations: List[str],
        timestamp: datetime,
    ) -> None:
        """Record alert emission time for each violation type in the cooldown buffer."""
        if not identity_key:
            return
        identity_key = self._resolve_alias(identity_key)
        for violation in violations:
            self.recent_alerts[(identity_key, violation)] = timestamp

    def resolve_identity_key(
        self,
        *,
        base_identity_key: str,
        embedding: Optional[Any],
        timestamp: datetime,
        cooldown_seconds: int,
        matched_person_id: Optional[str] = None,
    ) -> str:
        canonical_key = (
            f"person:{matched_person_id}" if matched_person_id else self._resolve_alias(base_identity_key)
        )
        self._prune_identity_signatures(timestamp, cooldown_seconds)

        embedding_array = self._normalize_embedding(embedding)
        if embedding_array is None:
            return canonical_key

        similar_key: Optional[str] = None
        best_score = 0.0
        recognizer = get_face_recognizer()
        for signature in self.recent_identity_signatures:
            similarity = recognizer.compare_embeddings(embedding_array, signature.embedding)
            if similarity >= recognizer.threshold and similarity > best_score:
                best_score = similarity
                similar_key = self._resolve_alias(signature.identity_key)

        if matched_person_id and similar_key and similar_key != canonical_key:
            self._alias_identity(similar_key, canonical_key)
        elif not matched_person_id and similar_key:
            canonical_key = similar_key

        self._store_identity_signature(canonical_key, embedding_array, timestamp)
        return canonical_key

    def get_active_violation(
        self, person_id: str, video_source: str
    ) -> Optional[ActiveViolation]:
        """Get the active violation for a person if any."""
        return self.active_violations.get((person_id, video_source))

    def get_violation_duration(
        self, person_id: str, video_source: str, current_frame: int
    ) -> int:
        """Get the duration in frames for an active violation."""
        active = self.get_active_violation(person_id, video_source)
        if active:
            return current_frame - active.start_frame + 1
        return 0

    def finalize_video(self, video_source: str) -> List[Tuple[str, int, Dict[str, List[str]]]]:
        """
        Finalize all active violations for a video (when processing ends).

        Returns list of (event_id, last_frame, final_violations) for events that need to be closed.
        """
        to_close = []
        keys_to_remove = []

        for key, violation in self.active_violations.items():
            if violation.video_source == video_source:
                final_violations = {
                    "ppe": list(violation.missing_ppe),
                    "actions": list(violation.actions)
                }
                to_close.append(
                    (
                        violation.event_id,
                        violation.last_frame,
                        final_violations,
                    )
                )
                keys_to_remove.append(key)

        for key in keys_to_remove:
            del self.active_violations[key]

        return to_close

    def clear(self) -> None:
        """Clear all tracked violations."""
        self.active_violations.clear()
        self.recent_alerts.clear()
        self.identity_aliases.clear()
        self.recent_identity_signatures.clear()

    def get_stats(self) -> Dict:
        """Get statistics about active violations."""
        return {
            "active_violations": len(self.active_violations),
            "recent_alerts": len(self.recent_alerts),
            "by_video": {
                video: len(
                    [
                        v
                        for v in self.active_violations.values()
                        if v.video_source == video
                    ]
                )
                for video in set(
                    v.video_source for v in self.active_violations.values()
                )
            },
        }

    def _prune_recent_alerts(self, timestamp: datetime, cooldown_seconds: int) -> None:
        cutoff = timestamp - timedelta(seconds=max(1, cooldown_seconds))
        expired_keys = [
            key for key, last_alert_at in self.recent_alerts.items() if last_alert_at < cutoff
        ]
        for key in expired_keys:
            del self.recent_alerts[key]

    def _resolve_alias(self, identity_key: str) -> str:
        current = identity_key
        while current in self.identity_aliases:
            next_key = self.identity_aliases[current]
            if next_key == current:
                break
            current = next_key
        return current

    def _alias_identity(self, from_key: str, to_key: str) -> None:
        source = self._resolve_alias(from_key)
        target = self._resolve_alias(to_key)
        if source == target:
            return
        self.identity_aliases[source] = target

        moved_alerts: Dict[Tuple[str, str], datetime] = {}
        for (identity_key, violation), last_alert_at in self.recent_alerts.items():
            resolved_key = self._resolve_alias(identity_key)
            target_key = target if resolved_key == source else resolved_key
            moved_key = (target_key, violation)
            previous = moved_alerts.get(moved_key)
            if previous is None or last_alert_at > previous:
                moved_alerts[moved_key] = last_alert_at
        self.recent_alerts = moved_alerts

        for signature in self.recent_identity_signatures:
            if self._resolve_alias(signature.identity_key) == source:
                signature.identity_key = target

    def _prune_identity_signatures(self, timestamp: datetime, cooldown_seconds: int) -> None:
        cutoff = timestamp - timedelta(seconds=max(1, cooldown_seconds))
        self.recent_identity_signatures = [
            signature
            for signature in self.recent_identity_signatures
            if signature.last_seen_at >= cutoff
        ]

    def _store_identity_signature(
        self,
        identity_key: str,
        embedding: np.ndarray,
        timestamp: datetime,
    ) -> None:
        resolved_key = self._resolve_alias(identity_key)
        for signature in self.recent_identity_signatures:
            if self._resolve_alias(signature.identity_key) == resolved_key:
                signature.embedding = embedding
                signature.last_seen_at = timestamp
                signature.identity_key = resolved_key
                return
        self.recent_identity_signatures.append(
            RecentIdentitySignature(
                identity_key=resolved_key,
                embedding=embedding,
                last_seen_at=timestamp,
            )
        )

    @staticmethod
    def _normalize_embedding(embedding: Optional[Any]) -> Optional[np.ndarray]:
        if embedding is None:
            return None
        if isinstance(embedding, np.ndarray):
            return embedding
        try:
            return np.asarray(embedding, dtype=np.float32)
        except Exception:
            return None


# Singleton instance
_deduplication_manager: Optional[DeduplicationManager] = None


def get_deduplication_manager() -> DeduplicationManager:
    """Get the singleton deduplication manager instance."""
    global _deduplication_manager
    if _deduplication_manager is None:
        _deduplication_manager = DeduplicationManager()
    return _deduplication_manager
