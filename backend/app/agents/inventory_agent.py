"""The Inventory agent: stock on hand, shortages, stock movements, slow and fast sellers."""

from __future__ import annotations

from app.agents.domain import DomainAgent

AGENT = DomainAgent(
    name="inventory_agent",
    title="Inventory agent",
    responsibilities=(
        "Analyse product stock availability",
        "Identify low-stock products",
        "Find out-of-stock products",
        "Analyse inventory movement",
        "Identify products with slow or high sales",
        "Compare stock availability across outlets",
    ),
    # orders and order_items are here only to tell slow sellers from fast ones
    tables=frozenset(
        {
            "outlets",
            "categories",
            "products",
            "inventory_levels",
            "stock_movements",
            "orders",
            "order_items",
        }
    ),
    examples=(
        "Which products are running low on stock?",
        "Which products are out of stock?",
        "Which outlet has the highest stock shortage?",
        "Which products have not sold recently?",
    ),
)
