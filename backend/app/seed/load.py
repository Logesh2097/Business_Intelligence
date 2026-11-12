"""Writes the generated rows into PostgreSQL as bi_owner, replacing what was there."""

from __future__ import annotations

from datetime import date

import psycopg

from app.seed.generate import Data, generate

# insert order respects the foreign keys; the columns match the tuples in generate.py
COLUMNS: dict[str, str] = {
    "app.tenants": "id, name, slug, currency, timezone",
    "biz.outlets": "id, tenant_id, name, code, city, region, opened_on",
    "app.users": "id, tenant_id, email, full_name, role, all_outlets",
    "app.user_outlets": "tenant_id, user_id, outlet_id",
    "biz.categories": "id, tenant_id, name",
    "biz.products": "id, tenant_id, category_id, sku, name, unit, unit_price, cost_price",
    "biz.customers": "id, tenant_id, name, phone, email, segment, city, joined_on",
    "biz.orders": "id, tenant_id, outlet_id, customer_id, order_no, ordered_at, order_date, "
    "channel, status, payment_method, subtotal, discount_amount, tax_amount, total_amount",
    "biz.order_items": "id, tenant_id, order_id, outlet_id, order_date, product_id, quantity, "
    "unit_price, unit_cost, discount_amount, line_total",
    "biz.customer_visits": "id, tenant_id, outlet_id, customer_id, visit_date, visited_at, "
    "source, party_size, order_id",
    "biz.suppliers": "id, tenant_id, name, contact_name, phone, city",
    "biz.inventory_levels": "tenant_id, outlet_id, product_id, quantity_on_hand, reorder_level",
    "biz.purchase_orders": "id, tenant_id, outlet_id, supplier_id, po_no, status, ordered_on, "
    "received_on, total_amount",
    "biz.purchase_order_items": "id, tenant_id, purchase_order_id, product_id, quantity, "
    "unit_cost, line_total",
    "biz.stock_movements": "id, tenant_id, outlet_id, product_id, movement_type, quantity, "
    "unit_cost, movement_date, moved_at, note",
    "biz.expense_categories": "id, tenant_id, name",
    "biz.expenses": "id, tenant_id, outlet_id, category_id, expense_date, amount, vendor, "
    "description, payment_method",
}


def load(owner_dsn: str, data: Data) -> dict[str, int]:
    """Replace every row of the demo tables. Returns the row count per table."""
    counts = {}
    with psycopg.connect(owner_dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "TRUNCATE app.query_log, app.user_outlets, app.users, app.tenants, "
            + ", ".join(t for t in COLUMNS if t.startswith("biz."))
            + " CASCADE"
        )
        for table, columns in COLUMNS.items():
            rows = data.tables.get(table, [])
            with cur.copy(f"COPY {table} ({columns}) FROM STDIN") as copy:
                for row in rows:
                    copy.write_row(row)
            counts[table] = len(rows)
        cur.execute("ANALYZE")
    return counts


def seed(owner_dsn: str, as_of: date, days: int = 120) -> dict[str, int]:
    return load(owner_dsn, generate(as_of, days))
