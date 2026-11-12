"""REAL PROTOCOL, NO DATABASE: the MCP server, started on 127.0.0.1 for the test and
called with the SDK's own client over streamable HTTP.

Real: the `mcp` SDK on both sides, the HTTP transport, the token check, the registry.
Not here: PostgreSQL (the business tools are tested live in
tests/integration/test_tools_live.py); the tools used are the catalog ones, which read
the vocabulary, and search_documents with in-memory stand-ins.
"""

from __future__ import annotations

import asyncio
import socket
import threading
import time
from collections.abc import Iterator
from dataclasses import replace
from typing import Any

import httpx
import httpx2
import pytest
import uvicorn
from mcp.client.client import Client
from mcp.client.streamable_http import streamable_http_client

from app.agents import document_agent
from app.agents.document_agent import DocumentDraft
from app.core import context_token
from app.core.config import Settings
from app.core.context import Caller
from app.core.errors import UnavailableError
from app.llm.embeddings import FakeEmbedder
from app.llm.fake import FakeLLM
from app.mcp_server.server import McpState, build_server, create_mcp_app
from app.rag.ingest import ingest
from app.store import documents
from app.tools.client import ToolRefused, call_over_mcp, use_tool
from app.tools.context import ToolContext
from app.tools.registry import TOOLS, call_tool
from tests.unit.analytics_fakes import caller
from tests.unit.rag_fakes import FakeMongo, FakeQdrant

KEY = "k" * 40
OTHER = "99999999-9999-9999-9999-999999999999"


class Running:
    def __init__(self, url: str, settings: Settings, qdrant: FakeQdrant, embedder: FakeEmbedder):
        self.url, self.settings, self.qdrant, self.embedder = url, settings, qdrant, embedder
        self.health = url.rsplit("/", 1)[0] + "/healthz"

    def ctx(self, who: Caller, **settings: Any) -> ToolContext:
        return ToolContext(
            caller=who, pg=None, settings=self.settings.model_copy(update=settings),  # type: ignore[arg-type]
            qdrant=self.qdrant, embedder=self.embedder,  # type: ignore[arg-type]
        )  # fmt: skip


@pytest.fixture(scope="module")
def server() -> Iterator[Running]:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    settings = Settings(
        _env_file=None, env="test", secret_key=KEY, rag_min_score=0.05,
        tools_transport="mcp", mcp_url=f"http://127.0.0.1:{port}/mcp",
    )  # fmt: skip
    qdrant, embedder = FakeQdrant(), FakeEmbedder()
    state = McpState(settings=settings, pg=None, qdrant=qdrant, embedder=embedder)  # type: ignore[arg-type]
    config = uvicorn.Config(
        create_mcp_app(state), host="127.0.0.1", port=port, log_config=None, access_log=False
    )
    running = uvicorn.Server(config)
    thread = threading.Thread(target=running.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 15
    while not running.started:
        assert time.monotonic() < deadline, "the MCP server did not start"
        time.sleep(0.02)
    yield Running(settings.mcp_url, settings, qdrant, embedder)
    running.should_exit = True
    thread.join(timeout=10)


def token_for(who: Caller, key: str = KEY, **kw: Any) -> str:
    return context_token.make(who, key, **kw)


async def connect(url: str, token: str | None) -> Any:
    headers = {"Authorization": f"Bearer {token}"} if token is not None else {}
    http = httpx2.AsyncClient(headers=headers, timeout=20)
    return http, Client(streamable_http_client(url, http_client=http), read_timeout_seconds=20)


# -- start-up, health, discovery ----------------------------------------------------------


def test_the_server_starts_and_its_health_route_needs_no_token(server: Running) -> None:
    response = httpx.get(server.health, timeout=5)
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "tools": len(TOOLS)}


