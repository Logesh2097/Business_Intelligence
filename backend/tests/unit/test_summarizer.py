"""MOCKED: the natural-language answer, and the check that its numbers are real."""

from __future__ import annotations

import pytest

from app.agents.summarizer import (
    Summary,
    numbers_in,
    plain_summary,
    summarise,
    unsupported_numbers,
)
from app.llm.base import LLMInvalidResponseError, LLMUnavailableError
from app.llm.fake import FakeLLM
from tests.unit.analytics_fakes import caller, result

Q = "Which outlet had the highest revenue last month?"
TOP = result(
    [
        {"outlet": "Indiranagar", "revenue": 512300.0, "orders": 1480},
        {"outlet": "Koramangala", "revenue": 431875.5, "orders": 1215},
    ]
)


async def say(llm: FakeLLM, res=TOP, who=None, question: str = Q) -> str:  # type: ignore[no-untyped-def]
    return await summarise(question=question, result=res, caller=who or caller(), llm=llm)


# -- a good explanation ------------------------------------------------------------


async def test_the_models_sentence_is_returned_when_its_numbers_are_in_the_result() -> None:
    text = "Indiranagar had the highest revenue, ₹5,12,300 from 1,480 orders."
    llm = FakeLLM().on("summary", Summary(text=text))
    assert await say(llm) == text
    assert llm.calls[0].fast is True


async def test_the_model_sees_the_question_and_the_result_as_delimited_data() -> None:
    llm = FakeLLM().on("summary", Summary(text="Indiranagar leads."))
    await say(llm)
    prompt = llm.calls[0].user
    assert f"<<<QUESTION\n{Q}\nQUESTION>>>" in prompt
    assert '"outlet": "Indiranagar"' in prompt and '"revenue": 512300.0' in prompt
    assert '"currency": "INR"' in prompt
    assert "Use only the numbers that appear in RESULT" in llm.calls[0].system


async def test_only_the_first_rows_are_sent_and_the_model_is_told() -> None:
    many = result([{"product": f"P{n}", "units": n} for n in range(1, 81)])
    llm = FakeLLM().on("summary", Summary(text="P1 sold 1 unit."))
    await say(llm, many)
    prompt = llm.calls[0].user
    assert '"P30"' in prompt and '"P31"' not in prompt
    assert '"row_count": 80' in prompt and '"cut_short": true' in prompt


async def test_a_limited_user_is_told_the_scope() -> None:
    llm = FakeLLM().on("summary", Summary(text="Indiranagar leads with ₹512,300."))
    text = await say(llm, who=caller("viewer", limited=True))
    assert text.endswith("Limited to your outlets: Indiranagar.")


# -- invented values -----------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "Indiranagar made ₹6,00,000.",  # not in the result
        "Indiranagar beat Koramangala by ₹80,424.50.",  # a difference the model computed
        "Revenue was up 18.6% on Koramangala.",  # a percentage the model computed
        "Total revenue was ₹9,44,175.50.",  # a sum the model computed
        "Indiranagar served 1,480 orders across 7 outlets.",  # 7 is invented
    ],
)
async def test_an_invented_or_computed_number_is_replaced_by_a_sentence_built_by_code(
    text: str,
) -> None:
    llm = FakeLLM().on("summary", Summary(text=text), Summary(text=text))
    answer = await say(llm)
    assert answer == (
        "Found 2 rows. The first is outlet: Indiranagar, revenue: INR 512,300.00, orders: 1480."
    )
    assert len(llm.calls) == 2  # asked once more, then stopped
    assert "used numbers that are not in RESULT" in llm.calls[1].user


async def test_a_corrected_second_answer_is_accepted() -> None:
    llm = FakeLLM().on(
        "summary",
        Summary(text="Indiranagar made ₹6,00,000."),
        Summary(text="Indiranagar made ₹512,300."),
    )
    assert await say(llm) == "Indiranagar made ₹512,300."


@pytest.mark.parametrize(
    ("text", "bad"),
    [
        ("Revenue was 512300.", []),
        ("Revenue was ₹5,12,300.00.", []),  # Indian digit grouping
        ("Revenue was about 512,300 from 1,480 orders.", []),
        ("Koramangala made 431,876.", []),  # rounded to the whole unit
        ("The top 2 outlets are shown.", []),  # the row count
        ("In October 2026 (2026-10-01 to 2026-10-31), revenue was 512300.", []),  # dates
        ("From 1 Oct to 31 Oct 2026, revenue was 512300.", []),
        ("Revenue was 512300 and 99999.", [99999.0]),
        ("Revenue was 5.1 lakh.", [5.1]),  # a converted unit is not a value of the result
    ],
)
def test_which_numbers_count_as_supported(text: str, bad: list[float]) -> None:
    assert unsupported_numbers(text, Q, TOP) == bad


def test_a_number_from_the_question_may_be_repeated() -> None:
    top10 = result([{"product": f"P{n}", "units": 100 - n} for n in range(10)])
    assert (
        unsupported_numbers("The top 10 products are listed.", "Show the top 10 products", top10)
        == []
    )


def test_a_ratio_may_be_written_as_a_percentage() -> None:
    rate = result([{"discount_rate": 0.0612}])
    assert unsupported_numbers("The discount rate was 6.1%.", "discount rate?", rate) == []


def test_numbers_in_ignores_dates_but_not_amounts() -> None:
    assert numbers_in("On 2026-10-31 and in October 2026 we made 1,200.50 from 30 orders") == [
        1200.5,
        30.0,
    ]


# -- no data, and a model that fails -----------------------------------------------


async def test_an_empty_result_is_explained_by_code_and_the_model_is_not_asked() -> None:
    llm = FakeLLM()  # nothing scripted: a call would fail the test
    assert await say(llm, result([])) == "No data matched this question."
    assert llm.calls == []


async def test_an_empty_result_for_a_limited_user_states_the_scope() -> None:
    text = await say(FakeLLM(), result([]), who=caller("viewer", limited=True))
    assert text == "No data matched this question. Limited to your outlets: Indiranagar."


@pytest.mark.parametrize("error", [LLMUnavailableError("down"), LLMInvalidResponseError("summary")])
async def test_when_the_model_fails_the_data_is_still_explained(error: Exception) -> None:
    one = result([{"revenue": 912345.5}])
    assert await say(FakeLLM().on("summary", error), one) == "Result: revenue: INR 912,345.50."


def test_the_plain_sentence_says_when_rows_were_cut() -> None:
    cut = result([{"product": "Latte", "units": 3}, {"product": "Tea", "units": 2}], truncated=True)
    assert plain_summary(cut, caller()).endswith("Only the first rows are shown.")
