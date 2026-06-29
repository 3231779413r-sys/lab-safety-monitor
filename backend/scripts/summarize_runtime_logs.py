#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import re
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable


FRAME_TIMING_RE = re.compile(r"FRAME_TIMING\s+(\{.*\})")
SHARD_SUMMARY_RE = re.compile(r"CAMERA_RUNTIME_SHARD_SUMMARY\s+(\{.*\})")
CAMERA_SUMMARY_RE = re.compile(r"CAMERA_RUNTIME_SUMMARY\s+(\{.*\})")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Summarize runtime logs for lab safety monitor.")
    parser.add_argument("--logs-root", default="logs", help="Root logs directory")
    parser.add_argument("--date", help="Filter by yyyy/mm/dd path, e.g. 2026/06/23")
    parser.add_argument("--hour", help="Filter by hour log file, e.g. 18")
    parser.add_argument("--start", help="Filter entries >= ISO datetime")
    parser.add_argument("--end", help="Filter entries <= ISO datetime")
    return parser.parse_args()


def discover_files(root: Path, date_filter: str | None, hour_filter: str | None) -> list[Path]:
    candidates = list(root.rglob("*.log"))
    results: list[Path] = []
    normalized_date = date_filter.strip("/") if date_filter else None
    normalized_hour = f"{hour_filter}.log" if hour_filter and not hour_filter.endswith(".log") else hour_filter
    for path in candidates:
        path_str = path.as_posix()
        if normalized_date and normalized_date not in path_str:
            continue
        if normalized_hour and path.name != normalized_hour:
            continue
        results.append(path)
    return sorted(results)


def parse_line_timestamp(line: str) -> datetime | None:
    prefix = line[:23]
    try:
        return datetime.strptime(prefix, "%Y-%m-%d %H:%M:%S,%f")
    except ValueError:
        return None


def in_range(ts: datetime | None, start: datetime | None, end: datetime | None) -> bool:
    if ts is None:
        return True
    if start and ts < start:
        return False
    if end and ts > end:
        return False
    return True