async def test_a_client_discovers_every_tool_with_its_schema_and_description(
    server: Running,
) -> None:
    http, client = await connect(server.url, token_for(caller("analyst")))
    async with http, client:
        listed = (await client.list_tools()).tools
    assert {tool.name for tool in listed} == set(TOOLS)
    by_name = {tool.name: tool for tool in listed}
    top = by_name["top_products"]
    assert top.description == TOOLS["top_products"].description
    assert top.input_schema["properties"]["by"]["enum"] == ["units", "revenue"]
    assert top.input_schema["properties"]["limit"]["maximum"] == 50
    assert by_name["describe_table"].input_schema["required"] == ["table"]
    for tool in listed:
        assert tool.annotations is not None and tool.annotations.read_only_hint is True
        for argument in tool.input_schema.get("properties", {}):
            assert not any(w in argument for w in ("tenant", "company", "user", "outlet"))


async def test_the_tools_are_the_registrys_and_nothing_else() -> None:
    server = build_server(McpState(settings=Settings(_env_file=None, env="test"), pg=None))  # type: ignore[arg-type]
    assert sorted(tool.name for tool in await server.list_tools()) == sorted(TOOLS)


# -- calling ------------------------------------------------------------------------------


async def test_a_tool_called_over_mcp_gives_the_same_result_as_in_process(server: Running) -> None:
    who = caller("analyst")
    for name, arguments in (
        ("list_tables", {}),
        ("describe_table", {"table": "orders"}),
        ("get_metric_definitions", {"names": ["revenue", "units sold"]}),
    ):
        over = await call_over_mcp(server.ctx(who), name, arguments)
        direct = await call_tool(server.ctx(who), name, arguments)
        assert over == direct and over.row_count > 0, name


async def test_the_transport_setting_chooses_the_path(server: Running) -> None:
    who = caller("analyst")
    assert (await use_tool(server.ctx(who), "list_tables", {})).row_count > 0  # over MCP
    # direct needs no server: point the address at a closed port and it still answers
    off = server.ctx(who, tools_transport="direct", mcp_url="http://127.0.0.1:9/mcp")
    assert (await use_tool(off, "list_tables", {})).row_count > 0
    down = server.ctx(who, mcp_url="http://127.0.0.1:9/mcp")
    with pytest.raises(UnavailableError, match="tool server is not available"):
        await use_tool(down, "list_tables", {})


@pytest.mark.parametrize(
    ("name", "arguments", "code"),
    [
        ("describe_table", {"table": "pg_shadow"}, "not_found"),
        ("sales_summary", {"period": "never"}, None),  # refused by the SDK's own validation
        ("top_products", {"limit": 5000}, None),
    ],
)
async def test_an_invalid_call_is_an_error_result_not_a_crash(
    server: Running, name: str, arguments: dict[str, Any], code: str | None
) -> None:
    with pytest.raises(ToolRefused) as refused:
        await call_over_mcp(server.ctx(caller("analyst")), name, arguments)
    if code:
        assert refused.value.code == code
    # and the server is still answering
    assert (await call_over_mcp(server.ctx(caller("analyst")), "list_tables", {})).row_count > 0


async def test_a_tool_the_caller_may_not_use_is_refused_over_mcp(server: Running) -> None:
    # a role with no right to ask at all is refused for every tool
    for name, arguments in (("list_tables", {}), ("run_query", {"sql": "SELECT 1"})):
        with pytest.raises(ToolRefused) as refused:
            await call_over_mcp(server.ctx(replace(caller(), role="guest")), name, arguments)
        assert refused.value.code == "permission_denied"


# -- the token: nothing else is trusted ----------------------------------------------------


def post(server: Running, token: str | None, **headers: str) -> httpx.Response:
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
    auth = {"Authorization": f"Bearer {token}"} if token is not None else {}
    return httpx.post(
        server.url, json=body, timeout=10,
        headers={"Accept": "application/json, text/event-stream", **auth, **headers},
    )  # fmt: skip


