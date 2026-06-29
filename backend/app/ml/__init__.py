from importlib import import_module
from typing import Any

__all__ = [
    "SAM3Detector",
    "get_detector",
    "FaceRecognizer",
    "get_face_recognizer",
    "TemporalFilter",
    "get_temporal_filter",
    "DetectionPipeline",
    "get_pipeline",
]


_EXPORT_MAP = {
    "SAM3Detector": (".sam3_detector", "SAM3Detector"),
    "get_detector": (".sam3_detector", "get_detector"),
    "FaceRecognizer": (".face_recognition", "FaceRecognizer"),
    "get_face_recognizer": (".face_recognition", "get_face_recognizer"),
    "TemporalFilter": (".temporal_filter", "TemporalFilter"),
    "get_temporal_filter": (".temporal_filter", "get_temporal_filter"),
    "DetectionPipeline": (".pipeline", "DetectionPipeline"),
    "get_pipeline": (".pipeline", "get_pipeline"),
}


def __getattr__(name: str) -> Any:
    module_info = _EXPORT_MAP.get(name)
    if module_info is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attr_name = module_info
    module = import_module(module_name, __name__)
    value = getattr(module, attr_name)
    globals()[name] = value
    return value
