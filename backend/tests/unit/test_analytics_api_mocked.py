"""MOCKED: POST /api/v1/analytics/query with a scripted model, a fake database and a
caller supplied by the test. Live equivalent: tests/integration/test_analytics_live.py.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.agents.sql_analyst import SqlDraft
from app.agents.summarizer import Summary
from app.api.deps import get_caller
from app.core.config import Settings
from app.core.context import Caller
from app.llm.base import LLMUnavailableError
from app.llm.fake import FakeLLM
from app.main import create_app
from app.store import users
from app.store.analytics import QueryTimeoutError
from tests.unit.analytics_fakes import FakeDatabase, caller, result

URL = "/api/v1/analytics/query"
Q = "What was our total revenue last month?"
SQL = (
    "SELECT ROUND(SUM(total_amount - tax_amount), 2) AS revenue FROM orders "
    "WHERE status = 'completed'"  # a sales figure must say which orders count (Phase 5.1)
)
REVENUE = result([{"revenue": 912345.5}])


class App:
    def __init__(self, client: TestClient) -> None:
        self.client = client
        self.llm = FakeLLM()
        client.app.state.llm = self.llm  # type: ignore[attr-defined]

    def sign_in(self, who: Caller) -> None:
        self.client.app.dependency_overrides[get_caller] = lambda: who  # type: ignore[attr-defined]

    def ask(self, question: Any = Q, **kw: Any) -> Any:
        return self.client.post(URL, json={"question": question}, **kw)


@pytest.fixture
def app() -> Iterator[App]:
    with TestClient(create_app(Settings(_env_file=None, env="test"))) as client:
        yield App(client)


def good(app: App) -> None:
    app.llm.on("sql", SqlDraft(sql=SQL, explanation="x", assumptions=["Sales means revenue."]))
    app.llm.on("summary", Summary(text="Revenue last month was ₹912,345.50."))


# -- the answer --------------------------------------------------------------------


def test_a_question_returns_the_summary_the_data_and_the_sql(
    app: App, monkeypatch: pytest.MonkeyPatch
) -> None:
    FakeDatabase(REVENUE).install(monkeypatch)
    app.sign_in(caller("analyst"))
    good(app)
    response = app.ask()

    assert response.status_code == 200
    assert response.json() == {
        "question": Q,
        "status": "answered",
        "summary": "Revenue last month was ₹912,345.50.",
        "sql": SQL + " LIMIT 1001",
        "columns": [{"name": "revenue", "type": "money"}],
        "data": [{"revenue": 912345.5}],
        "row_count": 1,
        "truncated": False,
        "assumptions": ["Sales means revenue."],
        "scope_note": None,
    }
    assert response.headers["X-Request-Id"]


def test_a_viewer_gets_the_answer_but_never_the_sql(
    app: App, monkeypatch: pytest.MonkeyPatch
) -> None:
    FakeDatabase(REVENUE).install(monkeypatch)
    app.sign_in(caller("viewer", limited=True))
    good(app)
    body = app.ask().json()

    assert body["status"] == "answered" and body["data"] == [{"revenue": 912345.5}]
    assert body["sql"] is None
    assert "SELECT" not in str(body)
    assert body["scope_note"] == "Limited to your outlets: Indiranagar."
    assert body["summary"].endswith("Limited to your outlets: Indiranagar.")


def test_no_data_is_said_clearly(app: App, monkeypatch: pytest.MonkeyPatch) -> None:
    FakeDatabase(result([])).install(monkeypatch)
    app.sign_in(caller())
    app.llm.on("sql", SqlDraft(sql=SQL))  # no summary scripted: the model must not be asked
    body = app.ask().json()
    assert (body["status"], body["data"], body["row_count"]) == ("no_data", [], 0)
    assert body["summary"] == "No data matched this question."


def test_a_question_the_data_cannot_answer(app: App, monkeypatch: pytest.MonkeyPatch) -> None:
    FakeDatabase().install(monkeypatch)
    app.sign_in(caller())
    app.llm.on("sql", SqlDraft(sql=None, explanation="There is no data about the weather."))
    body = app.ask("What will the weather be tomorrow?").json()
    assert body["status"] == "unanswerable" and body["sql"] is None and body["data"] == []
    assert body["summary"] == "There is no data about the weather."


def test_unsafe_sql_three_times_gives_a_readable_failure_and_shows_none_of_it(
    app: App, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = FakeDatabase().install(monkeypatch)
    app.sign_in(caller("admin"))
    app.llm.on("sql", SqlDraft(sql="DROP TABLE orders"))
    app.llm.on("sql_repair", SqlDraft(sql="DELETE FROM orders"), SqlDraft(sql="TRUNCATE orders"))
    response = app.ask()

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "failed" and body["sql"] is None and body["data"] == []
    assert body["summary"].startswith("I could not build a safe query")
    for word in ("DROP", "DELETE", "TRUNCATE"):
        assert word not in response.text
    assert db.executed == []


def test_a_timeout_is_explained(app: App, monkeypatch: pytest.MonkeyPatch) -> None:
    FakeDatabase(QueryTimeoutError()).install(monkeypatch)
    app.sign_in(caller())
    app.llm.on("sql", SqlDraft(sql=SQL))
    body = app.ask().json()
    assert body["status"] == "timeout" and body["data"] == []
    assert "shorter period" in body["summary"]


# -- failures ----------------------------------------------------------------------


def test_a_model_outage_is_503_in_the_error_shape(
    app: App, monkeypatch: pytest.MonkeyPatch
) -> None:
    FakeDatabase().install(monkeypatch)
    app.sign_in(caller())
    app.llm.on("sql", LLMUnavailableError("AuthenticationError"))
    response = app.ask()
    assert response.status_code == 503
    error = response.json()["error"]
    assert error["code"] == "unavailable"
    assert error["message"] == "The assistant is not available right now. Please try again."
    assert "Authentication" not in response.text


def test_without_a_model_key_the_answer_says_it_is_not_set_up(
    app: App, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.llm import build_llm

    FakeDatabase().install(monkeypatch)
    app.sign_in(caller())
    app.client.app.state.llm = build_llm(Settings(_env_file=None, env="test"))  # type: ignore[attr-defined]
    response = app.ask()
    assert response.status_code == 503
    assert "no model key is configured" in response.json()["error"]["message"]


def test_a_crash_inside_the_pipeline_shows_nothing_internal(
    app: App, monkeypatch: pytest.MonkeyPatch
) -> None:
    FakeDatabase(RuntimeError("relation biz.orders: password=hunter2")).install(monkeypatch)
    app.sign_in(caller())
    app.llm.on("sql", SqlDraft(sql=SQL))
    response = app.ask()
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "internal"
    assert "hunter2" not in response.text and "biz.orders" not in response.text


# -- the request -------------------------------------------------------------------


@pytest.mark.parametrize(
    "body",
    [{}, {"question": ""}, {"question": "  "}, {"question": "hi"}, {"question": "x" * 2001},
     {"question": 42}, {"question": None}, {"q": "revenue?"}],
)  # fmt: skip
def test_a_bad_request_is_422_and_the_model_is_never_called(app: App, body: dict[str, Any]) -> None:
    app.sign_in(caller())
    response = app.client.post(URL, json=body)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_failed"
    assert app.llm.calls == []


def test_the_question_is_trimmed(app: App, monkeypatch: pytest.MonkeyPatch) -> None:
    FakeDatabase(REVENUE).install(monkeypatch)
    app.sign_in(caller())
    good(app)
    assert app.ask(f"   {Q}  \n").json()["question"] == Q


# -- who is asking -----------------------------------------------------------------


def test_without_a_caller_it_is_401_and_nothing_runs(app: App) -> None:
    response = app.ask()
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "not_authenticated"
    assert app.llm.calls == []


def test_an_unknown_user_is_401(app: App, monkeypatch: pytest.MonkeyPatch) -> None:
    async def nobody(pg: Any, email: str) -> None:
        return None

    monkeypatch.setattr(users, "find_caller_by_email", nobody)
    response = app.ask(headers={"X-Dev-User": "stranger@example.com"})
    assert response.status_code == 401
    assert response.json()["error"]["message"] == "Unknown or inactive user."


def test_the_development_header_names_a_demo_user(
    app: App, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = []

    async def lookup(pg: Any, email: str) -> Caller:
        seen.append(email)
        return caller("analyst")

    monkeypatch.setattr(users, "find_caller_by_email", lookup)
    FakeDatabase(REVENUE).install(monkeypatch)
    good(app)
    assert app.ask(headers={"X-Dev-User": "analyst@brewbite.example"}).status_code == 200
    assert seen == ["analyst@brewbite.example"]


def test_outside_development_the_header_is_ignored_and_the_route_is_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def anyone(pg: Any, email: str) -> Caller:
        return caller("admin")

    monkeypatch.setattr(users, "find_caller_by_email", anyone)
    prod = Settings(
        _env_file=None, env="prod", secret_key="s" * 40, openai_api_key="sk-test",
        llm_model="m", pg_dsn="postgresql://bi_app:real@db/bi",
        pg_readonly_dsn="postgresql://bi_readonly:real@db/bi",
    )  # fmt: skip
    with TestClient(create_app(prod)) as client:
        response = client.post(
            URL, json={"question": Q}, headers={"X-Dev-User": "admin@brewbite.example"}
        )
    assert response.status_code == 401
    assert response.json()["error"]["message"] == "Sign-in is not available yet."
