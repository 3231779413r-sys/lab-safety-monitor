from __future__ import annotations

from typing import Any, Dict, List, Sequence

import numpy as np

from ..core.config import settings


def _frame_area(frame_shape: Sequence[int] | np.ndarray | None) -> float:
    if frame_shape is None:
        return 0.0
    if isinstance(frame_shape, np.ndarray):
        if frame_shape.ndim < 2:
            return 0.0
        height, width = frame_shape.shape[:2]
    else:
        values = list(frame_shape)
        if len(values) < 2:
            return 0.0
        height, width = values[0], values[1]
    try:
        return max(0.0, float(height)) * max(0.0, float(width))
    except (TypeError, ValueError):
        return 0.0


def _box_area(box: Any) -> float:
    if not isinstance(box, (list, tuple)) or len(box) != 4:
        return 0.0
    try:
        x1, y1, x2, y2 = [float(value) for value in box]
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, x2 - x1) * max(0.0, y2 - y1)


def filter_persons_by_min_box_area_ratio(
    persons: List[Dict[str, Any]],
    frame_shape: Sequence[int] | np.ndarray | None,
) -> List[Dict[str, Any]]:
    min_ratio = float(getattr(settings, "PERSON_MIN_BOX_AREA_RATIO", 0.0) or 0.0)
    if min_ratio <= 0.0 or not persons:
        return list(persons)

    frame_area = _frame_area(frame_shape)
    if frame_area <= 0.0:
        return list(persons)

    min_box_area = frame_area * min_ratio
    return [
        person
        for person in persons
        if _box_area(person.get("box")) >= min_box_area
    ]
