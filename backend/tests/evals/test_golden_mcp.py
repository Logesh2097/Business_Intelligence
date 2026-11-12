"""LIVE, NO MODEL: the golden set with every query going through a real MCP server.

`python -m app.mcp_server` is started on bi_eval. Each of the 58 questions then runs
through the application with `BI_TOOLS_TRANSPORT=mcp` and the scripted model: the SQL
analyst (in every domain agent and in the general route) sends its query to the
`run_query` tool over MCP, with the caller in a signed context token. The verdict is
the same comparison with the stored expected rows as in test_golden_dataset.py.

This is gate 8, item 1 of docs/plan.md: the same results through MCP as direct.
Part of `make eval-check`. It never calls a model.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import quote

import httpx
import pytest
from psycopg.conninfo import conninfo_to_dict

from app.core.config import Settings
from app.store.connections import Stores
from tests.evals import database
from tests.evals.dataset import Case, load_dataset
from tests.evals.runner import Outcome, run_case, scripted_llm

DATASET = load_dataset()
KEY = "golden-mcp-context-key-" + "k" * 24
THROUGH_MCP: list[Outcome] = []


def _url(dsn: str) -> str:
    info = conninfo_to_dict(dsn)
    login = quote(str(info["user"]), safe="")
    if info.get("password"):
        login += ":" + quote(str(info["password"]), safe="")
    host, port = info.get("host", "localhost"), info.get("port", 5432)
    return f"postgresql://{login}@{host}:{port}/{info['dbname']}"


@pytest.fixture(scope="module")
def mcp_settings(eval_settings: Settings) -> Iterator[Settings]:
    assert database.database_of(eval_settings.pg_dsn) == database.EVAL_DATABASE
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    environment = {
        **os.environ,
        "BI_ENV": "test",
        "BI_PG_DSN": _url(eval_settings.pg_dsn),
        "BI_PG_READONLY_DSN": _url(eval_settings.pg_readonly_dsn),
        "BI_PG_OWNER_DSN": "",
        "BI_SECRET_KEY": KEY,
        "BI_MCP_HOST": "127.0.0.1",
        "BI_MCP_PORT": str(port),
        "BI_FAKE_TODAY": str(database.REFERENCE_DATE),
        "BI_OPENAI_API_KEY": "",  # this process has no model
        "BI_EVAL_CONFIRM_REAL_MODEL": "",
    }
    process = subprocess.Popen(  # noqa: S603 - our own interpreter and module
        [sys.executable, "-m", "app.mcp_server"],
        cwd=Path(__file__).resolve().parents[2],
        env=environment,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    try:
        deadline = time.monotonic() + 30
        while True:
            try:
                if httpx.get(f"http://127.0.0.1:{port}/healthz", timeout=1).status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            if process.poll() is not None or time.monotonic() > deadline:
                detail = (process.stderr.read() if process.stderr else b"")[-400:]
                pytest.fail(f"the MCP server did not start: {detail!r}")
            time.sleep(0.1)
        yield eval_settings.model_copy(
            update={"secret_key": KEY, "tools_transport": "mcp",
                    "mcp_url": f"http://127.0.0.1:{port}/mcp"}
        )  # fmt: skip
    finally:
        process.terminate()
        process.wait(timeout=10)


@pytest.mark.parametrize("case", DATASET.active, ids=[c.id for c in DATASET.active])
async def test_every_active_question_passes_with_its_queries_run_through_mcp(
    stores: Stores, mcp_settings: Settings, case: Case
) -> None:
    outcome = await run_case(
        case, llm=scripted_llm(case), stores=stores, settings=mcp_settings, check_text=False
    )
    THROUGH_MCP.append(outcome)
    assert outcome.passed, f"{outcome.cause}: {outcome.details}"
    assert outcome.route == case.route.expect and set(outcome.agents) == set(case.route.agents)


def test_all_58_ran_through_mcp(capsys: pytest.CaptureFixture[str]) -> None:
    assert len(THROUGH_MCP) == len(DATASET.active) == 58
    assert all(outcome.passed for outcome in THROUGH_MCP)
    with capsys.disabled():
        print("\nthrough the MCP server: dataset 58 | executed 58 | passed 58 | failed 0")


async def test_without_the_right_key_a_question_fails_and_returns_no_rows(
    stores: Stores, mcp_settings: Settings
) -> None:
    case = next(c for c in DATASET.active if c.id.startswith("A1-"))
    wrong_key = mcp_settings.model_copy(update={"secret_key": "another-key-" + "z" * 30})
    outcome = await run_case(
        case, llm=scripted_llm(case), stores=stores, settings=wrong_key, check_text=False
    )
    assert not outcome.passed and outcome.cause == "execution"
    assert outcome.answer is not None and all(not part.data for part in outcome.answer.results)
