"""MOCKED: the three domain agents with a scripted model and a fake database.

The guard, the vocabulary and the prompts are real. Nothing here needs a server or a
model key. Live equivalent: tests/integration/test_agents_live.py.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.agents import domain
from app.agents.domain import AgentAnswer, AgentReport, DomainAgent
from app.agents.registry import AGENTS
from app.agents.sql_analyst import SqlDraft
from app.api.deps import get_caller
from app.core.config import Settings
from app.core.context import Caller
from app.llm.base import LLMInvalidResponseError, LLMUnavailableError
from app.llm.fake import FakeLLM
from app.main import create_app
from app.semantic.model import load_vocabulary
from app.store import analytics
from app.store.analytics import QueryFailedError, QueryTimeoutError
from tests.unit.analytics_fakes import TENANT, FakeDatabase, caller, result

SALES, INVENTORY, CUSTOMER = (
    AGENTS[n] for n in ("sales_agent", "inventory_agent", "customer_agent")
)
SETTINGS = Settings(_env_file=None, env="test")

# one question, the SQL a model would write for it and a result, per agent
CASES: dict[str, tuple[str, str, list[dict[str, Any]]]] = {
    "sales_agent": (
        "Which outlet performed best this month?",
        "SELECT t.name AS outlet, ROUND(SUM(o.total_amount - o.tax_amount), 2) AS revenue "
        "FROM orders o JOIN outlets t ON t.id = o.outlet_id WHERE o.status = 'completed' "
        "GROUP BY t.name ORDER BY revenue DESC",
        [{"outlet": "Indiranagar", "revenue": 270541.0}, {"outlet": "Adyar", "revenue": 98969.5}],
    ),
    "inventory_agent": (
        "Which products are running low on stock?",
        "SELECT t.name AS outlet, p.name AS product, l.quantity_on_hand AS on_hand, "
        "l.reorder_level AS reorder_level FROM inventory_levels l "
        "JOIN products p ON p.id = l.product_id JOIN outlets t ON t.id = l.outlet_id "
        "WHERE l.quantity_on_hand <= l.reorder_level AND l.quantity_on_hand > 0",
        [{"outlet": "Indiranagar", "product": "Latte", "on_hand": 4.0, "reorder_level": 20.0}],
    ),
    "customer_agent": (
        "How many customers visited this month?",
        "SELECT COUNT(DISTINCT v.customer_id) AS customers FROM customer_visits v "
        "WHERE v.visit_date BETWEEN DATE '2026-11-01' AND DATE '2026-11-10'",
        [{"customers": 216}],
    ),
}


def draft(sql: str | None, explanation: str = "", assumptions: list[str] | None = None) -> SqlDraft:
    return SqlDraft(sql=sql, explanation=explanation, assumptions=assumptions or [])


def report(summary: str, *insights: str) -> AgentReport:
    return AgentReport(summary=summary, insights=list(insights))


async def ask(
    agent: DomainAgent, llm: FakeLLM, question: str | None = None, **kw: Any
) -> AgentAnswer:
    return await domain.run(
        agent,
        question=question or CASES[agent.name][0],
        caller=kw.get("who", caller()),
        llm=llm,
        pg=None,  # type: ignore[arg-type]  # the database functions are replaced
        settings=kw.get("settings", SETTINGS),
    )


# -- what an agent is ------------------------------------------------------------------


def test_there_are_three_agents_each_with_its_own_prompt_and_tables() -> None:
    assert list(AGENTS) == ["sales_agent", "inventory_agent", "customer_agent"]
    known = {t.table for t in load_vocabulary().tables}
    prompts = set()
    for agent in AGENTS.values():
        assert agent.tables <= known
        assert len(agent.responsibilities) == 6 and len(agent.examples) == 4
        assert agent.prompt.startswith(f"You are the {agent.title}.")
        prompts.add(agent.prompt)
    assert len(prompts) == 3


def test_each_agent_is_limited_to_its_own_tables() -> None:
    assert "inventory_levels" not in SALES.tables and "customer_visits" not in SALES.tables
    assert "customer_visits" not in INVENTORY.tables and "customers" not in INVENTORY.tables
    assert "inventory_levels" not in CUSTOMER.tables and "products" not in CUSTOMER.tables
    # purchasing and expenses belong to no agent of this phase
    for agent in AGENTS.values():
        assert not agent.tables & {"suppliers", "purchase_orders", "expenses"}


def test_a_restricted_vocabulary_keeps_only_joins_and_metrics_it_can_use() -> None:
    vocabulary = load_vocabulary().restricted_to(CUSTOMER.tables)
    assert {t.table for t in vocabulary.tables} == CUSTOMER.tables
    for table in vocabulary.tables:
        assert all(join.table in CUSTOMER.tables for join in table.joins)
    names = {m.metric for m in vocabulary.metrics}
    assert "visits" in names and "revenue" in names
    assert "units_sold" not in names and "stock_value" not in names


# -- the structured answer -------------------------------------------------------------


@pytest.mark.parametrize("name", list(AGENTS))
async def test_each_agent_answers_with_the_same_structure(
    name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    question, sql, rows = CASES[name]
    db = FakeDatabase(result(rows)).install(monkeypatch)
    llm = FakeLLM().on("sql", draft(sql, assumptions=["All dates."]))
    llm.on("agent_report", report("Here is the answer.", "The first row stands out."))

    answer = await ask(AGENTS[name], llm)

    assert answer.agent == name and answer.question == question
    assert answer.status == "answered"
    assert answer.summary == "Here is the answer."
    assert answer.insights == ["The first row stands out."]
    assert answer.data == rows and answer.row_count == len(rows) and not answer.truncated
    assert [c["name"] for c in answer.columns] == list(rows[0])
    assert answer.assumptions == ["All dates."] and answer.scope_note is None
    assert answer.sql == db.executed[0] and "LIMIT" in answer.sql
    assert db.statuses == ["ok"]  # one query, logged once
    assert [c.task for c in llm.calls] == ["sql", "agent_report"]


# -- the prompts -----------------------------------------------------------------------


@pytest.mark.parametrize("name", list(AGENTS))
async def test_the_model_gets_the_shared_sql_rules_and_this_agents_own_prompt(
    name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    agent = AGENTS[name]
    FakeDatabase(result(CASES[name][2])).install(monkeypatch)
    llm = FakeLLM().on("sql", draft(CASES[name][1])).on("agent_report", report("Done."))
    await ask(agent, llm)

    sql_call, report_call = llm.calls
    assert "Write exactly one SELECT statement" in sql_call.system
    assert agent.prompt in sql_call.system and agent.prompt in report_call.system
    assert "Use only the numbers that appear in RESULT" in report_call.system
    for other in AGENTS.values():
        if other is not agent:
            assert other.prompt not in sql_call.system
    assert not sql_call.fast and report_call.fast  # the cheaper model writes the text


@pytest.mark.parametrize(
    ("name", "question"),
    [
        ("sales_agent", "Revenue, stock on hand, customer visits and expenses by outlet"),
        ("sales_agent", "Which outlet performed best this month?"),  # names no sales word
        ("inventory_agent", "How are we doing?"),
        ("customer_agent", "How many customers returned this month?"),
    ],
)
async def test_an_agent_is_shown_all_of_its_own_tables_and_no_other(
    name: str, question: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    agent = AGENTS[name]
    FakeDatabase(result([{"outlets": 1}])).install(monkeypatch)
    llm = FakeLLM().on("sql", draft("SELECT COUNT(*) AS outlets FROM outlets"))
    llm.on("agent_report", report("Done."))
    await ask(agent, llm, question)

    prompt = llm.calls[0].user
    shown = {
        line.split()[1].rstrip(":") for line in prompt.splitlines() if line.startswith("TABLE ")
    }
    assert shown == agent.tables
    for hidden in {t.table for t in load_vocabulary().tables} - agent.tables:
        assert f"TABLE {hidden}" not in prompt and f"= {hidden}." not in prompt


async def test_nothing_about_who_is_asking_reaches_the_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    FakeDatabase(result(CASES["sales_agent"][2])).install(monkeypatch)
    llm = FakeLLM().on("sql", draft(CASES["sales_agent"][1])).on("agent_report", report("Done."))
    who = caller("viewer", limited=True)
    await ask(SALES, llm, who=who)
    for call in llm.calls:
        assert str(TENANT) not in call.text and "tenant_id" not in call.text
        assert str(who.user_id) not in call.text and str(who.outlet_ids[0]) not in call.text


# -- SQL validation: an agent cannot reach outside its area ------------------------------


@pytest.mark.parametrize(
    ("name", "outside"),
    [
        ("sales_agent", "SELECT SUM(l.quantity_on_hand) AS stock FROM inventory_levels l"),
        ("sales_agent", "SELECT COUNT(*) AS visits FROM customer_visits v"),
        ("inventory_agent", "SELECT COUNT(*) AS visits FROM customer_visits v"),
        ("inventory_agent", "SELECT SUM(e.amount) AS spend FROM expenses e"),
        ("customer_agent", "SELECT SUM(l.quantity_on_hand) AS stock FROM inventory_levels l"),
        ("customer_agent", "SELECT p.name AS product FROM products p"),
    ],
)
async def test_sql_on_a_table_outside_the_agents_list_is_refused_and_never_runs(
    name: str, outside: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = FakeDatabase().install(monkeypatch)
    llm = FakeLLM().on("sql", draft(outside)).on("sql_repair", draft(outside), draft(outside))

    answer = await ask(AGENTS[name], llm)

    assert answer.status == "failed" and answer.sql is None and answer.data == []
    assert db.executed == []
    assert db.statuses == ["rejected"] * 3
    assert {entry["reason"] for entry in db.logged} == {"table_not_allowed"}
    assert answer.summary.startswith("I could not build a safe query")


@pytest.mark.parametrize(
    "unsafe",
    [
        "DELETE FROM orders",
        "SELECT 1 AS one; DROP TABLE orders",
        "SELECT * FROM orders",
        "SELECT u.email AS email FROM app.users u",
        "SELECT set_config('app.tenant_id', 'x', false) AS s",
        "SELECT c.phone AS phone FROM customers c",
    ],
)
@pytest.mark.parametrize("name", list(AGENTS))
async def test_unsafe_sql_from_any_agent_is_refused_by_the_same_guard(
    name: str, unsafe: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    question, sql, rows = CASES[name]
    db = FakeDatabase(result(rows)).install(monkeypatch)
    llm = FakeLLM().on("sql", draft(unsafe)).on("sql_repair", draft(sql))
    llm.on("agent_report", report("Done."))

    answer = await ask(AGENTS[name], llm)

    assert answer.status == "answered"
    assert db.statuses == ["rejected", "ok"] and len(db.executed) == 1
    assert "PREVIOUS ATTEMPT FAILED" in llm.calls_for("sql_repair")[0].user


async def test_a_question_outside_the_area_is_declined_without_a_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = FakeDatabase().install(monkeypatch)
    llm = FakeLLM().on("sql", draft(None, "Stock levels belong to the Inventory agent."))
    answer = await ask(SALES, llm, "Which products are out of stock?")
    assert answer.status == "unanswerable"
    assert answer.summary == "Stock levels belong to the Inventory agent."
    assert answer.insights == [] and answer.sql is None
    assert db.executed == [] and db.logged == []


# -- numbers by code, words by the model -------------------------------------------------


async def test_an_insight_with_a_figure_that_is_not_in_the_result_is_dropped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    question, sql, rows = CASES["sales_agent"]
    FakeDatabase(result(rows)).install(monkeypatch)
    llm = FakeLLM().on("sql", draft(sql))
    llm.on(
        "agent_report",
        report(
            "Indiranagar led with INR 270,541.",
            "Adyar was lowest at INR 98,969.50.",
            "Indiranagar earned 173% more than Adyar.",  # computed by the model: not in RESULT
            "  ",
        ),
    )
    answer = await ask(SALES, llm)
    assert answer.summary == "Indiranagar led with INR 270,541."
    assert answer.insights == ["Adyar was lowest at INR 98,969.50."]


async def test_a_summary_with_an_invented_figure_is_asked_again_then_built_by_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    question, sql, rows = CASES["customer_agent"]
    FakeDatabase(result(rows)).install(monkeypatch)
    llm = FakeLLM().on("sql", draft(sql))
    llm.on("agent_report", report("About 500 customers visited."), report("Roughly 480 came."))

    answer = await ask(CUSTOMER, llm)

    assert answer.status == "answered"
    assert answer.summary == "Result: customers: 216."
    assert "500" not in answer.summary and "480" not in answer.summary
    second = llm.calls_for("agent_report")[1]
    assert "used numbers that are not in RESULT" in second.user


async def test_a_second_write_up_that_passes_is_used(monkeypatch: pytest.MonkeyPatch) -> None:
    question, sql, rows = CASES["customer_agent"]
    FakeDatabase(result(rows)).install(monkeypatch)
    llm = FakeLLM().on("sql", draft(sql))
    llm.on("agent_report", report("About 500 visited."), report("216 customers visited."))
    answer = await ask(CUSTOMER, llm)
    assert answer.summary == "216 customers visited."


async def test_at_most_four_insights_are_kept(monkeypatch: pytest.MonkeyPatch) -> None:
    question, sql, rows = CASES["sales_agent"]
    FakeDatabase(result(rows)).install(monkeypatch)
    llm = FakeLLM().on("sql", draft(sql))
    llm.on("agent_report", report("Done.", *[f"Point {letter}." for letter in "abcdef"]))
    answer = await ask(SALES, llm)
    assert answer.insights == ["Point a.", "Point b.", "Point c.", "Point d."]


async def test_no_rows_is_said_by_code_and_the_model_is_not_asked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    FakeDatabase(result([])).install(monkeypatch)
    llm = FakeLLM().on("sql", draft(CASES["inventory_agent"][1]))
    answer = await ask(INVENTORY, llm)
    assert answer.status == "no_data"
    assert answer.summary == "No data matched this question." and answer.insights == []
    assert llm.calls_for("agent_report") == []


async def test_a_limited_caller_is_told_the_scope_and_the_query_runs_as_them(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    question, sql, rows = CASES["sales_agent"]
    db = FakeDatabase(result(rows)).install(monkeypatch)
    llm = FakeLLM().on("sql", draft(sql)).on("agent_report", report("Indiranagar led."))
    who = caller("viewer", limited=True)
    answer = await ask(SALES, llm, who=who)
    assert db.callers == [who]
    assert answer.scope_note == "Limited to your outlets: Indiranagar."
    assert answer.summary == "Indiranagar led. Limited to your outlets: Indiranagar."


# -- failures --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "failure", [LLMUnavailableError("RateLimitError"), LLMInvalidResponseError("x")]
)
async def test_a_failed_write_up_keeps_the_data_and_falls_back_to_a_plain_sentence(
    failure: Exception, monkeypatch: pytest.MonkeyPatch
) -> None:
    question, sql, rows = CASES["customer_agent"]
    FakeDatabase(result(rows)).install(monkeypatch)
    llm = FakeLLM().on("sql", draft(sql)).on("agent_report", failure)
    answer = await ask(CUSTOMER, llm)
    assert answer.status == "answered" and answer.data == rows
    assert answer.summary == "Result: customers: 216." and answer.insights == []


async def test_a_model_outage_while_writing_sql_is_not_swallowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = FakeDatabase().install(monkeypatch)
    llm = FakeLLM().on("sql", LLMUnavailableError("APITimeoutError"))
    with pytest.raises(LLMUnavailableError):
        await ask(SALES, llm)
    assert db.executed == []


async def test_a_malformed_model_answer_is_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    question, sql, rows = CASES["inventory_agent"]
    FakeDatabase(result(rows)).install(monkeypatch)
    llm = FakeLLM().on("sql", LLMInvalidResponseError("sql")).on("sql_repair", draft(sql))
    llm.on("agent_report", report("One product is low."))
    answer = await ask(INVENTORY, llm)
    assert answer.status == "answered" and answer.summary == "One product is low."


async def test_a_database_error_goes_back_to_the_model_and_is_repaired(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    question, sql, rows = CASES["inventory_agent"]
    db = FakeDatabase(QueryFailedError("42803", "column must appear in GROUP BY"), result(rows))
    db.install(monkeypatch)
    llm = FakeLLM().on("sql", draft(sql)).on("sql_repair", draft(sql))
    llm.on("agent_report", report("One product is low."))
    answer = await ask(INVENTORY, llm)
    assert answer.status == "answered" and db.statuses == ["error", "ok"]
    assert "column must appear in GROUP BY" in llm.calls_for("sql_repair")[0].user
    assert "GROUP BY" not in answer.summary  # the database's words stay out of the answer


async def test_a_database_error_three_times_ends_in_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    question, sql, rows = CASES["sales_agent"]
    error = QueryFailedError("42803", "bad grouping")
    db = FakeDatabase(error, error, error).install(monkeypatch)
    llm = FakeLLM().on("sql", draft(sql)).on("sql_repair", draft(sql), draft(sql))
    answer = await ask(SALES, llm)
    assert answer.status == "failed" and answer.data == [] and answer.sql is None
    assert "bad grouping" not in answer.summary and db.statuses == ["error"] * 3


async def test_a_query_past_the_database_time_limit_is_reported_and_not_retried(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    question, sql, rows = CASES["sales_agent"]
    db = FakeDatabase(QueryTimeoutError()).install(monkeypatch)
    llm = FakeLLM().on("sql", draft(sql))
    answer = await ask(SALES, llm)
    assert answer.status == "timeout" and db.statuses == ["timeout"]
    assert answer.summary.startswith("That question needs more data")


async def test_an_agent_that_runs_past_its_own_time_limit_stops_and_says_so(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def slow(pg: Any, who: Caller, sql: str, *, max_rows: int) -> Any:
        await asyncio.sleep(30)

    FakeDatabase().install(monkeypatch)
    monkeypatch.setattr(analytics, "run_checked_sql", slow)
    llm = FakeLLM().on("sql", draft(CASES["sales_agent"][1]))
    settings = Settings(_env_file=None, env="test", agent_timeout_seconds=1)

    answer = await asyncio.wait_for(ask(SALES, llm, settings=settings), timeout=5)

    assert answer.agent == "sales_agent" and answer.status == "timeout"
    assert answer.summary == domain.TIMEOUT_MESSAGE
    assert answer.data == [] and answer.sql is None


# -- the API ---------------------------------------------------------------------------


class App:
    def __init__(self, client: TestClient) -> None:
        self.client = client
        self.llm = FakeLLM()
        client.app.state.llm = self.llm  # type: ignore[attr-defined]

    def sign_in(self, who: Caller) -> None:
        self.client.app.dependency_overrides[get_caller] = lambda: who  # type: ignore[attr-defined]

    def ask(self, agent: str, question: Any) -> Any:
        return self.client.post(f"/api/v1/agents/{agent}/query", json={"question": question})


@pytest.fixture
def app() -> Iterator[App]:
    with TestClient(create_app(SETTINGS)) as client:
        yield App(client)


def test_the_api_lists_the_agents_and_what_each_one_does(app: App) -> None:
    app.sign_in(caller("viewer"))
    body = app.client.get("/api/v1/agents").json()
    assert [a["agent"] for a in body] == ["sales_agent", "inventory_agent", "customer_agent"]
    assert set(body[0]) == {"agent", "title", "responsibilities", "example_questions"}
    assert "Identify low-stock products" in body[1]["responsibilities"]
    assert "tables" not in str(body)  # how an agent is limited is not the browser's business


def test_the_api_answers_in_the_agent_shape(app: App, monkeypatch: pytest.MonkeyPatch) -> None:
    question, sql, rows = CASES["sales_agent"]
    FakeDatabase(result(rows)).install(monkeypatch)
    app.sign_in(caller("analyst"))
    app.llm.on("sql", draft(sql)).on("agent_report", report("Indiranagar led.", "Adyar was last."))

    response = app.ask("sales_agent", question)

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {
        "agent", "question", "status", "summary", "insights", "sql", "columns", "data",
        "row_count", "truncated", "assumptions", "scope_note",
    }  # fmt: skip
    assert body["agent"] == "sales_agent" and body["status"] == "answered"
    assert body["insights"] == ["Adyar was last."] and body["data"] == rows
    assert body["sql"].startswith("SELECT t.name AS outlet")
    assert body["columns"][1] == {"name": "revenue", "type": "money"}


def test_a_viewer_gets_the_answer_but_never_the_sql(
    app: App, monkeypatch: pytest.MonkeyPatch
) -> None:
    question, sql, rows = CASES["customer_agent"]
    FakeDatabase(result(rows)).install(monkeypatch)
    app.sign_in(caller("viewer", limited=True))
    app.llm.on("sql", draft(sql)).on("agent_report", report("216 customers visited."))
    body = app.ask("customer_agent", question).json()
    assert body["sql"] is None and body["data"] == rows
    assert body["scope_note"] == "Limited to your outlets: Indiranagar."


def test_an_unknown_agent_is_not_found(app: App) -> None:
    app.sign_in(caller("analyst"))
    response = app.ask("finance_agent", "What did we spend?")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"
    assert app.llm.calls == []


@pytest.mark.parametrize("bad", ["", "  ", "hi", "x" * 2001, None, 42, ["a list"]])
def test_an_invalid_question_is_refused_before_any_model_call(app: App, bad: Any) -> None:
    app.sign_in(caller("analyst"))
    response = app.ask("sales_agent", bad)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_failed"
    assert app.llm.calls == []


def test_a_missing_question_field_is_refused(app: App) -> None:
    app.sign_in(caller("analyst"))
    response = app.client.post("/api/v1/agents/sales_agent/query", json={"q": "revenue?"})
    assert response.status_code == 422


def test_without_a_caller_both_routes_are_closed(app: App) -> None:
    assert app.client.get("/api/v1/agents").status_code == 401
    assert app.ask("sales_agent", "What was revenue?").status_code == 401


def test_a_role_without_the_permission_is_refused(app: App) -> None:
    app.sign_in(caller("super_admin"))  # the platform admin has no company data to ask about
    assert app.ask("sales_agent", "What was revenue?").status_code == 403
    assert app.client.get("/api/v1/agents").status_code == 403


@pytest.mark.parametrize(
    ("reason", "message"),
    [
        (
            "RateLimitError",
            "The assistant is busy right now: the model's request limit was reached. "
            "Please wait a minute and try again.",
        ),
        (
            "OpenAIRateLimitError",
            "The assistant is busy right now: the model's request limit was reached. "
            "Please wait a minute and try again.",
        ),
        ("APIConnectionError", "The assistant is not available right now. Please try again."),
        ("InternalServerError", "The assistant is not available right now. Please try again."),
        ("not configured", "The assistant is not set up yet: no model key is configured."),
    ],
)
def test_a_model_that_is_down_or_not_set_up_is_a_503(app: App, reason: str, message: str) -> None:
    app.sign_in(caller("analyst"))
    app.llm.on("sql", LLMUnavailableError(reason))
    response = app.ask("inventory_agent", "Which products are out of stock?")
    assert response.status_code == 503
    assert response.json()["error"]["message"] == message
    assert reason not in response.text or reason == "not configured"
