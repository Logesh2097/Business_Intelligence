"""MongoDB connection and health: conversations, messages, reports, document records
(docs/design/data-model.md §5).

The client connects on first use, so the API starts even when MongoDB is down and
/readyz reports it.

How a store function uses it (every query a later phase adds filters on `tenant_id`):

    async def find_conversation(mongo: Mongo, tenant_id: str, conversation_id: str):
        async with mongo.guard():
            return await mongo.collection("conversations").find_one(
                {"tenant_id": tenant_id, "_id": conversation_id}
            )

Inside `guard()`, a server that is down or slow becomes an `UnavailableError`
(HTTP 503); the cause is logged, never returned. Routes never touch the client.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from pymongo import AsyncMongoClient
from pymongo.asynchronous.collection import AsyncCollection
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import ConnectionFailure, ExecutionTimeout, NetworkTimeout

from app.core.config import Settings
from app.core.errors import UnavailableError

log = logging.getLogger("bi.mongo")

Document = dict[str, Any]

SLOW_MESSAGE = "The database took too long to answer. Please try again."


class Mongo:
    def __init__(self, settings: Settings) -> None:
        self.pool_max = settings.mongo_pool_max
        self.client: AsyncMongoClient[Document] = AsyncMongoClient(
            settings.mongo_url,
            serverSelectionTimeoutMS=settings.mongo_timeout_ms,
            connectTimeoutMS=settings.mongo_timeout_ms,
            maxPoolSize=settings.mongo_pool_max,
            connect=False,  # no network until the first operation
            tz_aware=True,
        )
        self.db: AsyncDatabase[Document] = self.client[settings.mongo_db]

    # -- lifecycle ---------------------------------------------------------

    async def close(self) -> None:
        await self.client.close()

    # -- access ------------------------------------------------------------

    def collection(self, name: str) -> AsyncCollection[Document]:
        return self.db[name]

    @asynccontextmanager
    async def guard(self) -> AsyncIterator[None]:
        """Turn connection failures and timeouts into `UnavailableError`."""
        try:
            yield
        except (ExecutionTimeout, NetworkTimeout) as exc:
            log.warning("operation timed out", extra={"kind": type(exc).__name__})
            raise UnavailableError(SLOW_MESSAGE) from exc
        except ConnectionFailure as exc:
            # includes ServerSelectionTimeoutError: no server answered in time
            log.warning("mongo connection failed", extra={"kind": type(exc).__name__})
            raise UnavailableError() from exc

    # -- health ------------------------------------------------------------

    async def ping(self) -> None:
        """Raise unless the server answers."""
        await self.client.admin.command("ping")

    async def server_version(self) -> str:
        info = await self.client.server_info()
        return str(info.get("version", ""))

    @property
    def database(self) -> str:
        return self.db.name
