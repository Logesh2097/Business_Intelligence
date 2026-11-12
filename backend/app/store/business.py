"""The fixed business reads behind the dashboard and the business tools.

Every query here is written by us, takes its values as bind parameters, and runs as
bi_readonly in a read-only transaction under the caller's row security
(`pg.tenant_session`): the company and the outlets are the session's, never a
parameter. The definitions are the ones in semantic/metrics.yaml: revenue is
total minus tax on completed orders; units are quantities on completed orders.
"""

from __future__ import annotations

import datetime as dt
import time
from typing import Any

from app.core.context import Caller
from app.store.analytics import QueryResult, _column_type, _plain
from app.store.postgres import Postgres

MAX_ROWS = 500


async def _read(
    pg: Postgres, caller: Caller, sql: str, params: dict[str, Any], limit: int = MAX_ROWS
) -> QueryResult:
    started = time.perf_counter()
    async with pg.tenant_session(caller) as conn:
        cursor = await conn.execute(sql, params)
        fetched = await cursor.fetchmany(limit + 1)
        description = cursor.description or []
    rows = fetched[:limit]
    return QueryResult(
        columns=[{"name": c.name, "type": _column_type(c.name, c.type_code)} for c in description],
        rows=[{key: _plain(value) for key, value in row.items()} for row in rows],
        row_count=len(rows),
        truncated=len(fetched) > limit,
        duration_ms=round((time.perf_counter() - started) * 1000),
    )


async def sales_summary(pg: Postgres, caller: Caller, start: dt.date, end: dt.date) -> QueryResult:
    return await _read(
        pg,
        caller,
        """
        SELECT COALESCE(ROUND(SUM(o.total_amount - o.tax_amount), 2), 0) AS revenue,
               COUNT(*) AS orders,
               ROUND(SUM(o.total_amount - o.tax_amount) / NULLIF(COUNT(*), 0), 2)
                   AS average_order_value,
               (SELECT COALESCE(SUM(i.quantity), 0)
                  FROM order_items i JOIN orders io ON io.id = i.order_id
                 WHERE io.status = 'completed'
                   AND i.order_date >= %(start)s AND i.order_date <= %(end)s) AS units_sold
          FROM orders o
         WHERE o.status = 'completed'
           AND o.order_date >= %(start)s AND o.order_date <= %(end)s
        """,
        {"start": start, "end": end},
    )


async def sales_trend(pg: Postgres, caller: Caller, start: dt.date, end: dt.date) -> QueryResult:
    return await _read(
        pg,
        caller,
        """
        SELECT o.order_date AS day,
               ROUND(SUM(o.total_amount - o.tax_amount), 2) AS revenue,
               COUNT(*) AS orders
          FROM orders o
         WHERE o.status = 'completed'
           AND o.order_date >= %(start)s AND o.order_date <= %(end)s
         GROUP BY o.order_date
         ORDER BY o.order_date
        """,
        {"start": start, "end": end},
    )


async def revenue_by_outlet(
    pg: Postgres, caller: Caller, start: dt.date, end: dt.date
) -> QueryResult:
    # every outlet the caller may see, also one with no sales in the period
    return await _read(
        pg,
        caller,
        """
        SELECT t.name AS outlet,
               COALESCE(ROUND(SUM(o.total_amount - o.tax_amount), 2), 0) AS revenue,
               COUNT(o.id) AS orders
          FROM outlets t
          LEFT JOIN orders o
                 ON o.outlet_id = t.id AND o.status = 'completed'
                AND o.order_date >= %(start)s AND o.order_date <= %(end)s
         GROUP BY t.id, t.name
         ORDER BY revenue DESC, t.name
        """,
        {"start": start, "end": end},
    )


async def top_products(
    pg: Postgres, caller: Caller, start: dt.date, end: dt.date, *, by: str, limit: int
) -> QueryResult:
    order = {"units": "units_sold DESC, revenue DESC", "revenue": "revenue DESC, units_sold DESC"}[
        by
    ]  # one of two constants written here; never text from outside
    return await _read(
        pg,
        caller,
        f"""
        SELECT p.name AS product, c.name AS category,
               SUM(i.quantity) AS units_sold,
               ROUND(SUM(i.line_total), 2) AS revenue
          FROM order_items i
          JOIN orders o ON o.id = i.order_id
          JOIN products p ON p.id = i.product_id
          JOIN categories c ON c.id = p.category_id
         WHERE o.status = 'completed'
           AND i.order_date >= %(start)s AND i.order_date <= %(end)s
         GROUP BY p.id, p.name, c.name
         ORDER BY {order}, p.name
         LIMIT %(limit)s
        """,  # noqa: S608 - `order` is chosen from the two constants above
        {"start": start, "end": end, "limit": limit},
        limit,
    )


