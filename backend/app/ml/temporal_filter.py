"""
Temporal Filter for Stable Violation Detection

Uses a sliding window buffer to prevent flickering alerts.
Only triggers violations that persist for multiple consecutive frames.
Supports confidence fusion (EMA, mean, max) for more robust detection.
"""

import logging
from collections import defaultdict, deque
from typing import Dict, List, Any, Set, Optional
from dataclasses import dataclass
from datetime import datetime

from ..core.danger_events import canonicalize_danger_event_key, normalize_violation_key

logger = logging.getLogger(__name__)


@dataclass
class ViolationState:
    """State of a violation for temporal filtering."""

    missing_ppe: Set[str]
    frame_count: int
    first_seen: datetime
    last_seen: datetime


class TemporalFilter:
    """
    Filters detection results to ensure temporal consistency.

    Only reports violations that persist for a minimum number of frames,
    reducing false positives from momentary detection failures.

    Supports two modes:
    1. Binary mode (update): Uses set intersection of missing PPE
    2. Confidence mode (update_with_confidence): Uses EMA/mean/max fusion
    """

    def __init__(
        self,
        buffer_size: int = 3,
        min_frames_for_violation: int = 2,
        min_frames_by_type: Optional[Dict[str, int]] = None,
        min_frames_for_clear: int = 3,
        fusion_strategy: str = "ema",
        ema_alpha: float = 0.7,
        confidence_threshold: float = 0.4,
    ):
        """
        Args:
            buffer_size: Number of frames to keep in history
            min_frames_for_violation: Minimum consecutive frames with violation to trigger
            min_frames_for_clear: Minimum consecutive frames without violation to clear (hysteresis)
            fusion_strategy: One of "ema", "mean", "max"
            ema_alpha: Weight for most recent frame in EMA (higher = more weight to recent)
            confidence_threshold: Minimum fused confidence to consider as violation
        """
        self.buffer_size = buffer_size
        self.min_frames = min_frames_for_violation
        self.min_frames_by_type = {
            normalize_violation_key(key): max(1, int(value))
            for key, value in (min_frames_by_type or {}).items()
        }
        self.min_frames_clear = min_frames_for_clear
        self.fusion_strategy = fusion_strategy
        self.ema_alpha = ema_alpha
        self.confidence_threshold = confidence_threshold

        # Track violations per person: person_id -> deque of missing_ppe sets
        self.violation_history: Dict[str, deque] = defaultdict(
            lambda: deque(maxlen=buffer_size)
        )

        # Track confidence per person: person_id -> deque of confidence dicts
        self.confidence_history: Dict[str, deque] = defaultdict(
            lambda: deque(maxlen=buffer_size)
        )

        # Track fused confidence per person: person_id -> Dict[ppe_type, confidence]
        self.fused_confidence: Dict[str, Dict[str, float]] = defaultdict(dict)

        # Track active violations
        self.active_violations: Dict[str, ViolationState] = {}
        
        # Track consecutive frames without violation for hysteresis
        self.clear_counter: Dict[str, int] = defaultdict(int)

    def _required_frames(self, violation_type: str) -> int:
        normalized = canonicalize_danger_event_key(violation_type)
        return self.min_frames_by_type.get(normalized, self.min_frames)

    @staticmethod
    def _canonical_confidence_key(key: str) -> str:
        normalized = normalize_violation_key(key)
        if not normalized:
            return ""
        if normalized.startswith("no_"):
            base_key = canonicalize_danger_event_key(normalized[3:])
            return f"no_{base_key}" if base_key else ""
        return canonicalize_danger_event_key(normalized)

    def update(self, person_id: str, missing_ppe: List[str]) -> Dict[str, Any]:
        """
        Update the filter with new detection results (binary mode).

        Args:
            person_id: Unique identifier for the person
            missing_ppe: List of missing PPE items in current frame

        Returns:
            Dict with:
                - is_violation: Whether to trigger a violation alert
                - stable_missing_ppe: PPE that's been missing consistently
                - violation_duration: How many frames the violation has persisted
        """
        missing_set = {
            canonicalize_danger_event_key(item)
            for item in missing_ppe
            if canonicalize_danger_event_key(item)
        }

        # Add to history
        self.violation_history[person_id].append(missing_set)
        history = self.violation_history[person_id]

        stable_missing: Set[str] = set()
        recent_history = list(history)
        candidate_keys = set().union(*recent_history) if recent_history else set()
        for key in candidate_keys:
            required = self._required_frames(key)
            if len(recent_history) < required:
                continue
            if all(key in frame_missing for frame_missing in recent_history[-required:]):
                stable_missing.add(key)

        now = datetime.now()

        if stable_missing:
            # Reset clear counter when violation is present
            self.clear_counter[person_id] = 0
            
            # Update or create violation state
            if person_id in self.active_violations:
                state = self.active_violations[person_id]
                state.missing_ppe = stable_missing
                state.frame_count += 1
                state.last_seen = now
            else:
                self.active_violations[person_id] = ViolationState(
                    missing_ppe=stable_missing,
                    frame_count=1,
                    first_seen=now,
                    last_seen=now,
                )

            return {
                "is_violation": True,
                "stable_missing_ppe": sorted(stable_missing),
                "violation_duration": self.active_violations[person_id].frame_count,
            }
        else:
            # Increment clear counter
            self.clear_counter[person_id] += 1
            
            # Hysteresis: only clear violation after min_frames_clear without violation
            if person_id in self.active_violations:
                if self.clear_counter[person_id] < self.min_frames_clear:
                    # Still in hysteresis period - maintain violation
                    logger.debug(
                        f"Temporal filter: hysteresis for {person_id} "
                        f"({self.clear_counter[person_id]}/{self.min_frames_clear} frames)"
                    )
                    return {
                        "is_violation": True,
                        "stable_missing_ppe": sorted(self.active_violations[person_id].missing_ppe),
                        "violation_duration": self.active_violations[person_id].frame_count,
                    }
                else:
                    # Clear violation after hysteresis period
                    logger.debug(f"Temporal filter: clearing violation for {person_id}")
                    del self.active_violations[person_id]

            return {
                "is_violation": False,
                "stable_missing_ppe": [],
                "violation_duration": 0,
            }

    def update_with_confidence(
        self, person_id: str, detection_confidence: Dict[str, float]
    ) -> Dict[str, Any]:
        """
        Update the filter with confidence scores (fusion mode).

        Args:
            person_id: Unique identifier for the person
            detection_confidence: Dict of PPE type -> detection confidence
                Example: {"no_safety_goggles": 0.85, "lab coat": 0.92}
                Keys starting with "no_" indicate missing PPE

        Returns:
            Dict with:
                - is_violation: Whether to trigger a violation alert
                - stable_missing_ppe: PPE that's been missing with high confidence
                - fused_confidence: Dict of PPE type -> fused confidence
                - violation_duration: How many frames the violation has persisted
        """
        # Add to confidence history
        normalized_confidence = {
            normalized_key: float(value)
            for key, value in detection_confidence.items()
            if (normalized_key := self._canonical_confidence_key(key))
        }
        self.confidence_history[person_id].append(normalized_confidence)
        history = self.confidence_history[person_id]

        # Compute fused confidence for each PPE type
        fused = self._compute_fused_confidence(list(history))
        self.fused_confidence[person_id] = fused

        # Extract missing PPE based on fused confidence
        # Keys starting with "no_" indicate violations
        stable_missing = []
        recent_history = list(history)
        for key, conf in fused.items():
            if not key.startswith("no_"):
                continue
            ppe_name = canonicalize_danger_event_key(key[3:])
            required = self._required_frames(ppe_name)
            if len(recent_history) < required:
                continue
            recent_values = [
                frame_conf.get(key, 0.0) for frame_conf in recent_history[-required:]
            ]
            if conf >= self.confidence_threshold and all(
                value >= self.confidence_threshold for value in recent_values
            ):
                stable_missing.append(ppe_name)

        now = datetime.now()

        if stable_missing:
            # Reset clear counter when violation is present
            self.clear_counter[person_id] = 0
            
            # Update or create violation state
            if person_id in self.active_violations:
                state = self.active_violations[person_id]
                state.missing_ppe = set(stable_missing)
                state.frame_count += 1
                state.last_seen = now
            else:
                self.active_violations[person_id] = ViolationState(
                    missing_ppe=set(stable_missing),
                    frame_count=1,
                    first_seen=now,
                    last_seen=now,
                )

            return {
                "is_violation": True,
                "stable_missing_ppe": sorted(stable_missing),
                "fused_confidence": fused,
                "violation_duration": self.active_violations[person_id].frame_count,
            }
        else:
            # Increment clear counter
            self.clear_counter[person_id] += 1
            
            # Hysteresis: only clear violation after min_frames_clear without violation
            if person_id in self.active_violations:
                if self.clear_counter[person_id] < self.min_frames_clear:
                    # Still in hysteresis period - maintain violation
                    logger.debug(
                        f"Temporal filter: confidence hysteresis for {person_id} "
                        f"({self.clear_counter[person_id]}/{self.min_frames_clear} frames)"
                    )
                    return {
                        "is_violation": True,
                        "stable_missing_ppe": sorted(self.active_violations[person_id].missing_ppe),
                        "fused_confidence": fused,
                        "violation_duration": self.active_violations[person_id].frame_count,
                    }
                else:
                    # Clear violation after hysteresis period
                    logger.debug(
                        f"Temporal filter: clearing confidence-based violation for {person_id}"
                    )
                    del self.active_violations[person_id]

            return {
                "is_violation": False,
                "stable_missing_ppe": [],
                "fused_confidence": fused,
                "violation_duration": 0,
            }

    def _compute_fused_confidence(
        self, history: List[Dict[str, float]]
    ) -> Dict[str, float]:
        """
        Compute fused confidence from history using the configured strategy.

        Args:
            history: List of confidence dicts from recent frames

        Returns:
            Dict of PPE type -> fused confidence
        """
        if not history:
            return {}

        # Collect all PPE types seen in history
        all_keys: Set[str] = set()
        for frame_conf in history:
            all_keys.update(frame_conf.keys())

        fused = {}

        for key in all_keys:
            # Get confidence values for this key across frames (0 if not present)
            values = [frame_conf.get(key, 0.0) for frame_conf in history]

            if self.fusion_strategy == "ema":
                # Exponential Moving Average (most recent has highest weight)
                fused_val = values[0]
                for val in values[1:]:
                    fused_val = self.ema_alpha * val + (1 - self.ema_alpha) * fused_val
                fused[key] = fused_val

            elif self.fusion_strategy == "max":
                # Maximum confidence across frames
                fused[key] = max(values)

            else:  # "mean" or default
                # Simple average
                fused[key] = sum(values) / len(values)

        return fused

    def get_active_violations(self) -> Dict[str, ViolationState]:
        """Get all currently active violations."""
        return dict(self.active_violations)

    def get_fused_confidence(self, person_id: str) -> Dict[str, float]:
        """Get the fused confidence for a specific person."""
        return dict(self.fused_confidence.get(person_id, {}))

    def clear_person(self, person_id: str):
        """Clear history for a specific person."""
        if person_id in self.violation_history:
            del self.violation_history[person_id]
        if person_id in self.confidence_history:
            del self.confidence_history[person_id]
        if person_id in self.fused_confidence:
            del self.fused_confidence[person_id]
        if person_id in self.active_violations:
            del self.active_violations[person_id]
        if person_id in self.clear_counter:
            del self.clear_counter[person_id]

    def clear_all(self):
        """Clear all history."""
        self.violation_history.clear()
        self.confidence_history.clear()
        self.fused_confidence.clear()
        self.active_violations.clear()
        self.clear_counter.clear()


