"""The Customer agent: visits, new and returning customers, buying behaviour, retention."""

from __future__ import annotations

from app.agents.domain import DomainAgent

AGENT = DomainAgent(
    name="customer_agent",
    title="Customer agent",
    responsibilities=(
        "Analyse customer visits",
        "Identify new and returning customers",
        "Analyse customer purchase behaviour",
        "Compare customer visits across outlets",
        "Identify changes in customer activity",
        "Analyse customer retention trends",
    ),
    tables=frozenset({"outlets", "customers", "customer_visits", "orders"}),
    examples=(
        "How many customers visited this month?",
        "Which outlet has the highest customer visits?",
        "How many customers returned this month?",
        "Are customer visits increasing or decreasing?",
    ),
)
