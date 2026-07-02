from typing import Iterable


PPE_DANGER_EVENT_TYPES = (
    "hardhat",
    "mask",
    "safety_vest",
    "work_clothes",
    "safety_shoes",
    "gloves",
    "goggles",
    "respirator",
)

ACTION_DANGER_EVENT_TYPES = (
    "missed_inspection",
    "area_missed_inspection",
    "unauthorized_intrusion",
    "overtime_stay",
    "blind_spot_stay",
    "area_overcapacity",
    "workshop_overcapacity",
    "fall_detected",
)

DANGER_EVENT_TYPES = PPE_DANGER_EVENT_TYPES + ACTION_DANGER_EVENT_TYPES

PERSONNEL_SELECTABLE_EVENT_TYPES = PPE_DANGER_EVENT_TYPES + (
    "unauthorized_intrusion",
    "overtime_stay",
    "blind_spot_stay",
    "fall_detected",
)

DANGER_EVENT_LABELS = {
    "hardhat": "未佩戴安全帽",
    "mask": "未佩戴口罩",
    "safety_vest": "未穿戴安全背心",
    "work_clothes": "未穿工作服",
    "safety_shoes": "未穿戴防护鞋",
    "gloves": "未佩戴防护手套",
    "goggles": "未佩戴护目镜",
    "respirator": "未佩戴防毒口罩",
    "missed_inspection": "未巡检",
    "area_missed_inspection": "区域漏巡",
    "unauthorized_intrusion": "违规闯入",
    "overtime_stay": "超时驻留",
    "blind_spot_stay": "盲区驻留",
    "area_overcapacity": "区域超员",
    "workshop_overcapacity": "车间超员",
    "fall_detected": "人员跌倒",
}

_DANGER_EVENT_MATCHERS = {
    "hardhat": "hardhat",
    "hard_hat": "hardhat",
    "no_hardhat": "hardhat",
    "no_hard_hat": "hardhat",
    "mask": "mask",
    "no_mask": "mask",
    "safety_vest": "safety_vest",
    "vest": "safety_vest",
    "no_safety_vest": "safety_vest",
    "no_vest": "safety_vest",
    "work_clothes": "work_clothes",
    "no_work_clothes": "work_clothes",
    "safety_shoes": "safety_shoes",
    "protective_shoes": "safety_shoes",
    "no_safety_shoes": "safety_shoes",
    "no_protective_shoes": "safety_shoes",
    "gloves": "gloves",
    "no_gloves": "gloves",
    "goggles": "goggles",
    "no_goggles": "goggles",
    "respirator": "respirator",
    "gas_mask": "respirator",
    "anti_toxic_mask": "respirator",
    "no_respirator": "respirator",
    "no_gas_mask": "respirator",
    "missed_inspection": "missed_inspection",
    "area_missed_inspection": "area_missed_inspection",
    "unauthorized_intrusion": "unauthorized_intrusion",
    "overtime_stay": "overtime_stay",
    "blind_spot_stay": "blind_spot_stay",
    "area_overcapacity": "area_overcapacity",
    "workshop_overcapacity": "workshop_overcapacity",
    "fall_detected": "fall_detected",
}


def normalize_violation_key(value: str) -> str:
    return (value or "").strip().lower().replace("-", "_").replace(" ", "_")


def canonicalize_danger_event_key(value: str) -> str:
    normalized = normalize_violation_key(value)
    if not normalized:
        return ""
    if normalized.startswith("action:"):
        action_value = canonicalize_danger_event_key(normalized.split(":", 1)[1])
        return f"action:{action_value}" if action_value else ""
    return _DANGER_EVENT_MATCHERS.get(normalized, normalized)


def canonicalize_danger_event_values(*value_groups: Iterable[str] | None) -> list[str]:
    canonicalized: list[str] = []
    seen: set[str] = set()

    for values in value_groups:
        for value in values or []:
            canonical = canonicalize_danger_event_key(value)
            if canonical and canonical not in seen:
                seen.add(canonical)
                canonicalized.append(canonical)

    return canonicalized


def match_danger_event_types(*value_groups: Iterable[str] | None) -> list[str]:
    return canonicalize_danger_event_values(*value_groups)


def expand_danger_event_filter_values(event_type: str) -> list[str]:
    normalized = canonicalize_danger_event_key(event_type)
    if not normalized:
        return []

    expanded: list[str] = []
    for raw_value, matched_type in _DANGER_EVENT_MATCHERS.items():
        if matched_type == normalized and raw_value not in expanded:
            expanded.append(raw_value)

    if normalized not in expanded:
        expanded.insert(0, normalized)

    return expanded


def get_danger_event_label(event_type: str) -> str:
    normalized = canonicalize_danger_event_key(event_type)
    return DANGER_EVENT_LABELS.get(normalized, normalized)
