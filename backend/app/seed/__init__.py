"""The demo data: two companies with outlets, products, customers, orders, visits, stock,
purchases and expenses (docs/design/data-model.md §7).

    python -m app.seed [--as-of 2026-11-01] [--days 120]

Deterministic: the same date and the same number of days give the same rows and ids.
Refused outside development and test, and when BI_SEED_DEMO is off.
"""
