"""MOCKED: the assistant graph (supervisor -> agents | general -> aggregate) and its API,
with a scripted model and a fake database. The guard, the vocabulary, the prompts and
LangGraph itself are real. Live equivalent: tests/integration/test_assistant_live.py.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel

from app.agents import supervisor
from app.agents.domain import AgentReport
from app.agents.registry import AGENTS
from app.agents.sql_analyst import SqlDraft
from app.agents.summarizer import Summary
from app.agents.supervisor import AgentTask, Decision
from app.api.deps import get_caller
from app.core.config import Settings
from app.core.context import Caller
from app.core.errors import UnavailableError
from app.llm.base import LLMUnavailableError
from app.llm.fake import Call, FakeLLM
from app.main import create_app
from app.store import analytics
from app.store.analytics import QueryFailedError, QueryResult, QueryTimeoutError
from app.workflows import assistant
from app.workflows.assistant import AssistantAnswer, Combined
from tests.unit.analytics_fakes import TENANT, FakeDatabase, caller, result

SETTINGS = Settings(_env_file=None, env="test")

SQL = {
    # a sales figure must say which orders count (Phase 5.1, the correctness check)
    "sales_agent": "SELECT ROUND(SUM(o.total_amount - o.tax_amount), 2) AS revenue FROM orders o "
    "WHERE o.status = 'completed'",
    "inventory_agent": "SELECT COUNT(*) AS low_products FROM inventory_levels l "
    "WHERE l.quantity_on_hand <= l.reorder_level",
    "customer_agent": "SELECT COUNT(DISTINCT v.customer_id) AS customers FROM customer_visits v",
    "general": "SELECT ROUND(SUM(e.amount), 2) AS spend FROM expenses e",
}
ROWS: dict[str, list[dict[str, Any]]] = {
    "sales_agent": [{"revenue": 902538.0}],
    "inventory_agent": [{"low_products": 3}],
    "customer_agent": [{"customers": 216}],
    "general": [{"spend": 41250.0}],
}
SAID = {
    "sales_agent": "Revenue was INR 902,538.",
    "inventory_agent": "3 products are low on stock.",
    "customer_agent": "216 customers visited.",
    "general": "Spend was 41,250.",
}


def who_is(call: Call) -> str:
    """Which agent a model call belongs to, read from its own prompt."""
    for agent in AGENTS.values():
        if agent.prompt in call.system:
            return agent.name
    return "general"


def per_target(make: Callable[[str], BaseModel | BaseException]) -> Callable[[Call], BaseModel]:
    def answer(call: Call) -> BaseModel:
        made = make(who_is(call))
        if isinstance(made, BaseException):
            raise made
        return made

    return answer


class Db:
    """A fake database that answers by the table a query reads, so parallel agents can
    arrive in any order."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, **overrides: Any) -> None:
        self.answers: dict[str, Any] = {name: result(rows) for name, rows in ROWS.items()}
        self.answers.update(overrides)
        self.executed: list[str] = []
        self.callers: list[Caller] = []
        self.logged: list[dict[str, Any]] = []
        self.running = self.most_at_once = 0
        monkeypatch.setattr(analytics, "run_checked_sql", self.run)
        monkeypatch.setattr(analytics, "log_query", self.log)

    @staticmethod
    def target(sql: str) -> str:
        for name, marker in (
            ("inventory_agent", "inventory_levels"),
            ("customer_agent", "customer_visits"),
            ("general", "expenses"),
        ):
            if marker in sql:
                return name
        return "sales_agent"

    async def run(self, pg: Any, who: Caller, sql: str, *, max_rows: int) -> QueryResult:
        self.executed.append(sql)
        self.callers.append(who)
        self.running += 1
        self.most_at_once = max(self.most_at_once, self.running)
        try:
            await asyncio.sleep(0.02)  # long enough for parallel branches to overlap
            answer = self.answers[self.target(sql)]
            if isinstance(answer, BaseException):
                raise answer
            return answer  # type: ignore[no-any-return]
        finally:
            self.running -= 1

    async def log(self, pg: Any, who: Caller, **fields: Any) -> None:
        self.logged.append(fields)


