"""The records of uploaded documents (MongoDB collection `documents`,
docs/design/data-model.md §5). Every query filters on `tenant_id`, added here.

A deleted document keeps its record with `deleted_at` set, so an old answer can show
its source as removed. Deleted records are never returned by the functions below.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass
from typing import Any

from app.store.mongo import Mongo

COLLECTION = "documents"
LIST_LIMIT = 500

STATUSES = ("processing", "ready", "failed")


@dataclass(frozen=True)
class DocumentRecord:
    id: str
    tenant_id: str
    uploaded_by: str
    filename: str
    title: str
    kind: str  # pdf | docx | txt | md
    size: int
    sha256: str
    storage_path: str
    status: str  # processing | ready | failed
    error: str | None
    chunk_count: int
    page_count: int | None
    created_at: dt.datetime
    updated_at: dt.datetime


def _now() -> dt.datetime:
    # MongoDB keeps milliseconds; cut here, so a record just made equals the one read back
    now = dt.datetime.now(dt.timezone.utc)
    return now.replace(microsecond=now.microsecond // 1000 * 1000)


def _record(found: dict[str, Any]) -> DocumentRecord:
    return DocumentRecord(
        id=str(found["_id"]),
        tenant_id=str(found["tenant_id"]),
        uploaded_by=str(found["uploaded_by"]),
        filename=str(found["filename"]),
        title=str(found["title"]),
        kind=str(found["kind"]),
        size=int(found["size"]),
        sha256=str(found["sha256"]),
        storage_path=str(found["storage_path"]),
        status=str(found["status"]),
        error=found.get("error"),
        chunk_count=int(found.get("chunk_count") or 0),
        page_count=found.get("page_count"),
        created_at=found["created_at"],
        updated_at=found["updated_at"],
    )


def _mine(tenant_id: str, **more: Any) -> dict[str, Any]:
    if not tenant_id:
        raise ValueError("a company is required for every document query")
    return {"tenant_id": tenant_id, "deleted_at": None, **more}


def new_id() -> str:
    return str(uuid.uuid4())


async def create(
    mongo: Mongo,
    *,
    tenant_id: str,
    document_id: str,
    uploaded_by: str,
    filename: str,
    title: str,
    kind: str,
    size: int,
    sha256: str,
    storage_path: str,
) -> DocumentRecord:
    now = _now()
    record = {
        "_id": document_id,
        "tenant_id": tenant_id,
        "uploaded_by": uploaded_by,
        "filename": filename,
        "title": title,
        "kind": kind,
        "size": size,
        "sha256": sha256,
        "storage_path": storage_path,
        "status": "processing",
        "error": None,
        "chunk_count": 0,
        "page_count": None,
        "created_at": now,
        "updated_at": now,
        "deleted_at": None,
    }
    async with mongo.guard():
        await mongo.collection(COLLECTION).insert_one(record)
    return _record(record)


async def find(mongo: Mongo, *, tenant_id: str, document_id: str) -> DocumentRecord | None:
    async with mongo.guard():
        found = await mongo.collection(COLLECTION).find_one(_mine(tenant_id, _id=document_id))
    return _record(found) if found else None


async def find_by_sha(mongo: Mongo, *, tenant_id: str, sha256: str) -> DocumentRecord | None:
    async with mongo.guard():
        found = await mongo.collection(COLLECTION).find_one(_mine(tenant_id, sha256=sha256))
    return _record(found) if found else None


async def list_all(mongo: Mongo, *, tenant_id: str) -> list[DocumentRecord]:
    async with mongo.guard():
        cursor = mongo.collection(COLLECTION).find(_mine(tenant_id)).sort("created_at", -1)
        found = await cursor.to_list(LIST_LIMIT)
    return [_record(item) for item in found]


async def count(mongo: Mongo, *, tenant_id: str) -> int:
    async with mongo.guard():
        return int(await mongo.collection(COLLECTION).count_documents(_mine(tenant_id)))


async def set_status(
    mongo: Mongo,
    *,
    tenant_id: str,
    document_id: str,
    status: str,
    error: str | None = None,
    chunk_count: int = 0,
    page_count: int | None = None,
) -> None:
    if status not in STATUSES:
        raise ValueError(f"unknown document status {status!r}")
    async with mongo.guard():
        await mongo.collection(COLLECTION).update_one(
            _mine(tenant_id, _id=document_id),
            {
                "$set": {
                    "status": status,
                    "error": error,
                    "chunk_count": chunk_count,
                    "page_count": page_count,
                    "updated_at": _now(),
                }
            },
        )


async def mark_deleted(mongo: Mongo, *, tenant_id: str, document_id: str) -> bool:
    async with mongo.guard():
        result = await mongo.collection(COLLECTION).update_one(
            _mine(tenant_id, _id=document_id), {"$set": {"deleted_at": _now()}}
        )
    return bool(result.modified_count)
