"""REAL DRIVERS, NO SERVER: the evaluation may only ever drop and rebuild `bi_eval`.

These prove the refusals without a database: a wrong name, a protected name, a
production environment, a remote server, and settings that already point at bi_eval
all stop before any connection is opened.
"""

from __future__ import annotations

import inspect
import sys
from datetime import date

import pytest
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from app.core.config import Settings, load_settings
from app.seed.generate import generate
from tests.evals import database
from tests.evals.database import EvalDatabaseError, check_target, eval_settings


def settings(**changes: object) -> Settings:
    base = {
        "env": "test",
        "pg_dsn": "postgresql://bi_app@localhost:5610/bi",
        "pg_readonly_dsn": "postgresql://bi_readonly@localhost:5610/bi",
        "pg_owner_dsn": "postgresql://bi_owner@localhost:5610/bi",
    }
    return Settings(_env_file=None, **{**base, **changes})  # type: ignore[arg-type]


def test_the_fixed_inputs_are_the_approved_ones() -> None:
    assert database.EVAL_DATABASE == "bi_eval"
    assert database.REFERENCE_DATE == date(2026, 11, 11)
    assert (database.SEED, database.DAYS) == (42, 120)


def test_bi_eval_with_normal_local_settings_is_allowed() -> None:
    check_target("bi_eval", settings())


@pytest.mark.parametrize(
    "name",
    [
        "bi",
        "bi_test",
        "postgres",
        "template0",
        "template1",
        "",
        "BI_EVAL",
        "bi_eval2",
        "bi_eval; DROP DATABASE bi",
    ],
)
def test_no_other_database_name_is_ever_accepted(name: str) -> None:
    with pytest.raises(EvalDatabaseError, match="refused"):
        check_target(name, settings())


@pytest.mark.parametrize("env", ["prod", "staging"])
def test_it_is_refused_outside_dev_and_test(env: str) -> None:
    with pytest.raises(EvalDatabaseError, match="not dev or test"):
        check_target("bi_eval", settings().model_copy(update={"env": env}))


@pytest.mark.parametrize("field", ["pg_dsn", "pg_readonly_dsn", "pg_owner_dsn"])
def test_it_is_refused_when_the_application_already_points_at_bi_eval(field: str) -> None:
    pointed = settings(**{field: "postgresql://bi_app@localhost:5610/bi_eval"})
    with pytest.raises(EvalDatabaseError, match="already points at"):
        check_target("bi_eval", pointed)


@pytest.mark.parametrize("field", ["pg_dsn", "pg_readonly_dsn", "pg_owner_dsn"])
def test_it_is_refused_on_a_server_that_is_not_local(field: str) -> None:
    remote = settings(**{field: "postgresql://bi_app@db.example.com:5432/bi"})
    with pytest.raises(EvalDatabaseError, match="not a local server"):
        check_target("bi_eval", remote)


def test_it_is_refused_without_the_owners_connection() -> None:
    with pytest.raises(EvalDatabaseError, match="BI_PG_OWNER_DSN"):
        check_target("bi_eval", settings(pg_owner_dsn=""))


def test_the_evaluation_settings_are_a_copy_and_the_original_is_unchanged() -> None:
    normal = settings(openai_api_key="a-key")
    copy = eval_settings(normal)
    for dsn in (copy.pg_dsn, copy.pg_readonly_dsn, copy.pg_owner_dsn):
        assert database.database_of(dsn) == "bi_eval"
    assert copy.fake_today == date(2026, 11, 11) and copy.openai_api_key == ""
    assert database.database_of(normal.pg_dsn) == "bi" and normal.fake_today is None
    assert normal.openai_api_key == "a-key"
    assert eval_settings(normal, keep_model=True).openai_api_key == "a-key"


def test_the_copy_keeps_the_same_server_and_users() -> None:
    normal = settings()
    copy = eval_settings(normal)
    assert copy.pg_dsn == make_conninfo(normal.pg_dsn, dbname="bi_eval")
    for before, after in (
        (normal.pg_dsn, copy.pg_dsn),
        (normal.pg_readonly_dsn, copy.pg_readonly_dsn),
        (normal.pg_owner_dsn, copy.pg_owner_dsn),
    ):
        was, now = conninfo_to_dict(before), conninfo_to_dict(after)
        assert {**was, "dbname": "bi_eval"} == now  # only the database name differs


def test_confirming_the_target_refuses_another_database_before_connecting() -> None:
    with pytest.raises(EvalDatabaseError, match="'bi' is not bi_eval"):
        database.confirm_is_eval("postgresql://nobody@localhost:1/bi")


def test_recreate_refuses_before_opening_any_connection() -> None:
    # port 1 is closed: if a connection were attempted this would be an OperationalError
    production = settings(pg_owner_dsn="postgresql://bi_owner@localhost:1/bi").model_copy(
        update={"env": "prod"}
    )
    with pytest.raises(EvalDatabaseError):
        database.recreate(production)


def test_no_function_takes_the_name_of_a_database_to_drop() -> None:
    source = inspect.getsource(database)
    assert source.count('sql.SQL("DROP DATABASE') == 1  # one place, and it formats the constant
    assert 'DROP DATABASE {} WITH (FORCE)").format(name)' in source
    assert "name = sql.Identifier(EVAL_DATABASE)" in source
    assert list(inspect.signature(database.recreate).parameters) == ["base"]
    assert (
        "DELETE FROM" not in source and "DROP TABLE" not in source and "DROP SCHEMA" not in source
    )


def test_nothing_in_the_evaluation_writes_settings_or_env_files() -> None:
    source = inspect.getsource(database)
    assert "os.environ" not in source and "putenv" not in source
    writes = [line for line in source.split("\n") if "write_text" in line or "open(" in line]
    assert writes == []  # baseline.yaml is read here, never written


def test_the_normal_settings_do_not_point_at_bi_eval() -> None:
    normal = load_settings()
    assert database.database_of(normal.pg_dsn) != "bi_eval"


# -- the fingerprint: the same rows on every run, on this Python --------------------------------


def test_the_generator_gives_the_approved_rows_on_this_python() -> None:
    """Mandatory, and never skipped: if this fails, expected values are NOT to be edited."""
    baseline = database.load_baseline()
    assert baseline is not None
    data = generate(database.REFERENCE_DATE, database.DAYS, seed=database.SEED)
    version = f"Python {sys.version_info.major}.{sys.version_info.minor}"
    assert database.fingerprint(data) == baseline["fingerprint"], (
        f"the generator gives different rows on {version}: the golden set's figures do not hold"
    )
    assert baseline["inputs"] == {"reference_date": "2026-11-11", "seed": 42, "days": 120}


def test_the_fingerprint_changes_with_any_input() -> None:
    approved = database.load_baseline()["fingerprint"]  # type: ignore[index]
    assert database.fingerprint(generate(date(2026, 11, 12), 120, seed=42)) != approved
    assert database.fingerprint(generate(date(2026, 11, 11), 119, seed=42)) != approved
    assert database.fingerprint(generate(date(2026, 11, 11), 120, seed=43)) != approved