def script(llm: FakeLLM, decision: Decision | BaseException, *, times: int = 3) -> FakeLLM:
    """A model that routes as told and then writes each target's usual SQL and text."""
    llm.on("route", decision)
    for _ in range(times):
        llm.on("sql", per_target(lambda name: SqlDraft(sql=SQL[name], explanation="x")))
        llm.on("agent_report", per_target(lambda name: AgentReport(summary=SAID[name])))
    llm.on("summary", Summary(text=SAID["general"]))
    return llm


def to_agents(*names: str, parts: dict[str, str] | None = None) -> Decision:
    parts = parts or {}
    return Decision(
        route="agents",
        agents=[AgentTask(agent=n, question=parts.get(n, "")) for n in names],
        reason="The question is about these areas.",
    )


async def ask(llm: FakeLLM, question: str, **kw: Any) -> AssistantAnswer:
    return await assistant.ask(
        question=question,
        caller=kw.get("who", caller()),
        llm=llm,
        pg=None,  # type: ignore[arg-type]  # the database functions are replaced
        settings=kw.get("settings", SETTINGS),
    )


# -- single-agent routing ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "question"),
    [
        ("sales_agent", "What was our total revenue last month?"),
        ("inventory_agent", "Which products are running low on stock?"),
        ("customer_agent", "How many customers visited this month?"),
    ],
)
async def test_a_question_of_one_area_reaches_only_that_agent(
    name: str, question: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = Db(monkeypatch)
    llm = script(FakeLLM(), to_agents(name, parts={name: "something the supervisor rewrote"}))

    answer = await ask(llm, question)

    assert (answer.status, answer.route) == ("answered", "agents")
    assert answer.reason == "The question is about these areas."
    assert [p.agent for p in answer.results] == [name]
    part = answer.results[0]
    assert part.question == question  # the user's words, not the supervisor's
    assert part.data == ROWS[name] and part.sql == db.executed[0]
    assert answer.summary == part.summary == SAID[name]  # passed through, not rewritten
    assert len(db.executed) == 1
    assert [c.task for c in llm.calls] == ["route", "sql", "agent_report"]
    assert f"<<<QUESTION\n{question}\nQUESTION>>>" in llm.calls[1].user
    assert who_is(llm.calls[1]) == name


# -- multi-agent routing -----------------------------------------------------------------


async def test_two_agents_run_at_the_same_time_and_their_results_are_combined(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = Db(monkeypatch)
    question = "What was our revenue and how many customers visited?"
    parts = {"sales_agent": "What was our revenue", "customer_agent": "how many customers visited?"}
    llm = script(FakeLLM(), to_agents("sales_agent", "customer_agent", parts=parts))
    llm.on("supervisor_report", Combined(text="Revenue was INR 902,538 and 216 customers visited."))

    answer = await ask(llm, question)

    assert answer.status == "answered" and answer.question == question
    assert [p.agent for p in answer.results] == ["sales_agent", "customer_agent"]
    assert [p.question for p in answer.results] == list(parts.values())
    assert [p.data for p in answer.results] == [ROWS["sales_agent"], ROWS["customer_agent"]]
    assert answer.summary == "Revenue was INR 902,538 and 216 customers visited."
    assert db.most_at_once == 2 and len(db.executed) == 2
    report = llm.calls_for("supervisor_report")[0]
    assert question in report.user and "902538.0" in report.user and "216" in report.user


async def test_three_agents_all_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    db = Db(monkeypatch)
    llm = script(FakeLLM(), to_agents("customer_agent", "sales_agent", "inventory_agent"))
    llm.on("supervisor_report", Combined(text="216 customers; revenue 902,538; 3 low products."))
    answer = await ask(llm, "Give me visits, revenue and low stock.")
    # in the order the supervisor chose, whatever order they finished in
    assert [p.agent for p in answer.results] == ["customer_agent", "sales_agent", "inventory_agent"]
    assert answer.status == "answered" and db.most_at_once == 3


@pytest.mark.parametrize(
    "text",
    [
        "Revenue was INR 902,538 across 9,999 customers.",  # 9,999 is in no result
        "Revenue per visiting customer was INR 4,178.",  # computed by the model
        "",
    ],
)
async def test_a_combined_summary_with_a_figure_from_nowhere_is_replaced_by_code(
    text: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    Db(monkeypatch)
    llm = script(FakeLLM(), to_agents("sales_agent", "customer_agent"))
    llm.on("supervisor_report", Combined(text=text))
    answer = await ask(llm, "Revenue and visiting customers?")
    assert answer.status == "answered"
    assert answer.summary == (
        "Sales agent: Revenue was INR 902,538. Customer agent: 216 customers visited."
    )


async def test_a_failed_combined_summary_falls_back_to_the_agents_own_words(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    Db(monkeypatch)
    llm = script(FakeLLM(), to_agents("sales_agent", "inventory_agent"))
    llm.on("supervisor_report", LLMUnavailableError("RateLimitError"))
    answer = await ask(llm, "Revenue and low stock?")
    assert answer.status == "answered" and len(answer.results) == 2
    assert answer.summary.startswith("Sales agent: Revenue was INR 902,538. Inventory agent: 3 ")


# -- the general route -------------------------------------------------------------------


@pytest.mark.parametrize(
    "question",
    [
        "What did we spend on rent last month?",  # expenses
        "How much did we buy from each supplier?",  # purchasing
        "What revenue came from products that are below their reorder level?",  # a join
        "What did VIP customers buy most?",  # customers x products
    ],
)
async def test_the_general_route_runs_the_existing_analytics_workflow_on_the_users_words(
    question: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = Db(monkeypatch)
    decision = Decision(
        route="general",
        agents=[AgentTask(agent="sales_agent", question="rewritten")],  # must be ignored
        reason="Needs data outside one agent.",
    )
    answer = await ask(script(FakeLLM(), decision), question)

    assert (answer.status, answer.route) == ("answered", "general")
    assert [p.agent for p in answer.results] == ["general"]
    part = answer.results[0]
    assert part.question == question and part.insights == []
    assert part.data == ROWS["general"] and answer.summary == SAID["general"]
    assert len(db.executed) == 1 and db.most_at_once == 1


async def test_the_general_route_sees_the_whole_vocabulary_and_no_agent_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    Db(monkeypatch)
    llm = script(FakeLLM(), Decision(route="general"))
    await ask(llm, "What were our expenses and purchases by supplier?")
    route, sql, summary = llm.calls
    assert [c.task for c in llm.calls] == ["route", "sql", "summary"]
    assert who_is(sql) == "general" and "<<<AGENT" not in sql.system
    assert "TABLE expenses:" in sql.user and "TABLE suppliers:" in sql.user


# -- ambiguous and unsupported -----------------------------------------------------------


async def test_an_ambiguous_question_is_answered_with_one_question_and_nothing_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = Db(monkeypatch)
    asked = "Do you mean sales, stock or customers?"
    llm = FakeLLM().on("route", Decision(route="clarify", clarification=asked, reason="Unclear."))
    answer = await ask(llm, "How are we doing?")
    assert (answer.status, answer.route) == ("needs_clarification", "clarify")
    assert answer.clarification == answer.summary == asked
    assert answer.results == [] and db.executed == [] and len(llm.calls) == 1


async def test_an_unsupported_question_is_declined_and_nothing_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = Db(monkeypatch)
    llm = FakeLLM().on("route", Decision(route="unsupported", reason="Not about the business."))
    answer = await ask(llm, "Write a poem about coffee.")
    assert (answer.status, answer.route) == ("unsupported", "unsupported")
    assert answer.summary == supervisor.UNSUPPORTED and answer.clarification is None
    assert answer.results == [] and db.executed == [] and len(llm.calls) == 1


async def test_two_decisions_that_break_the_rules_end_in_failed_and_nothing_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = Db(monkeypatch)
    llm = FakeLLM().on("route", to_agents("finance_agent"), to_agents("hr_agent"))
    answer = await ask(llm, "What was revenue?")
    assert (answer.status, answer.route) == ("failed", "undecided")
    assert answer.summary == supervisor.UNDECIDED
    assert answer.results == [] and db.executed == []


# -- the wrong agent ---------------------------------------------------------------------


async def test_a_wrongly_chosen_agent_declines_and_there_is_no_second_try(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = Db(monkeypatch)
    declined = "This question is about stock levels, which belongs to the Inventory agent."
    llm = FakeLLM().on("route", to_agents("sales_agent"))
    llm.on("sql", SqlDraft(sql=None, explanation=declined))
    answer = await ask(llm, "Which products are out of stock?")
    assert answer.status == "unanswerable" and answer.summary == declined
    assert [p.agent for p in answer.results] == ["sales_agent"]
    assert db.executed == [] and [c.task for c in llm.calls] == ["route", "sql"]


async def test_a_wrongly_chosen_agent_cannot_read_outside_its_own_tables(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = Db(monkeypatch)
    stock = SqlDraft(sql=SQL["inventory_agent"])
    llm = FakeLLM().on("route", to_agents("sales_agent")).on("sql", stock)
    llm.on("sql_repair", stock, stock)
    answer = await ask(llm, "Which products are out of stock?")
    assert answer.status == "failed" and answer.results[0].data == []
    assert db.executed == []
    assert {entry["reason"] for entry in db.logged} == {"table_not_allowed"}


# -- failures and partial results --------------------------------------------------------


@pytest.mark.parametrize(
    "failure",
    [
        LLMUnavailableError("RateLimitError"),
        UnavailableError("The database is not available."),
    ],
)
async def test_one_agent_failing_among_several_gives_a_partial_answer(
    failure: Exception, monkeypatch: pytest.MonkeyPatch
) -> None:
    Db(monkeypatch)
    llm = FakeLLM().on("route", to_agents("sales_agent", "inventory_agent"))

    def sql(name: str) -> BaseModel | BaseException:
        return failure if name == "inventory_agent" else SqlDraft(sql=SQL[name])

    llm.on("sql", per_target(sql), per_target(sql))
    llm.on("agent_report", AgentReport(summary=SAID["sales_agent"]))

    answer = await ask(llm, "Revenue and low stock?")

    assert answer.status == "partial"
    sales, stock = answer.results
    assert sales.status == "answered" and sales.data == ROWS["sales_agent"]
    assert stock.status == "failed" and stock.data == [] and stock.summary == assistant.PART_FAILED
    assert answer.summary == (
        "Sales agent: Revenue was INR 902,538. "
        "Inventory agent: This part could not be answered right now. Please try again."
    )
    assert "RateLimitError" not in answer.summary and "database" not in answer.summary


async def test_one_agent_declining_among_several_is_partial_and_keeps_the_decline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    Db(monkeypatch)
    llm = FakeLLM().on("route", to_agents("sales_agent", "customer_agent"))

    def sql(name: str) -> BaseModel:
        if name == "customer_agent":
            return SqlDraft(sql=None, explanation="That belongs to the Inventory agent.")
        return SqlDraft(sql=SQL[name])

    llm.on("sql", per_target(sql), per_target(sql))
    llm.on("agent_report", AgentReport(summary=SAID["sales_agent"]))
    answer = await ask(llm, "Revenue and stock?")
    assert answer.status == "partial"
    assert answer.results[1].status == "unanswerable"
    assert answer.summary.endswith("Customer agent: That belongs to the Inventory agent.")


async def test_every_agent_failing_is_failed_with_no_figures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = Db(monkeypatch)
    llm = FakeLLM().on("route", to_agents("sales_agent", "inventory_agent"))
    down = LLMUnavailableError("APITimeoutError")
    llm.on("sql", down, down)
    answer = await ask(llm, "Revenue and low stock?")
    assert answer.status == "failed" and db.executed == []
    assert [p.status for p in answer.results] == ["failed", "failed"]
    assert not any(ch.isdigit() for ch in answer.summary)


async def test_a_database_error_inside_an_agent_is_repaired_as_before(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = Db(monkeypatch)
    answers = iter([QueryFailedError("42803", "bad grouping"), result(ROWS["sales_agent"])])

    async def run(pg: Any, who: Caller, sql: str, *, max_rows: int) -> QueryResult:
        answer = next(answers)
        if isinstance(answer, BaseException):
            raise answer
        return answer

    monkeypatch.setattr(analytics, "run_checked_sql", run)
    llm = FakeLLM().on("route", to_agents("sales_agent"))
    llm.on("sql", SqlDraft(sql=SQL["sales_agent"]))
    llm.on("sql_repair", SqlDraft(sql=SQL["sales_agent"]))
    llm.on("agent_report", AgentReport(summary=SAID["sales_agent"]))
    answer = await ask(llm, "Revenue?")
    assert answer.status == "answered" and "bad grouping" not in answer.summary
    assert [entry["status"] for entry in db.logged] == ["error", "ok"]


async def test_a_query_timeout_in_the_general_route_keeps_its_own_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    Db(monkeypatch, general=QueryTimeoutError())
    llm = FakeLLM().on("route", Decision(route="general"))
    llm.on("sql", SqlDraft(sql=SQL["general"]))
    answer = await ask(llm, "All expenses ever, by day?")
    assert answer.status == "timeout" and answer.results[0].agent == "general"
    assert answer.summary.startswith("That question needs more data")


async def test_a_model_outage_in_the_general_route_is_a_failed_part_not_a_crash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    Db(monkeypatch)
    llm = FakeLLM().on("route", Decision(route="general"))
    llm.on("sql", LLMUnavailableError("RateLimitError"))
    answer = await ask(llm, "What did we spend?")
    assert answer.status == "failed" and answer.summary == assistant.PART_FAILED


async def test_a_supervisor_outage_is_raised_and_nothing_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = Db(monkeypatch)
    llm = FakeLLM().on("route", LLMUnavailableError("RateLimitError"))
    with pytest.raises(LLMUnavailableError):
        await ask(llm, "What was revenue?")
    assert db.executed == []


async def test_past_the_time_limit_the_answer_is_timeout_with_what_finished(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = Db(monkeypatch)

    async def run(pg: Any, who: Caller, sql: str, *, max_rows: int) -> QueryResult:
        if "inventory_levels" in sql:
            await asyncio.sleep(30)  # this agent never finishes in time
        return result(ROWS[db.target(sql)])

    monkeypatch.setattr(analytics, "run_checked_sql", run)
    llm = script(FakeLLM(), to_agents("sales_agent", "inventory_agent"))
    settings = Settings(_env_file=None, env="test", assistant_timeout_seconds=1)

    answer = await asyncio.wait_for(ask(llm, "Revenue and low stock?", settings=settings), 5)

    assert answer.status == "timeout" and answer.summary == assistant.TIMEOUT_MESSAGE
    assert [p.agent for p in answer.results] == ["sales_agent"]
    assert answer.results[0].data == ROWS["sales_agent"]


# -- who is asking -----------------------------------------------------------------------


@pytest.mark.parametrize(
    "decision",
    [to_agents("sales_agent", "inventory_agent", "customer_agent"), Decision(route="general")],
)
async def test_every_query_runs_as_the_caller_and_no_prompt_names_them(
    decision: Decision, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = Db(monkeypatch)
    who = caller("viewer", limited=True)
    llm = script(FakeLLM(), decision)
    llm.on("supervisor_report", Combined(text="Revenue 902,538; 3 low; 216 customers."))

    answer = await ask(llm, "Everything about the business, please.", who=who)

    assert db.callers and all(c is who for c in db.callers)
    for call in llm.calls:
        assert str(TENANT) not in call.text and "tenant_id" not in call.text
        assert str(who.user_id) not in call.text and str(who.outlet_ids[0]) not in call.text
    assert answer.scope_note == "Limited to your outlets: Indiranagar."
    assert answer.summary.count("Limited to your outlets: Indiranagar.") == 1


async def test_the_graph_has_the_designed_shape() -> None:
    drawn = assistant.GRAPH.get_graph()
    edges = {(e.source, e.target) for e in drawn.edges}
    assert edges == {
        ("__start__", "supervise"),
        ("supervise", "run_agent"),
        ("supervise", "run_general"),
        ("supervise", "run_documents"),
        ("supervise", "aggregate"),
        ("run_agent", "aggregate"),
        ("run_general", "aggregate"),
        ("run_documents", "aggregate"),
        ("aggregate", "__end__"),
    }
    assert assistant.GRAPH.checkpointer is None  # nothing about a run is stored


# -- the API -----------------------------------------------------------------------------

URL = "/api/v1/assistant/query"


class App:
    def __init__(self, client: TestClient) -> None:
        self.client = client
        self.llm = FakeLLM()
        client.app.state.llm = self.llm  # type: ignore[attr-defined]

    def sign_in(self, who: Caller) -> None:
        self.client.app.dependency_overrides[get_caller] = lambda: who  # type: ignore[attr-defined]

    def ask(self, question: Any) -> Any:
        return self.client.post(URL, json={"question": question})


@pytest.fixture
def app() -> Iterator[App]:
    with TestClient(create_app(SETTINGS)) as client:
        yield App(client)


def test_the_api_answers_in_the_assistant_shape(app: App, monkeypatch: pytest.MonkeyPatch) -> None:
    Db(monkeypatch)
    app.sign_in(caller("analyst"))
    script(app.llm, to_agents("sales_agent", "customer_agent"))
    app.llm.on("supervisor_report", Combined(text="Revenue 902,538 and 216 customers."))

    response = app.ask("Revenue and customers?")

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {
        "question", "status", "route", "reason", "summary", "clarification", "results",
        "scope_note",
    }  # fmt: skip
    assert (body["status"], body["route"]) == ("answered", "agents")
    assert [r["agent"] for r in body["results"]] == ["sales_agent", "customer_agent"]
    assert set(body["results"][0]) == {
        "agent", "question", "status", "summary", "insights", "sql", "columns", "data",
        "row_count", "truncated", "assumptions", "sources",
    }  # fmt: skip
    assert body["results"][0]["sql"].startswith("SELECT ROUND(SUM(")
    assert body["results"][0]["columns"] == [{"name": "revenue", "type": "money"}]


def test_a_viewer_gets_no_sql_in_any_part(app: App, monkeypatch: pytest.MonkeyPatch) -> None:
    Db(monkeypatch)
    app.sign_in(caller("viewer", limited=True))
    script(app.llm, to_agents("sales_agent", "inventory_agent"))
    app.llm.on("supervisor_report", Combined(text="Revenue 902,538; 3 low products."))
    body = app.ask("Revenue and low stock?").json()
    assert [r["sql"] for r in body["results"]] == [None, None]
    assert body["results"][0]["data"] == ROWS["sales_agent"]
    assert "SELECT" not in str(body)


def test_clarify_and_unsupported_are_200_with_their_status(
    app: App, monkeypatch: pytest.MonkeyPatch
) -> None:
    Db(monkeypatch)
    app.sign_in(caller("analyst"))
    app.llm.on("route", Decision(route="clarify", clarification="Which area do you mean?"))
    body = app.ask("How are we doing?").json()
    assert body["status"] == "needs_clarification" and body["results"] == []
    assert body["clarification"] == "Which area do you mean?"

    app.llm.on("route", Decision(route="unsupported"))
    response = app.ask("Write a poem.")
    assert response.status_code == 200 and response.json()["status"] == "unsupported"


@pytest.mark.parametrize("bad", ["", "hi", "x" * 2001, None, 7])
def test_an_invalid_question_is_refused_before_any_model_call(app: App, bad: Any) -> None:
    app.sign_in(caller("analyst"))
    assert app.ask(bad).status_code == 422
    assert app.llm.calls == []


def test_no_caller_is_401_and_a_role_without_the_permission_is_403(app: App) -> None:
    assert app.ask("What was revenue?").status_code == 401
    app.sign_in(caller("super_admin"))
    assert app.ask("What was revenue?").status_code == 403
    assert app.llm.calls == []


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
def test_a_supervisor_that_cannot_be_reached_is_a_503(app: App, reason: str, message: str) -> None:
    app.sign_in(caller("analyst"))
    app.llm.on("route", LLMUnavailableError(reason))
    response = app.ask("What was revenue?")
    assert response.status_code == 503
    assert response.json()["error"]["message"] == message


def test_the_two_older_routes_still_answer_in_their_own_shapes(
    app: App, monkeypatch: pytest.MonkeyPatch
) -> None:
    FakeDatabase(result(ROWS["sales_agent"]), result(ROWS["sales_agent"])).install(monkeypatch)
    app.sign_in(caller("analyst"))
    app.llm.on("sql", SqlDraft(sql=SQL["sales_agent"]), SqlDraft(sql=SQL["sales_agent"]))
    app.llm.on("summary", Summary(text="Revenue was 902,538."))
    app.llm.on("agent_report", AgentReport(summary="Revenue was 902,538."))

    old = app.client.post("/api/v1/analytics/query", json={"question": "Revenue?"}).json()
    assert set(old) == {
        "question", "status", "summary", "sql", "columns", "data", "row_count", "truncated",
        "assumptions", "scope_note",
    }  # fmt: skip
    agent = app.client.post("/api/v1/agents/sales_agent/query", json={"question": "Revenue?"})
    assert set(agent.json()) == set(old) | {"agent", "insights"}
    assert app.llm.calls_for("route") == []  # neither goes through the supervisor