def percentile(values: list[float], ratio: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    idx = int(math.ceil((len(ordered) - 1) * ratio))
    idx = max(0, min(len(ordered) - 1, idx))
    return ordered[idx]


def format_stats(values: list[float]) -> str:
    if not values:
        return "n/a"
    return "count={count} p50={p50:.1f} p95={p95:.1f} p99={p99:.1f}".format(
        count=len(values),
        p50=percentile(values, 0.50) or 0.0,
        p95=percentile(values, 0.95) or 0.0,
        p99=percentile(values, 0.99) or 0.0,
    )


def top_items(mapping: dict[str, list[float]], limit: int = 5) -> list[tuple[str, float, float, int]]:
    ranked: list[tuple[str, float, float, int]] = []
    for key, values in mapping.items():
        if not values:
            continue
        avg = sum(values) / len(values)
        p95 = percentile(values, 0.95) or 0.0
        ranked.append((key, avg, p95, len(values)))
    ranked.sort(key=lambda item: item[2], reverse=True)
    return ranked[:limit]


def recommend_profile(
    *,
    avg_latency: float | None,
    p95_latency: float | None,
    avg_person_count: float | None,
    avg_backlog: float | None,
    backpressure_skips: int,
) -> tuple[bool, str, str]:
    reasons: list[str] = []
    if avg_latency is not None and avg_latency > 600.0:
        reasons.append(f"avg_latency={avg_latency:.1f}")
    if p95_latency is not None and p95_latency > 1200.0:
        reasons.append(f"p95_latency={p95_latency:.1f}")
    if avg_person_count is not None and avg_person_count > 1.5:
        reasons.append(f"avg_person_count={avg_person_count:.2f}")
    if avg_backlog is not None and avg_backlog > 0.5:
        reasons.append(f"avg_backlog={avg_backlog:.2f}")
    if backpressure_skips >= 3:
        reasons.append(f"backpressure_skips={backpressure_skips}")
    if reasons:
        return True, "fast", ",".join(reasons)
    if (
        avg_latency is not None
        and avg_latency < 220.0
        and (p95_latency or 0.0) < 350.0
        and (avg_person_count or 0.0) <= 0.5
    ):
        return False, "accurate", "latency headroom is high"
    return False, "balanced", "current load is within balanced profile range"


def recommend_target_shard(
    current_shard: str | None,
    shard_payloads: list[dict[str, Any]],
    *,
    hot_camera: bool,
) -> str | None:
    if not hot_camera or not shard_payloads:
        return None
    ranked = sorted(
        shard_payloads,
        key=lambda item: (
            float(item.get("p95_latency_ms") or item.get("avg_latency_ms") or 0.0),
            int(item.get("active_camera_count") or 0),
        ),
    )
    for shard in ranked:
        candidate = f"worker-shard-{int(shard.get('shard_index'))}"
        if candidate != current_shard:
            return candidate
    return None


def iter_lines(files: Iterable[Path]) -> Iterable[tuple[Path, str]]:
    for path in files:
        try:
            with path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    yield path, line.rstrip("\n")
        except OSError:
            continue


def detect_shard_key(path: Path) -> str:
    for part in path.parts:
        if part.startswith("worker-shard-"):
            return part
        if part in {"inference", "identity", "backend", "frontend"}:
            fallback = part
    return locals().get("fallback", path.parent.name)


def main() -> int:
    args = parse_args()
    logs_root = Path(args.logs_root)
    start = datetime.fromisoformat(args.start) if args.start else None
    end = datetime.fromisoformat(args.end) if args.end else None
    files = discover_files(logs_root, args.date, args.hour)
    if not files:
        print("No log files matched.")
        return 1

    inference_total_ms: list[float] = []
    inference_batch_wait_ms: list[float] = []
    inference_compute_ms: list[float] = []
    identity_total_ms: list[float] = []
    invalid_frame_count = 0
    expired_async_identity_count = 0
    identity_async_merged_count = 0
    batch_size_dist: Counter[int] = Counter()
    camera_latency: dict[str, list[float]] = defaultdict(list)
    camera_person_count: dict[str, list[float]] = defaultdict(list)
    camera_complexity: dict[str, list[float]] = defaultdict(list)
    camera_shards: dict[str, Counter[str]] = defaultdict(Counter)
    camera_backpressure_skips: Counter[str] = Counter()
    shard_latency: dict[str, list[float]] = defaultdict(list)
    latest_camera_summaries: dict[str, dict[str, Any]] = {}
    latest_shard_summaries: dict[str, dict[str, Any]] = {}

    for path, line in iter_lines(files):
        ts = parse_line_timestamp(line)
        if not in_range(ts, start, end):
            continue
        if "Dropping invalid frame payload" in line:
            invalid_frame_count += 1
        if "Expired pending async identity update" in line:
            expired_async_identity_count += 1
        if "Skipping inference sample for camera=" in line:
            match = re.search(r"camera=([a-f0-9-]+)", line)
            if match:
                camera_backpressure_skips[match.group(1)] += 1
        if "FRAME_TIMING" in line:
            match = FRAME_TIMING_RE.search(line)
            if not match:
                continue
            try:
                payload = json.loads(match.group(1))
            except json.JSONDecodeError:
                continue
            if payload.get("stage") == "identity_async_merged":
                identity_async_merged_count += 1
            camera_id = str(payload.get("camera_id") or "")
            if payload.get("inference_total_ms") is not None:
                value = float(payload["inference_total_ms"])
                inference_total_ms.append(value)
                if camera_id:
                    camera_latency[camera_id].append(value)
                shard_key = detect_shard_key(path)
                shard_latency[shard_key].append(value)
                if camera_id:
                    camera_shards[camera_id][shard_key] += 1
            if payload.get("inference_batch_wait_ms") is not None:
                inference_batch_wait_ms.append(float(payload["inference_batch_wait_ms"]))
            if payload.get("inference_compute_ms") is not None:
                inference_compute_ms.append(float(payload["inference_compute_ms"]))
            if payload.get("identity_total_ms") is not None:
                identity_total_ms.append(float(payload["identity_total_ms"]))
            if payload.get("person_count") is not None and camera_id:
                camera_person_count[camera_id].append(float(payload["person_count"]))
            if payload.get("frame_complexity_score") is not None and camera_id:
                camera_complexity[camera_id].append(float(payload["frame_complexity_score"]))
            if payload.get("inference_batch_size") is not None:
                try:
                    batch_size_dist[int(payload["inference_batch_size"])] += 1
                except (TypeError, ValueError):
                    pass
            continue
        if "CAMERA_RUNTIME_SUMMARY" in line:
            match = CAMERA_SUMMARY_RE.search(line)
            if not match:
                continue
            try:
                payload = json.loads(match.group(1))
            except json.JSONDecodeError:
                continue
            camera_id = str(payload.get("camera_id") or "")
            if camera_id:
                latest_camera_summaries[camera_id] = payload
            continue
        if "CAMERA_RUNTIME_SHARD_SUMMARY" in line:
            match = SHARD_SUMMARY_RE.search(line)
            if not match:
                continue
            try:
                payload = json.loads(match.group(1))
            except json.JSONDecodeError:
                continue
            latest_shard_summaries[str(payload.get("shard_index"))] = payload

    print(f"Scanned files: {len(files)}")
    print(f"inference_total_ms: {format_stats(inference_total_ms)}")
    print(f"inference_batch_wait_ms: {format_stats(inference_batch_wait_ms)}")
    print(f"inference_compute_ms: {format_stats(inference_compute_ms)}")
    print(f"identity_total_ms: {format_stats(identity_total_ms)}")
    print(f"invalid_frame_count: {invalid_frame_count}")
    print(f"expired_async_identity_count: {expired_async_identity_count}")
    print(f"identity_async_merged_count: {identity_async_merged_count}")
    print("batch_size_distribution:", dict(sorted(batch_size_dist.items())))

    print("top_cameras_by_latency:")
    for camera_id, avg, p95, count in top_items(camera_latency):
        print(f"  {camera_id}: avg={avg:.1f} p95={p95:.1f} count={count}")

    print("top_shards_by_latency:")
    for shard_id, avg, p95, count in top_items(shard_latency):
        print(f"  {shard_id}: avg={avg:.1f} p95={p95:.1f} count={count}")

    if latest_shard_summaries:
        print("latest_shard_summaries:")
        for shard_id in sorted(latest_shard_summaries):
            payload = latest_shard_summaries[shard_id]
            print(
                f"  shard={shard_id} active={payload.get('active_camera_count')} "
                f"avg_latency={payload.get('avg_latency_ms')} p95_latency={payload.get('p95_latency_ms')} "
                f"queues={payload.get('queue_pressure_snapshot')}"
            )
    shard_payloads = list(latest_shard_summaries.values())
    print("camera_recommendations:")
    hot_fast_cameras: list[str] = []
    for camera_id, avg, p95, count in top_items(camera_latency, limit=12):
        avg_person = (
            round(sum(camera_person_count[camera_id]) / len(camera_person_count[camera_id]), 2)
            if camera_person_count.get(camera_id)
            else None
        )
        latest_runtime = latest_camera_summaries.get(camera_id, {})
        avg_backlog = latest_runtime.get("avg_backlog")
        backpressure_skips = int(latest_runtime.get("inference_backpressure_skip_count") or camera_backpressure_skips[camera_id] or 0)
        hot_camera, profile, reason = recommend_profile(
            avg_latency=avg,
            p95_latency=p95,
            avg_person_count=avg_person,
            avg_backlog=float(avg_backlog) if avg_backlog is not None else None,
            backpressure_skips=backpressure_skips,
        )
        current_shard = camera_shards[camera_id].most_common(1)[0][0] if camera_shards.get(camera_id) else None
        target_shard = recommend_target_shard(current_shard, shard_payloads, hot_camera=hot_camera)
        if hot_camera and profile == "fast":
            hot_fast_cameras.append(camera_id)
        print(
            "  {camera}: hot_camera={hot} recommended_profile={profile} reason={reason} current_shard={current} recommended_target_shard={target}".format(
                camera=camera_id,
                hot=str(hot_camera).lower(),
                profile=profile,
                reason=reason,
                current=current_shard,
                target=target_shard,
            )
        )

    if hot_fast_cameras:
        print("recommend fast profile for:", hot_fast_cameras)
    move_suggestions: list[str] = []
    for camera_id, avg, p95, _count in top_items(camera_latency, limit=12):
        current_shard = camera_shards[camera_id].most_common(1)[0][0] if camera_shards.get(camera_id) else None
        hot_camera, _profile, _reason = recommend_profile(
            avg_latency=avg,
            p95_latency=p95,
            avg_person_count=round(sum(camera_person_count[camera_id]) / len(camera_person_count[camera_id]), 2)
            if camera_person_count.get(camera_id)
            else None,
            avg_backlog=float((latest_camera_summaries.get(camera_id, {}) or {}).get("avg_backlog") or 0.0),
            backpressure_skips=int((latest_camera_summaries.get(camera_id, {}) or {}).get("inference_backpressure_skip_count") or camera_backpressure_skips[camera_id] or 0),
        )
        target_shard = recommend_target_shard(current_shard, shard_payloads, hot_camera=hot_camera)
        if hot_camera and current_shard and target_shard:
            move_suggestions.append(f"move {camera_id} from {current_shard} to {target_shard}")
    if move_suggestions:
        print("repartition_recommendations:")
        for item in move_suggestions:
            print(f"  {item}")

    batch_total = sum(batch_size_dist.values())
    batch_size_one_ratio = (batch_size_dist.get(1, 0) / batch_total) if batch_total else 0.0
    capacity_warning = batch_size_one_ratio >= 0.85 and len(hot_fast_cameras) >= 2 and (percentile(inference_total_ms, 0.95) or 0.0) > 500.0
    print(
        "capacity_warning:",
        {
            "warning": capacity_warning,
            "batch_size_one_ratio": round(batch_size_one_ratio, 3),
            "reason": (
                "low-latency mode is near capacity; consider adding inference workers or GPU"
                if capacity_warning
                else None
            ),
        },
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
