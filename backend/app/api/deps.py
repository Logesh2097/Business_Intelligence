"""What a route may ask for. Routes declare these with `Depends`; they never reach
into `request.app.state` themselves, and they never hold SQL or Mongo queries.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Header, Request

from app.core.config import Settings
from app.core.context import Caller
from app.core.errors import NotAuthenticatedError
from app.llm.base import LLM
from app.llm.embeddings import Embedder
from app.store import users
from app.store.connections import Stores
from app.store.files import FileStore


def get_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def get_stores(request: Request) -> Stores:
    stores: Stores = request.app.state.stores
    return stores


def get_llm(request: Request) -> LLM:
    llm: LLM = request.app.state.llm
    return llm


def get_embedder(request: Request) -> Embedder:
    embedder: Embedder = request.app.state.embedder
    return embedder


def get_files(request: Request) -> FileStore:
    files: FileStore = request.app.state.files
    return files


SettingsDep = Annotated[Settings, Depends(get_settings)]
EmbedderDep = Annotated[Embedder, Depends(get_embedder)]
FilesDep = Annotated[FileStore, Depends(get_files)]
StoresDep = Annotated[Stores, Depends(get_stores)]
LLMDep = Annotated[LLM, Depends(get_llm)]


async def get_caller(
    settings: SettingsDep,
    stores: StoresDep,
    x_dev_user: Annotated[str | None, Header()] = None,
) -> Caller:
    """Who is asking.

    SIGN-IN IS NOT BUILT YET. Until it is, development and test accept the email of a
    demo user in the `X-Dev-User` header, and every other environment refuses: there
    is no way to name a company without a real credential. The authentication phase
    replaces the body of this function; nothing that depends on `Caller` changes.
    """
    if not settings.relaxed:
        raise NotAuthenticatedError("Sign-in is not available yet.")
    if not x_dev_user:
        raise NotAuthenticatedError(
            "Sign-in is not built yet. In development, send the X-Dev-User header "
            "with the email of a demo user."
        )
    caller = await users.find_caller_by_email(stores.postgres, x_dev_user)
    if caller is None:
        raise NotAuthenticatedError("Unknown or inactive user.")
    return caller


CallerDep = Annotated[Caller, Depends(get_caller)]
