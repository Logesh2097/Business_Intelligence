"""/api/v1/documents: a company's uploaded documents (docs/api.md §6).

An upload is the file itself as the request body (any content type), with its name in
the query string: `POST /api/v1/documents?filename=policy.pdf&title=Refund policy`.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Query, Request, Response
from pydantic import BaseModel, Field

from app.api.deps import CallerDep, EmbedderDep, FilesDep, SettingsDep, StoresDep
from app.core.errors import (
    BadRequestError,
    NotFoundError,
    PermissionDeniedError,
    TooLargeError,
)
from app.store.documents import DocumentRecord
from app.workflows import documents

router = APIRouter(prefix="/api/v1/documents", tags=["documents"])


class DocumentOut(BaseModel):
    id: str
    filename: str
    title: str
    kind: Literal["pdf", "docx", "txt", "md"]
    size: int = Field(description="Bytes")
    status: Literal["processing", "ready", "failed"]
    error: str | None = Field(description="Why indexing failed, as a sentence; else null")
    chunk_count: int
    page_count: int | None
    created_at: dt.datetime
    updated_at: dt.datetime


class DocumentList(BaseModel):
    documents: list[DocumentOut]
    can_manage: bool = Field(description="Whether the caller may upload, re-index and delete")
    max_mb: int
    max_documents: int


def _out(record: DocumentRecord) -> DocumentOut:
    return DocumentOut(
        id=record.id,
        filename=record.filename,
        title=record.title,
        kind=record.kind,
        size=record.size,
        status=record.status,
        error=record.error,
        chunk_count=record.chunk_count,
        page_count=record.page_count,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _id(document_id: str) -> str:
    try:
        return str(uuid.UUID(document_id))
    except ValueError:
        raise NotFoundError("No such document.") from None


async def _body(request: Request, max_bytes: int, max_mb: int) -> bytes:
    """The request body, never more than the limit held in memory."""
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > max_bytes:
        raise TooLargeError(f"A document may be at most {max_mb} MB.")
    received = bytearray()
    async for piece in request.stream():
        received += piece
        if len(received) > max_bytes:
            raise TooLargeError(f"A document may be at most {max_mb} MB.")
    return bytes(received)


@router.post("", status_code=201)
async def upload(
    request: Request,
    caller: CallerDep,
    stores: StoresDep,
    files: FilesDep,
    embedder: EmbedderDep,
    settings: SettingsDep,
    filename: Annotated[str, Query(min_length=1, max_length=300)],
    title: Annotated[str | None, Query(max_length=300)] = None,
) -> DocumentOut:
    if not caller.can("documents:manage"):  # before the body is read
        raise PermissionDeniedError()
    data = await _body(request, settings.doc_max_mb * 1024 * 1024, settings.doc_max_mb)
    if not data:
        raise BadRequestError("The file is empty.", code="unsupported_file")
    record = await documents.upload(
        data=data,
        filename=filename,
        title=title,
        caller=caller,
        stores=stores,
        files=files,
        embedder=embedder,
        settings=settings,
    )
    return _out(record)


@router.get("")
async def list_documents(
    caller: CallerDep, stores: StoresDep, settings: SettingsDep
) -> DocumentList:
    found = await documents.list_documents(caller=caller, stores=stores)
    return DocumentList(
        documents=[_out(record) for record in found],
        can_manage=caller.can("documents:manage"),
        max_mb=settings.doc_max_mb,
        max_documents=settings.doc_max_per_tenant,
    )


@router.get("/{document_id}")
async def get_document(document_id: str, caller: CallerDep, stores: StoresDep) -> DocumentOut:
    return _out(
        await documents.get_document(document_id=_id(document_id), caller=caller, stores=stores)
    )


@router.post("/{document_id}/reindex")
async def reindex(
    document_id: str, caller: CallerDep, stores: StoresDep, files: FilesDep, embedder: EmbedderDep
) -> DocumentOut:
    record = await documents.reindex(
        document_id=_id(document_id), caller=caller, stores=stores, files=files, embedder=embedder
    )
    return _out(record)


@router.delete("/{document_id}", status_code=204)
async def delete(
    document_id: str, caller: CallerDep, stores: StoresDep, files: FilesDep
) -> Response:
    await documents.delete(document_id=_id(document_id), caller=caller, stores=stores, files=files)
    return Response(status_code=204)
