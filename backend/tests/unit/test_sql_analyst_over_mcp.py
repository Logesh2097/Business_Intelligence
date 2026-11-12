"""MOCKED: the SQL analyst when queries go through the run_query tool
(`BI_TOOLS_TRANSPORT=mcp`). The tool call is replaced; what is tested is what the
analyst sends and what it does with each kind of answer. The real path through a real
MCP server is tests/evals/test_golden_mcp.py.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from app.agents import sql_analyst
from app.agents.sql_analyst import SqlDraft, SqlOutcome, answer_with_sql
from app.core.config import Settings
from app.llm.fake import FakeLLM
from app.semantic.model import load_vocabulary
from app.tools import registry
from app.tools.context import ToolContext
from app.tools.registry import ToolResult, call_tool
from tests.unit.analytics_fakes import FakeDatabase, caller, result

MCP = Settings(_env_file=None, env="test", tools_transport="mcp")
DIRECT = Settings(_env_file=None, env="test")
QUESTION = "What was revenue?"
GOOD = "SELECT SUM(total_amount - tax_amount) AS revenue FROM orders WHERE status = 'completed'"
SALES = frozenset({"orders", "order_items", "products", "categories", "outlets"})


def tool_answer(status: str = "ok", **more: Any) -> ToolResult:
    base: dict[str, Any] = {"tool": "run_query", "title": "Query", "status": status}
    if status == "ok":
        base |= {"columns": [{"name": "revenue", "type": "money"}], "rows": [{"revenue": 5.0}],
                 "row_count": 1, "sql": GOOD + " LIMIT 1001"}  # fmt: skip
    return ToolResult(**(base | more))


class Tools:
    def __init__(self, *answers: ToolResult) -> None:
        self.answers = list(answers)
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def __call__(self, ctx: ToolContext, name: str, arguments: dict[str, Any]) -> ToolResult:
        self.calls.append((name, arguments))
        return self.answers.pop(0)


async def run(
    llm: FakeLLM, settings: Settings = MCP, tables: frozenset[str] | None = None
) -> SqlOutcome:
    vocabulary = load_vocabulary()
    return await answer_with_sql(
        question=QUESTION, caller=caller(), llm=llm, pg=None,  # type: ignore[arg-type]
        vocabulary=vocabulary.restricted_to(tables) if tables else vocabulary,
        today=date(2026, 11, 1), max_rows=1000,
        tools=ToolContext(caller=caller(), pg=None, settings=settings),  # type: ignore[arg-type]
    )  # fmt: skip


def draft(sql: str = GOOD) -> SqlDraft:
    return SqlDraft(sql=sql, explanation="e", assumptions=["Completed orders only."])


async def test_over_mcp_the_query_goes_to_the_tool_and_nothing_runs_here(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = FakeDatabase().install(monkeypatch)  # any local run or log would be recorded here
    tools = Tools(tool_answer(notes=["A note from the correctness check."]))
    monkeypatch.setattr(sql_analyst, "use_tool", tools)
    outcome = await run(FakeLLM().on("sql", draft()), tables=SALES)

    assert outcome.status == "answered" and outcome.result is not None
    assert outcome.result.rows == [{"revenue": 5.0}]
    assert outcome.sql == GOOD + " LIMIT 1001"  # the query as the server checked it
    assert outcome.assumptions == ["Completed orders only.", "A note from the correctness check."]
    ((name, arguments),) = tools.calls
    assert name == "run_query" and arguments["sql"] == GOOD and arguments["asked"] == QUESTION
    assert set(arguments["only_tables"]) == SALES  # the agent's own tables, and only those
    assert db.executed == [] and db.logged == []


async def test_a_refusal_from_the_tool_goes_back_to_the_model_like_a_local_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tools = Tools(
        tool_answer("refused", code="missing_status_filter", reason="State the order status."),
        tool_answer("error", code="42883", reason="function x() does not exist"),
        tool_answer(),
    )
    monkeypatch.setattr(sql_analyst, "use_tool", tools)
    llm = FakeLLM().on("sql", draft("SELECT SUM(total_amount) AS t FROM orders"))
    llm.on("sql_repair", draft("SELECT x() AS t FROM orders"), draft())
    outcome = await run(llm)
    assert (outcome.status, outcome.attempts) == ("answered", 3)
    repairs = [c.text for c in llm.calls_for("sql_repair")]
    assert "State the order status." in repairs[0]
    assert "PostgreSQL said: function x() does not exist" in repairs[1]


async def test_three_refusals_end_in_failed_and_a_timeout_is_not_retried(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    refused = tool_answer("refused", code="unsafe_aggregation", reason="A join repeats rows.")
    monkeypatch.setattr(sql_analyst, "use_tool", Tools(refused, refused, refused))
    llm = FakeLLM().on("sql", draft()).on("sql_repair", draft(), draft())
    outcome = await run(llm)
    assert (outcome.status, outcome.attempts, outcome.result) == ("failed", 3, None)

    tools = Tools(tool_answer("timeout", code="timeout", reason="too long"))
    monkeypatch.setattr(sql_analyst, "use_tool", tools)
    timed = await run(FakeLLM().on("sql", draft()))
    assert (timed.status, timed.attempts) == ("timeout", 1) and len(tools.calls) == 1


async def test_with_the_direct_transport_the_tool_is_never_called(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = FakeDatabase(result([{"revenue": 5.0}])).install(monkeypatch)
    tools = Tools()
    monkeypatch.setattr(sql_analyst, "use_tool", tools)
    outcome = await run(FakeLLM().on("sql", draft()), settings=DIRECT)
    assert outcome.status == "answered" and tools.calls == [] and db.statuses == ["ok"]


# -- the tool's side: the table restriction can only narrow ---------------------------------


def ctx() -> ToolContext:
    return ToolContext(caller=caller("analyst"), pg=None, settings=DIRECT)  # type: ignore[arg-type]


async def test_only_tables_narrows_what_a_query_may_read_and_never_widens_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = FakeDatabase(result([{"n": 1}]), result([{"n": 1}])).install(monkeypatch)
    stock = "SELECT COUNT(*) AS n FROM inventory_levels"
    # allowed without a restriction, refused when the caller narrows to the sales tables
    assert (await call_tool(ctx(), "run_query", {"sql": stock})).status == "ok"
    narrowed = await call_tool(ctx(), "run_query", {"sql": stock, "only_tables": sorted(SALES)})
    assert narrowed.status == "refused" and narrowed.rows == []
    # naming a table that is not in the vocabulary adds nothing
    widened = await call_tool(
        ctx(), "run_query",
        {"sql": "SELECT COUNT(*) AS n FROM app.users",
         "only_tables": ["orders", "users", "app.users"]},
    )  # fmt: skip
    assert widened.status == "refused"
    with pytest.raises(registry.BadRequestError):
        await call_tool(ctx(), "run_query", {"sql": stock, "only_tables": ["pg_shadow"]})
    assert db.executed == [db.executed[0]]  # only the first query ever ran


async def test_the_question_is_what_the_log_holds(monkeypatch: pytest.MonkeyPatch) -> None:
    db = FakeDatabase(result([{"n": 1}]), result([{"n": 1}])).install(monkeypatch)
    sql = "SELECT COUNT(*) AS n FROM outlets"
    answered = await call_tool(ctx(), "run_query", {"sql": sql, "asked": "How many outlets?"})
    await call_tool(ctx(), "run_query", {"sql": sql})
    assert [entry["question"] for entry in db.logged] == ["How many outlets?", "[tool] run_query"]
    assert answered.sql is not None and "outlets" in answered.sql


async def test_the_pipeline_may_run_a_query_for_a_viewer_but_a_viewer_may_not_type_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = FakeDatabase(result([{"n": 1}])).install(monkeypatch)
    sql = "SELECT COUNT(*) AS n FROM outlets"
    viewer = caller("viewer", limited=True)
    typed = ToolContext(caller=viewer, pg=None, settings=DIRECT)  # type: ignore[arg-type]
    with pytest.raises(registry.PermissionDeniedError):
        await call_tool(typed, "run_query", {"sql": sql})
    piped = ToolContext(caller=viewer, pg=None, settings=DIRECT, pipeline=True)  # type: ignore[arg-type]
    assert (await call_tool(piped, "run_query", {"sql": sql})).status == "ok"
    assert db.callers == [viewer]  # run as the viewer: row security decides what is seen
