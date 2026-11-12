"""Everything the API holds open, made once at start and closed at shutdown."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from app.core.config import Settings
from app.store.mongo import Mongo
from app.store.postgres import Postgres
from app.store.qdrant import Qdrant

log = logging.getLogger("bi.stores")


@dataclass
class Stores:
    settings: Settings
    postgres: Postgres
    mongo: Mongo
    qdrant: Qdrant

    @classmethod
    async def open(cls, settings: Settings) -> Stores:
        """Create the pools and clients. Does not wait for any server to answer."""
        postgres = Postgres(settings)
        await postgres.open()
        log.info("stores opened")
        return cls(
            settings=settings, postgres=postgres, mongo=Mongo(settings), qdrant=Qdrant(settings)
        )

    async def close(self) -> None:
        """Close everything, even if one of them fails to close."""
        try:
            await self.postgres.close()
        finally:
            await self.mongo.close()
            log.info("stores closed")