def test_no_token_a_forged_token_an_expired_token_and_a_wrong_key_are_all_refused(
    server: Running,
) -> None:
    who = caller("admin")
    good = token_for(who)
    body, signature = good.split(".")
    refused = {
        "no token": None,
        "forged signature": f"{body}.{'A' * len(signature)}",
        "payload of another token": f"{token_for(replace(who, role='viewer')).split('.')[0]}.{signature}",  # noqa: E501
        "expired": token_for(who, now=time.time() - 3600),
        "signed with another key": token_for(who, key="z" * 40),
        "not a token": "Bearer",
    }
    for why, token in refused.items():
        response = post(server, token)
        assert response.status_code == 401, why
        assert response.json()["error"]["code"] == "not_authenticated", why
        assert "tools" not in response.text
    # headers that claim an identity mean nothing
    spoofed = post(server, None, **{"X-Dev-User": "admin@brewbite.example", "X-Tenant-Id": OTHER})
    assert spoofed.status_code == 401
    # a scheme other than Bearer is not a token
    assert (
        httpx.post(server.url, json={}, headers={"Authorization": f"Basic {good}"}).status_code
        == 401
    )  # noqa: E501
    assert post(server, good).status_code == 200  # the control: the good token passes


async def test_a_client_without_a_token_cannot_even_list_the_tools(server: Running) -> None:
    http, client = await connect(server.url, None)
    with pytest.raises(BaseException):  # noqa: B017, PT011 - the SDK raises its own error group
        async with http, client:
            await client.list_tools()


# -- company isolation through MCP ---------------------------------------------------------


async def add_document(server: Running, tenant: str, text: bytes, title: str) -> None:
    mongo = FakeMongo()
    record = await documents.create(
        mongo,  # type: ignore[arg-type]
        tenant_id=tenant, document_id=documents.new_id(), uploaded_by="u", filename=f"{title}.txt",
        title=title, kind="txt", size=len(text), sha256=title, storage_path="p",
    )  # fmt: skip
    await ingest(record, text, mongo=mongo, qdrant=server.qdrant, embedder=server.embedder)  # type: ignore[arg-type]


async def test_the_company_in_the_token_decides_which_documents_are_searched(
    server: Running,
) -> None:
    mine, theirs = (
        caller("viewer"),
        replace(caller("viewer"), tenant_id=__import__("uuid").UUID(OTHER)),
    )
    await add_document(server, str(mine.tenant_id), b"Our refund window is 14 days.", "ours")
    await add_document(server, OTHER, b"Our refund window is 90 days.", "theirs")

    async def texts(who: Caller) -> list[str]:
        found = await call_over_mcp(server.ctx(who), "search_documents", {"query": "refund window"})
        return [row["text"] for row in found.rows]

    assert await texts(mine) == ["Our refund window is 14 days."]
    assert await texts(theirs) == ["Our refund window is 90 days."]
    assert set(server.qdrant.searches) == {str(mine.tenant_id), OTHER}


async def test_the_document_agent_works_through_mcp(server: Running) -> None:
    who = caller("viewer")
    if not server.qdrant.of(str(who.tenant_id)):
        await add_document(server, str(who.tenant_id), b"Our refund window is 14 days.", "ours")
    llm = FakeLLM().on("document_answer", DocumentDraft(found=True, answer="14 days [1]."))
    before = len(server.qdrant.searches)
    answer = await document_agent.answer("refund window?", ctx=server.ctx(who), llm=llm)
    assert (answer.status, answer.summary) == ("answered", "14 days [1].")
    assert [s.title for s in answer.sources] == ["ours"]
    assert len(server.qdrant.searches) == before + 1  # the search ran in the server process
    assert "90 days" not in llm.calls[0].text


async def test_calls_at_the_same_time_keep_their_own_callers(server: Running) -> None:
    mine, theirs = (
        caller("viewer"),
        replace(caller("viewer"), tenant_id=__import__("uuid").UUID(OTHER)),
    )
    if not server.qdrant.of(OTHER):
        await add_document(server, str(mine.tenant_id), b"Our refund window is 14 days.", "ours")
        await add_document(server, OTHER, b"Our refund window is 90 days.", "theirs")
    results = await asyncio.gather(
        *(
            call_over_mcp(server.ctx(who), "search_documents", {"query": "refund window"})
            for who in (mine, theirs) * 5
        )
    )
    for index, found in enumerate(results):
        want = "14 days" if index % 2 == 0 else "90 days"
        assert [want in row["text"] for row in found.rows] == [True]