async def inventory_status(
    pg: Postgres, caller: Caller, *, only_low: bool, limit: int
) -> QueryResult:
    return await _read(
        pg,
        caller,
        """
        SELECT p.name AS product, t.name AS outlet,
               l.quantity_on_hand AS on_hand, l.reorder_level,
               GREATEST(l.reorder_level - l.quantity_on_hand, 0) AS shortage,
               (l.quantity_on_hand <= l.reorder_level) AS low_stock
          FROM inventory_levels l
          JOIN products p ON p.id = l.product_id
          JOIN outlets t ON t.id = l.outlet_id
         WHERE p.is_active
           AND (NOT %(only_low)s OR l.quantity_on_hand <= l.reorder_level)
         ORDER BY shortage DESC, p.name, t.name
         LIMIT %(limit)s
        """,
        {"only_low": only_low, "limit": limit + 1},
        limit,
    )


async def inventory_summary(pg: Postgres, caller: Caller) -> QueryResult:
    return await _read(
        pg,
        caller,
        """
        SELECT COUNT(*) AS stock_lines,
               COUNT(*) FILTER (WHERE l.quantity_on_hand <= l.reorder_level) AS low_stock_lines,
               COUNT(*) FILTER (WHERE l.quantity_on_hand <= 0) AS out_of_stock_lines,
               COUNT(DISTINCT l.product_id)
                   FILTER (WHERE l.quantity_on_hand <= l.reorder_level) AS low_stock_products
          FROM inventory_levels l
          JOIN products p ON p.id = l.product_id
         WHERE p.is_active
        """,
        {},
    )


async def customer_summary(
    pg: Postgres, caller: Caller, start: dt.date, end: dt.date
) -> QueryResult:
    return await _read(
        pg,
        caller,
        """
        SELECT (SELECT COUNT(*) FROM customer_visits v
                 WHERE v.visit_date >= %(start)s AND v.visit_date <= %(end)s) AS visits,
               (SELECT COUNT(DISTINCT v.customer_id) FROM customer_visits v
                 WHERE v.visit_date >= %(start)s AND v.visit_date <= %(end)s)
                   AS visiting_customers,
               (SELECT COUNT(*) FROM customers c
                 WHERE c.joined_on >= %(start)s AND c.joined_on <= %(end)s) AS new_customers,
               (SELECT COUNT(DISTINCT o.customer_id) FROM orders o
                 WHERE o.status = 'completed'
                   AND o.order_date >= %(start)s AND o.order_date <= %(end)s)
                   AS buying_customers
        """,
        {"start": start, "end": end},
    )


async def visits_by_segment(
    pg: Postgres, caller: Caller, start: dt.date, end: dt.date
) -> QueryResult:
    return await _read(
        pg,
        caller,
        """
        SELECT c.segment, COUNT(*) AS visits, COUNT(DISTINCT v.customer_id) AS visiting_customers
          FROM customer_visits v
          JOIN customers c ON c.id = v.customer_id
         WHERE v.visit_date >= %(start)s AND v.visit_date <= %(end)s
         GROUP BY c.segment
         ORDER BY visits DESC, c.segment
        """,
        {"start": start, "end": end},
    )


async def product_information(
    pg: Postgres, caller: Caller, *, name: str, limit: int
) -> QueryResult:
    # the pattern is a bind parameter; % and _ in the user's text are matched literally
    escaped = name.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return await _read(
        pg,
        caller,
        """
        SELECT p.name AS product, p.sku, c.name AS category, p.unit, p.unit_price,
               p.is_active AS active
          FROM products p
          JOIN categories c ON c.id = p.category_id
         WHERE p.name ILIKE %(pattern)s OR p.sku ILIKE %(pattern)s
         ORDER BY p.name
         LIMIT %(limit)s
        """,
        {"pattern": f"%{escaped}%", "limit": limit + 1},
        limit,
    )


async def money_out(pg: Postgres, caller: Caller, start: dt.date, end: dt.date) -> QueryResult:
    return await _read(
        pg,
        caller,
        """
        SELECT (SELECT COALESCE(ROUND(SUM(e.amount), 2), 0) FROM expenses e
                 WHERE e.expense_date >= %(start)s AND e.expense_date <= %(end)s) AS expenses,
               (SELECT COALESCE(ROUND(SUM(po.total_amount), 2), 0) FROM purchase_orders po
                 WHERE po.status = 'received'
                   AND po.ordered_on >= %(start)s AND po.ordered_on <= %(end)s)
                   AS purchases_received_amount
        """,
        {"start": start, "end": end},
    )
