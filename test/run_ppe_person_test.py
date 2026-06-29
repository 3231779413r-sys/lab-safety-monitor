#!/usr/bin/env python3
"""Batch test images with a YOLO model and save annotated outputs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List

try:
    import cv2
except ModuleNotFoundError as exc:
    raise SystemExit(
        "Missing Python dependency 'cv2'. "
        "Run `bash test/run_ppe_person_test_docker.sh` "
        "or use a Python environment with backend dependencies installed."
    ) from exc

try:
    from ultralytics import YOLO
except ModuleNotFoundError as exc:
    raise SystemExit(
        "Missing Python dependency 'ultralytics'. "
        "Run `bash test/run_ppe_person_test_docker.sh` "
        "or use a Python environment with backend dependencies installed."
    ) from exc


PROJECT_ROOT = Path(__file__).resolve().parents[1]
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
DEFAULT_EXPECTED_CLASSES = [
    "Gloves",
    "Hard_hat",
    "Mask",
    "Person",
    "Safety_boots",
    "Vest",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a YOLO model on images from a folder")
    parser.add_argument(
        "--model-path",
        type=Path,
        default=PROJECT_ROOT / "test" / "yolo11m-cls.pt",
        help="Path to the YOLO model",
    )
    parser.add_argument(
        "--input-dir",
        type=Path,
        default=PROJECT_ROOT / "test" / "images",
        help="Folder with input images",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=PROJECT_ROOT / "test" / "output",
        help="Folder for annotated images and summary",
    )
    parser.add_argument(
        "--recursive",
        action="store_true",
        help="Scan input images recursively",
    )
    parser.add_argument(
        "--conf",
        type=float,
        default=0.25,
        help="Confidence threshold for detection-style tasks",
    )
    parser.add_argument(
        "--topk",
        type=int,
        default=5,
        help="How many classification results to draw and save",
    )
    parser.add_argument(
        "--expected-classes",
        nargs="*",
        default=DEFAULT_EXPECTED_CLASSES,
        help="Expected class names for quick compatibility checking",
    )
    return parser.parse_args()


def iter_images(folder: Path, recursive: bool) -> List[Path]:
    if not folder.exists():
        return []

    iterator: Iterable[Path] = folder.rglob("*") if recursive else folder.iterdir()
    images = [p for p in iterator if p.is_file() and p.suffix.lower() in IMAGE_EXTS]
    return sorted(images)


def normalize_label(value: str) -> str:
    return value.strip().lower().replace("-", "_").replace(" ", "_")


def collect_model_info(model: YOLO, expected_classes: List[str]) -> Dict[str, Any]:
    names = getattr(model, "names", {}) or {}
    task = getattr(model, "task", None) or model.overrides.get("task")
    model_class_names = [str(names[k]) for k in sorted(names)]

    model_normalized = {normalize_label(name) for name in model_class_names}
    expected_normalized = {normalize_label(name) for name in expected_classes}

    matched = [name for name in expected_classes if normalize_label(name) in model_normalized]
    missing = [name for name in expected_classes if normalize_label(name) not in model_normalized]

    return {
        "task": task,
        "class_count": len(model_class_names),
        "class_names": model_class_names,
        "expected_classes": expected_classes,
        "matched_expected_classes": matched,
        "missing_expected_classes": missing,
        "looks_compatible_with_expected_classes": len(missing) == 0,
    }


def draw_text_lines(frame, lines: List[str]) -> Any:
    annotated = frame.copy()
    if not lines:
        return annotated

    font = cv2.FONT_HERSHEY_SIMPLEX
    font_scale = 0.7
    thickness = 2
    line_height = 28
    padding = 12
    max_width = 0

    for line in lines:
        (width, _), _ = cv2.getTextSize(line, font, font_scale, thickness)
        max_width = max(max_width, width)

    box_width = max_width + padding * 2
    box_height = line_height * len(lines) + padding * 2

    cv2.rectangle(annotated, (10, 10), (10 + box_width, 10 + box_height), (0, 0, 0), -1)
    cv2.rectangle(annotated, (10, 10), (10 + box_width, 10 + box_height), (0, 200, 255), 2)

    y = 10 + padding + 18
    for line in lines:
        cv2.putText(annotated, line, (20, y), font, font_scale, (255, 255, 255), thickness, cv2.LINE_AA)
        y += line_height

    return annotated


def classify_image(model: YOLO, image_path: Path, topk: int) -> Dict[str, Any]:
    results = model.predict(source=str(image_path), verbose=False)
    result = results[0]
    frame = cv2.imread(str(image_path))
    if frame is None:
        raise RuntimeError(f"Failed to read image: {image_path}")

    probs = result.probs
    if probs is None or probs.data is None:
        raise RuntimeError(f"No classification probabilities returned for image: {image_path}")

    scores = probs.data.cpu().numpy()
    top_indices = scores.argsort()[::-1][:topk].tolist()
    names = getattr(model, "names", {}) or {}

    top_predictions = []
    lines = []
    for rank, class_id in enumerate(top_indices, start=1):
        class_name = str(names.get(class_id, class_id))
        confidence = float(scores[class_id])
        top_predictions.append(
            {
                "rank": rank,
                "class_id": int(class_id),
                "class_name": class_name,
                "confidence": confidence,
            }
        )
        lines.append(f"Top{rank}: {class_name} {confidence:.3f}")

    annotated = draw_text_lines(frame, lines)
    return {
        "annotated": annotated,
        "record": {
            "image": image_path.name,
            "task": "classify",
            "top_predictions": top_predictions,
            "top1": top_predictions[0] if top_predictions else None,
        },
    }


def detect_like_image(model: YOLO, image_path: Path, conf: float) -> Dict[str, Any]:
    results = model.predict(source=str(image_path), conf=conf, verbose=False)
    result = results[0]
    annotated = result.plot()
    detections: List[Dict[str, Any]] = []
    names = getattr(model, "names", {}) or {}

    boxes = getattr(result, "boxes", None)
    if boxes is not None:
        for box in boxes:
            xyxy = box.xyxy[0].cpu().numpy().tolist()
            class_id = int(box.cls[0])
            score = float(box.conf[0])
            detections.append(
                {
                    "class_id": class_id,
                    "class_name": str(names.get(class_id, class_id)),
                    "confidence": score,
                    "box": [float(v) for v in xyxy],
                }
            )

    return {
        "annotated": annotated,
        "record": {
            "image": image_path.name,
            "task": getattr(model, "task", None) or model.overrides.get("task"),
            "detections": detections,
            "detection_count": len(detections),
        },
    }


def main() -> int:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    image_paths = iter_images(args.input_dir, args.recursive)
    if not image_paths:
        print(f"No images found in {args.input_dir}")
        return 0

    if not args.model_path.exists():
        raise SystemExit(f"Model not found: {args.model_path}")

    model = YOLO(str(args.model_path))
    model_info = collect_model_info(model, args.expected_classes)
    task = model_info["task"]

    print(f"Model: {args.model_path}")
    print(f"Task: {task}")
    print(f"Classes: {model_info['class_count']}")
    print(f"Matched expected classes: {model_info['matched_expected_classes']}")
    print(f"Missing expected classes: {model_info['missing_expected_classes']}")

    manifest = {
        "model": {
            "path": str(args.model_path),
            **model_info,
        },
        "images": [],
    }

    for image_path in image_paths:
        if task == "classify":
            run_result = classify_image(model, image_path, args.topk)
        else:
            run_result = detect_like_image(model, image_path, args.conf)

        output_path = args.output_dir / f"{image_path.stem}_annotated{image_path.suffix}"
        cv2.imwrite(str(output_path), run_result["annotated"])

        record = dict(run_result["record"])
        record["output"] = output_path.name
        manifest["images"].append(record)

        if task == "classify":
            top1 = record.get("top1") or {}
            print(
                f"{image_path.name}: top1={top1.get('class_name')} "
                f"conf={top1.get('confidence', 0.0):.3f}"
            )
        else:
            print(f"{image_path.name}: detections={record['detection_count']}")

    summary_path = args.output_dir / "summary.json"
    summary_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Summary saved to {summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
