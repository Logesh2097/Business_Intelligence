"""Uploading, listing, re-indexing and deleting a company's documents (rag.md §2, §5).

The steps of an upload, in order: check the file, refuse a duplicate, save the file,
write the record, index it. The record's `status` tells how indexing ended.
"""

from __future__ import annotations

import hashlib
import logging

from app.core.config import Settings
from app.core.context import Caller
from app.core.errors import (
    BadRequestError,
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
    TooLargeError,
)
from app.llm.embeddings import Embedder
from app.rag.detect import RejectedFile, clean_filename, clean_title, detect
from app.rag.ingest import ingest
from app.store import documents
from app.store.connections import Stores
from app.store.documents import DocumentRecord
from app.store.files import FileStore

log = logging.getLogger("bi.documents")
audit = logging.getLogger("bi.audit")

FILE_MISSING = "The stored file of this document is missing. Upload it again."


def _need(caller: Caller, permission: str) -> None:
    if not caller.can(permission):
        raise PermissionDeniedError()


def _audit(action: str, caller: Caller, document_id: str) -> None:
    # the audit table (data-model.md) is not built yet; until it is, this log line is it
    audit.info(
        "audit",
        extra={
            "action": action,
            "tenant_id": str(caller.tenant_id),
            "user_id": str(caller.user_id),
            "document_id": document_id,
        },
    )


async def _reload(stores: Stores, caller: Caller, document_id: str) -> DocumentRecord:
    record = await documents.find(
        stores.mongo, tenant_id=str(caller.tenant_id), document_id=document_id
    )
    if record is None:
        raise NotFoundError("No such document.")
    return record


async def upload(
    *,
    data: bytes,
    filename: str,
    title: str | None,
    caller: Caller,
    stores: Stores,
    files: FileStore,
    embedder: Embedder,
    settings: Settings,
) -> DocumentRecord:
    _need(caller, "documents:manage")
    tenant_id = str(caller.tenant_id)
    if len(data) > settings.doc_max_mb * 1024 * 1024:
        raise TooLargeError(f"A document may be at most {settings.doc_max_mb} MB.")
    try:
        kind = detect(data, filename)
    except RejectedFile as refusal:
        raise BadRequestError(refusal.reason, code="unsupported_file") from None

    sha256 = hashlib.sha256(data).hexdigest()
    existing = await documents.find_by_sha(stores.mongo, tenant_id=tenant_id, sha256=sha256)
    if existing is not None:
        raise ConflictError(
            f'This file was already uploaded as "{existing.title}".', code="duplicate_document"
        )
    if await documents.count(stores.mongo, tenant_id=tenant_id) >= settings.doc_max_per_tenant:
        raise ConflictError(
            f"The limit of {settings.doc_max_per_tenant} documents is reached. "
            "Delete one before uploading another.",
            code="document_limit",
        )

    document_id = documents.new_id()
    shown = clean_filename(filename)
    storage_path = await files.save(tenant_id, document_id, kind, data)
    record = await documents.create(
        stores.mongo,
        tenant_id=tenant_id,
        document_id=document_id,
        uploaded_by=str(caller.user_id),
        filename=shown,
        title=clean_title(title, shown),
        kind=kind,
        size=len(data),
        sha256=sha256,
        storage_path=storage_path,
    )
    _audit("document.upload", caller, document_id)
    await ingest(record, data, mongo=stores.mongo, qdrant=stores.qdrant, embedder=embedder)
    return await _reload(stores, caller, document_id)


async def list_documents(*, caller: Caller, stores: Stores) -> list[DocumentRecord]:
    _need(caller, "documents:read")
    return await documents.list_all(stores.mongo, tenant_id=str(caller.tenant_id))


async def get_document(*, document_id: str, caller: Caller, stores: Stores) -> DocumentRecord:
    _need(caller, "documents:read")
    return await _reload(stores, caller, document_id)


async def reindex(
    *, document_id: str, caller: Caller, stores: Stores, files: FileStore, embedder: Embedder
) -> DocumentRecord:
    _need(caller, "documents:manage")
    record = await _reload(stores, caller, document_id)
    data = await files.read(record.tenant_id, record.id, record.kind)
    if data is None:
        raise ConflictError(FILE_MISSING, code="file_missing")
    _audit("document.reindex", caller, document_id)
    await ingest(record, data, mongo=stores.mongo, qdrant=stores.qdrant, embedder=embedder)
    return await _reload(stores, caller, document_id)


async def delete(*, document_id: str, caller: Caller, stores: Stores, files: FileStore) -> None:
    _need(caller, "documents:manage")
    record = await _reload(stores, caller, document_id)
    # the passages go first: a document that cannot be searched is the safe half-way state
    await stores.qdrant.delete_document(tenant_id=record.tenant_id, document_id=record.id)
    await documents.mark_deleted(stores.mongo, tenant_id=record.tenant_id, document_id=record.id)
    await files.delete(record.tenant_id, record.id)
    _audit("document.delete", caller, document_id)
