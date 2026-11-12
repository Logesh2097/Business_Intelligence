"""The Sales agent: revenue, orders, products and outlets by what they sold."""

from __future__ import annotations

from app.agents.domain import DomainAgent

AGENT = DomainAgent(
    name="sales_agent",
    title="Sales agent",
    responsibilities=(
        "Analyse total sales and revenue",
        "Identify top-selling products",
        "Compare daily, weekly and monthly sales",
        "Identify outlets with high and low revenue",
        "Analyse sales trends",
        "Identify unusual sales changes",
    ),
    tables=frozenset({"outlets", "categories", "products", "orders", "order_items"}),
    examples=(
        "What was our total revenue last month?",
        "Which products generated the highest revenue?",
        "Which outlet performed best this month?",
        "How much did sales increase compared to last month?",
    ),
)
