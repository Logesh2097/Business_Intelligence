"""The supervisor: reads a question and decides who should answer it. It answers nothing.

    question -> one model call -> a decision -> checked by code -> a plan

The model only proposes. What runs is decided here: agent names must be in the
registry, the general route stands alone, and the user's own words are what an agent
is given unless a part of them is clearly enough. The supervisor has no tools, no
database access and no knowledge of who is asking, so nothing it says can widen what
a user may see.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field

from app.agents.prompting import block, load_prompt
from app.agents.registry import AGENTS
from app.agents.summarizer import numbers_in
from app.llm.base import LLM, LLMInvalidResponseError

log = logging.getLogger("bi.supervisor")

GENERAL = "general"  # the route to the general analytics workflow; not an agent
DOCUMENTS = "document_agent"  # the target of the route "documents"; not a SQL agent
MAX_ATTEMPTS = 2  # a decision that breaks the rules is asked for once more
CLARIFICATION = (
    "Which area do you mean: sales, inventory, customers, "
    "or something else such as purchasing or expenses?"
)
UNSUPPORTED = (
    "I can only answer questions about this business's data: "
    "sales, inventory, customers, purchasing and expenses, "
    "and about what is written in its uploaded documents."
)
UNDECIDED = "I could not work out how to answer this question. Try asking it in another way."

Route = Literal["agents", "general", "documents", "clarify", "unsupported"]


class AgentTask(BaseModel):
    agent: str = Field(description="sales_agent, inventory_agent or customer_agent")
    question: str = Field(default="", description="The part of the question for this agent")


class Decision(BaseModel):
    """What the model must return."""

    route: Route
    agents: list[AgentTask] = Field(default_factory=list)
    clarification: str = ""
    reason: str = ""


@dataclass(frozen=True)
class Task:
    target: str  # an agent's name, or GENERAL
    question: str  # what that target is asked


@dataclass(frozen=True)
class Plan:
    route: Route | Literal["undecided"]
    tasks: tuple[Task, ...] = ()
    clarification: str = ""
    reason: str = ""


def agents_text() -> str:
    """The agents and their responsibilities, from the registry so they cannot drift."""
    lines = []
    for agent in AGENTS.values():
        lines.append(f"{agent.name} ({agent.title}):")
        lines += [f"  - {duty}" for duty in agent.responsibilities]
    return "\n".join(lines)


def build_prompt(question: str) -> tuple[str, str]:
    user = block("AGENTS", agents_text()) + "\n\n" + block("QUESTION", question)
    return load_prompt("supervisor"), user


def _keeps_the_users_words(part: str, question: str) -> bool:
    """A part is usable only if it adds no figure or date and is no longer than the question."""
    if not part or len(part) > len(question):
        return False
    said = set(re.findall(r"\d+", question))
    return all(n in said for n in re.findall(r"\d+", part))


def _one_plain_question(text: str) -> bool:
    text = text.strip()
    return (
        3 <= len(text) <= 300
        and text.endswith("?")
        and text.count("?") == 1
        and not numbers_in(text)
    )


def validate(decision: Decision, question: str) -> Plan | None:
    """Turn the model's decision into what will run, or None if it breaks the rules."""
    reason = decision.reason.strip()

    if decision.route == "agents":
        chosen: dict[str, str] = {}
        for task in decision.agents:
            name = task.agent.strip()
            if name not in AGENTS:
                log.warning("supervisor named an unknown agent")
                return None
            chosen.setdefault(name, task.question.strip())  # a repeated agent is one agent
        if not chosen or len(chosen) > len(AGENTS):
            return None
        if len(chosen) == 1:
            # one agent answers the whole question: it gets the user's words, untouched
            return Plan("agents", (Task(next(iter(chosen)), question),), reason=reason)
        tasks = tuple(
            Task(name, part if _keeps_the_users_words(part, question) else question)
            for name, part in chosen.items()
        )
        return Plan("agents", tasks, reason=reason)

    if decision.agents:
        log.info("supervisor listed agents for a route that takes none; ignored")

    if decision.route == "general":
        # the general workflow always gets the user's words, untouched
        return Plan("general", (Task(GENERAL, question),), reason=reason)

    if decision.route == "documents":
        # the document agent always gets the user's words, untouched
        return Plan("documents", (Task(DOCUMENTS, question),), reason=reason)

    if decision.route == "clarify":
        asked = decision.clarification.strip()
        return Plan(
            "clarify",
            clarification=asked if _one_plain_question(asked) else CLARIFICATION,
            reason=reason,
        )

    return Plan("unsupported", reason=reason)


async def decide(question: str, llm: LLM) -> Plan:
    """One decision, checked. A model outage is raised; a bad decision is not."""
    system, user = build_prompt(question)
    extra = ""
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            decision = await llm.structured(
                task="route", system=system, user=user + extra, schema=Decision, fast=True
            )
        except LLMInvalidResponseError:
            decision = None
        plan = validate(decision, question) if decision is not None else None
        if plan is not None:
            log.info(
                "routed",
                extra={
                    "route": plan.route,
                    "targets": [t.target for t in plan.tasks],
                    "attempt": attempt,
                },
            )
            return plan
        extra = (
            "\n\nYour previous answer did not follow the rules: use one of the five routes, "
            "and for the route agents name only agents listed under AGENTS."
        )
    log.warning("supervisor gave no usable decision")
    return Plan("undecided")
