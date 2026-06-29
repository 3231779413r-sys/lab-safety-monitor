"""Temporal smoothing for pose-derived action labels."""

from collections import defaultdict
from typing import Any, Dict, List, Set

from ..core.config import settings


class PoseActionFilter:
    """Keep pose action labels stable across a few consecutive frames."""

    def __init__(self):
        self.min_frames = max(1, getattr(settings, "POSE_ACTION_MIN_FRAMES", 2))
        self.clear_frames = max(1, getattr(settings, "POSE_ACTION_CLEAR_FRAMES", 2))
        self.hit_counts: Dict[str, Dict[str, int]] = defaultdict(
            lambda: defaultdict(int)
        )
        self.miss_counts: Dict[str, Dict[str, int]] = defaultdict(
            lambda: defaultdict(int)
        )
        self.active_actions: Dict[str, Set[str]] = defaultdict(set)
        self.latest_payloads: Dict[str, Dict[str, Dict[str, Any]]] = defaultdict(dict)

    def update(self, person_id: str, actions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        current_names = {
            action.get("action") for action in actions if action.get("action")
        }
        payload_by_name = {
            action["action"]: action for action in actions if action.get("action")
        }

        known_names = (
            set(self.hit_counts[person_id].keys())
            | set(self.active_actions[person_id])
            | current_names
        )

        for name in known_names:
            if name in current_names:
                self.hit_counts[person_id][name] += 1
                self.miss_counts[person_id][name] = 0
                self.latest_payloads[person_id][name] = payload_by_name[name]
                if self.hit_counts[person_id][name] >= self.min_frames:
                    self.active_actions[person_id].add(name)
            else:
                self.hit_counts[person_id][name] = 0
                self.miss_counts[person_id][name] += 1
                if self.miss_counts[person_id][name] >= self.clear_frames:
                    self.active_actions[person_id].discard(name)
                    self.latest_payloads[person_id].pop(name, None)

        return [
            self.latest_payloads[person_id][name]
            for name in sorted(self.active_actions[person_id])
            if name in self.latest_payloads[person_id]
        ]

    def clear_all(self) -> None:
        self.hit_counts.clear()
        self.miss_counts.clear()
        self.active_actions.clear()
        self.latest_payloads.clear()


_pose_action_filter: PoseActionFilter | None = None


def get_pose_action_filter() -> PoseActionFilter:
    """Get the singleton pose action temporal filter."""
    global _pose_action_filter
    if _pose_action_filter is None:
        _pose_action_filter = PoseActionFilter()
    return _pose_action_filter