# Singleton instance
_filter = None


def get_temporal_filter() -> TemporalFilter:
    global _filter
    if _filter is None:
        from ..core.config import settings

        min_frames_by_type = getattr(
            settings, "TEMPORAL_VIOLATION_MIN_FRAMES_BY_TYPE", {}
        ) or {}
        required_buffer_size = max(
            [int(settings.TEMPORAL_BUFFER_SIZE), int(getattr(settings, "TEMPORAL_VIOLATION_MIN_FRAMES", 2))]
            + [max(1, int(value)) for value in min_frames_by_type.values()]
        )

        _filter = TemporalFilter(
            buffer_size=required_buffer_size,
            min_frames_for_violation=getattr(
                settings, "TEMPORAL_VIOLATION_MIN_FRAMES", 2
            ),
            min_frames_by_type=min_frames_by_type,
            min_frames_for_clear=getattr(
                settings, "TEMPORAL_VIOLATION_MIN_FRAMES_CLEAR", 3
            ),
            fusion_strategy=getattr(settings, "TEMPORAL_FUSION_STRATEGY", "ema"),
            ema_alpha=getattr(settings, "TEMPORAL_EMA_ALPHA", 0.7),
            confidence_threshold=getattr(
                settings, "TEMPORAL_CONFIDENCE_THRESHOLD", 0.4
            ),
        )
    return _filter
