from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Awaitable, Callable, Optional

import asyncpg
from sqlalchemy import text
from sqlalchemy.engine import make_url

from .config import settings
from .database import engine

logger = logging.getLogger(__name__)


def _postgres_dsn() -> str:
    url = make_url(settings.DATABASE_URL)
    if not url.drivername.startswith("postgresql"):
        raise RuntimeError("PostgreSQL is required for realtime notifications")
    driverless = url.set(drivername="postgresql")
    return driverless.render_as_string(hide_password=False)


async def publish_realtime_message(payload: dict[str, Any]) -> None:
    if engine.dialect.name != "postgresql":
        logger.warning("Skipping realtime notification because database is not PostgreSQL")
        return

    message = json.dumps(payload, ensure_ascii=False, default=str)
    async with engine.begin() as conn:
        await conn.execute(
            text("SELECT pg_notify(:channel, :payload)"),
            {"channel": settings.PG_NOTIFY_CHANNEL, "payload": message},
        )


class PostgresNotificationRelay:
    def __init__(self, callback: Callable[[dict[str, Any]], Awaitable[None]]):
        self._callback = callback
        self._conn: Optional[asyncpg.Connection] = None

    async def start(self) -> None:
        if engine.dialect.name != "postgresql":
            logger.warning("Realtime relay disabled because database is not PostgreSQL")
            return
        self._conn = await asyncpg.connect(_postgres_dsn())
        await self._conn.add_listener(settings.PG_NOTIFY_CHANNEL, self._handle_payload)
        logger.info("Started realtime notification relay on channel %s", settings.PG_NOTIFY_CHANNEL)

    async def stop(self) -> None:
        if self._conn is None:
            return
        try:
            await self._conn.remove_listener(settings.PG_NOTIFY_CHANNEL, self._handle_payload)
        except Exception:
            logger.exception("Failed to remove realtime notification listener")
        await self._conn.close()
        self._conn = None

    def _handle_payload(
        self,
        _connection: asyncpg.Connection,
        _pid: int,
        _channel: str,
        payload: str,
    ) -> None:
        async def _dispatch() -> None:
            try:
                await self._callback(json.loads(payload))
            except Exception:
                logger.exception("Failed to dispatch realtime payload")

        asyncio.create_task(_dispatch())

