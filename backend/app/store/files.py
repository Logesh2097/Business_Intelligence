"""Where uploaded files are kept: a local folder (`BI_UPLOAD_DIR`).

The path is built by code from the company, the document id and the detected type:
`<root>/<company>/<document id>/original.<ext>`. The uploaded name is never part of
it, so a name cannot place a file anywhere else. A Cloud Storage bucket
(`BI_GCS_BUCKET`) is in the design and is not built.
"""

from __future__ import annotations

import asyncio
import shutil
import uuid
from pathlib import Path

_EXTENSIONS = {"pdf": "pdf", "docx": "docx", "txt": "txt", "md": "md"}


class FileStore:
    def __init__(self, root: str) -> None:
        self.root = Path(root).resolve()

    def _folder(self, tenant_id: str, document_id: str) -> Path:
        # both must be UUIDs: nothing else can become a path segment
        folder = self.root / str(uuid.UUID(tenant_id)) / str(uuid.UUID(document_id))
        if self.root not in folder.parents:
            raise ValueError("a file path left the upload folder")
        return folder

    def path(self, tenant_id: str, document_id: str, kind: str) -> Path:
        return self._folder(tenant_id, document_id) / f"original.{_EXTENSIONS[kind]}"

    async def save(self, tenant_id: str, document_id: str, kind: str, data: bytes) -> str:
        """Write the file; return its path relative to the root (what the record keeps)."""
        target = self.path(tenant_id, document_id, kind)

        def write() -> None:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)

        await asyncio.to_thread(write)
        return str(target.relative_to(self.root))

    async def read(self, tenant_id: str, document_id: str, kind: str) -> bytes | None:
        target = self.path(tenant_id, document_id, kind)
        return await asyncio.to_thread(lambda: target.read_bytes() if target.is_file() else None)

    async def delete(self, tenant_id: str, document_id: str) -> None:
        folder = self._folder(tenant_id, document_id)
        await asyncio.to_thread(shutil.rmtree, folder, True)
