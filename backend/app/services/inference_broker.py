from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Awaitable, Callable, Optional

import aio_pika

from ..core.config import settings
from ..inference_payloads import build_frame_task_message
from ..telemetry import clone_telemetry, mark_telemetry

logger = logging.getLogger(__name__)


InferenceResultHandler = Callable[[str, str, dict[str, Any]], Awaitable[None] | None]


@dataclass
class OutboundInferenceMessage:
    routing_key: str
    request_id: str
    camera_id: str
    video_source: str
    frame_path: str | None
    frame_jpeg: bytes | None
    inference_options: dict[str, Any]
    result_queue: str
    submitted_at: datetime
    telemetry: dict[str, Any]


class InferenceBroker:
    """RabbitMQ-backed bridge between capture workers and inference workers."""

    def __init__(self) -> None:
        self._connection: Optional[aio_pika.abc.AbstractRobustConnection] = None
        self._channel: Optional[aio_pika.abc.AbstractRobustChannel] = None
        self._frame_exchange: Optional[aio_pika.abc.AbstractExchange] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._result_handler: Optional[InferenceResultHandler] = None
        self._started = False
        self._last_not_ready_log_at = 0.0

    async def start(
        self,
        *,
        loop: asyncio.AbstractEventLoop,
        result_handler: InferenceResultHandler,
    ) -> None:
        if self._started:
            return

        self._loop = loop
        self._result_handler = result_handler
        self._connection = await aio_pika.connect_robust(settings.RABBITMQ_URL)
        self._channel = await self._connection.channel()
        await self._channel.set_qos(prefetch_count=max(32, settings.INFERENCE_BATCH_SIZE * 8))

        self._frame_exchange = self._channel.default_exchange
        await self._channel.declare_queue(settings.RABBITMQ_FRAME_QUEUE, durable=True)
        result_queue = await self._channel.declare_queue(
            self._result_queue_name(),
            durable=True,
        )
        await result_queue.consume(self._on_result_message)
        self._started = True
        logger.info(
            "InferenceBroker connected to RabbitMQ frame_queue=%s result_queue=%s",
            settings.RABBITMQ_FRAME_QUEUE,
            self._result_queue_name(),
        )

    async def stop(self) -> None:
        if not self._started:
            return

        if self._channel is not None:
            await self._channel.close()
        if self._connection is not None:
            await self._connection.close()

        self._connection = None
        self._channel = None
        self._frame_exchange = None
        self._started = False

    def publish_frame(
        self,
        *,
        request_id: str,
        camera_id: str,
        video_source: str,
        frame_path: str | None,
        frame_jpeg: bytes | None,
        inference_options: dict[str, Any] | None,
        submitted_at: datetime,
        telemetry: dict[str, Any] | None = None,
    ) -> bool:
        if not self._is_publish_ready():
            self._log_not_ready(request_id=request_id, camera_id=camera_id)
            return False

        message = OutboundInferenceMessage(
            routing_key=settings.RABBITMQ_FRAME_QUEUE,
            request_id=request_id,
            camera_id=camera_id,
            video_source=video_source,
            frame_path=frame_path,
            frame_jpeg=frame_jpeg,
            inference_options=dict(inference_options or {}),
            result_queue=self._result_queue_name(),
            submitted_at=submitted_at,
            telemetry=mark_telemetry(
                telemetry,
                "inference_publish_enqueued_at",
            ),
        )
        future = asyncio.run_coroutine_threadsafe(self._publish_binary(message), self._loop)
        timeout_seconds = max(
            0.1,
            float(
                getattr(
                    settings,
                    "BROKER_PUBLISH_TIMEOUT_SECONDS",
                    getattr(settings, "RABBITMQ_PUBLISH_TIMEOUT_SECONDS", 2.0),
                )
            ),
        )
        try:
            future.result(timeout=timeout_seconds)
            return True
        except Exception:
            logger.exception(
                "Inference publish failed request_id=%s camera_id=%s timeout=%.2fs",
                request_id,
                camera_id,
                timeout_seconds,
            )
            return False

    async def _publish_binary(self, message: OutboundInferenceMessage) -> None:
        if not self._is_publish_ready():
            raise RuntimeError("RabbitMQ exchange is not initialized")
        body = build_frame_task_message(
            request_id=message.request_id,
            camera_id=message.camera_id,
            video_source=message.video_source,
            frame_path=message.frame_path,
            frame_jpeg=message.frame_jpeg,
            inference_options=message.inference_options,
            result_queue=message.result_queue,
            submitted_at=message.submitted_at,
            telemetry=mark_telemetry(message.telemetry, "inference_publish_started_at"),
        )
        await self._frame_exchange.publish(
            aio_pika.Message(
                body=json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
                content_type="application/json",
                delivery_mode=aio_pika.DeliveryMode.NOT_PERSISTENT,
                timestamp=datetime.now(),
            ),
            routing_key=message.routing_key,
        )

    def _is_publish_ready(self) -> bool:
        if not self._started or self._loop is None or self._frame_exchange is None:
            return False
        channel = self._channel
        connection = self._connection
        if channel is None or connection is None:
            return False
        if channel.is_closed or connection.is_closed:
            return False
        return True

    def _log_not_ready(self, *, request_id: str, camera_id: str) -> None:
        now = time.monotonic()
        if now - self._last_not_ready_log_at >= 5.0:
            logger.warning(
                "InferenceBroker is not ready, drop request_id=%s camera_id=%s",
                request_id,
                camera_id,
            )
            self._last_not_ready_log_at = now

    async def _on_result_message(self, message: aio_pika.abc.AbstractIncomingMessage) -> None:
        async with message.process(requeue=False):
            try:
                payload = json.loads(message.body.decode("utf-8"))
            except Exception:
                logger.exception("Failed to decode inference result message")
                return

            camera_id = str(payload.get("camera_id") or "")
            request_id = str(payload.get("request_id") or "")
            detections = payload.get("detections") or {}
            telemetry = mark_telemetry(
                clone_telemetry(payload.get("telemetry")),
                "inference_result_broker_received_at",
            )
            if not camera_id or not request_id:
                logger.warning("Ignoring malformed inference result payload: %s", payload)
                return

            if payload.get("error"):
                logger.warning(
                    "Inference worker returned error for camera=%s request_id=%s: %s",
                    camera_id,
                    request_id,
                    payload.get("error"),
                )

            handler = self._result_handler
            if handler is None:
                return
            result = handler(
                camera_id,
                request_id,
                {
                    **detections,
                    "_telemetry": telemetry,
                    "_error": payload.get("error"),
                    "_error_type": payload.get("error_type"),
                    "_error_detail": payload.get("error_detail"),
                    "_frame_path": payload.get("frame_path"),
                },
            )
            if asyncio.iscoroutine(result):
                await result

    def _result_queue_name(self) -> str:
        shard_index = int(getattr(settings, "CAMERA_MONITOR_SHARD_INDEX", 0))
        return f"{settings.RABBITMQ_RESULT_QUEUE}.worker-{shard_index}"


_inference_broker: Optional[InferenceBroker] = None


def get_inference_broker() -> InferenceBroker:
    global _inference_broker
    if _inference_broker is None:
        _inference_broker = InferenceBroker()
    return _inference_broker
