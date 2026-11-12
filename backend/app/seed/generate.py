"""Builds the demo rows in memory. Pure: no database, so it is testable on its own."""

from __future__ import annotations

import random
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

NAMESPACE = uuid.UUID("6f1d3c1e-5b7a-4c0e-9f43-2a1b0c9d8e7f")
TAX_RATE = Decimal("0.05")
Row = tuple[Any, ...]


def uid(*parts: object) -> uuid.UUID:
    """A stable id from a name, so tests and docs can refer to rows."""
    return uuid.uuid5(NAMESPACE, ":".join(str(p) for p in parts))


def money(value: float | Decimal) -> Decimal:
    return Decimal(value).quantize(Decimal("0.01"))


@dataclass(frozen=True)
class Company:
    slug: str
    name: str
    # (name, code, city, region, daily orders): the first outlet is the busiest on purpose
    outlets: list[tuple[str, str, str, str, int]]
    # category -> [(product, price, cost, weight)]
    catalogue: dict[str, list[tuple[str, int, int, int]]]
    channels: list[tuple[str, int]]
    users: list[tuple[str, str, str, list[int] | None]]  # email, name, role, outlet indexes


BREWBITE = Company(
    slug="brewbite",
    name="Brew & Bite Cafés",
    outlets=[
        ("Indiranagar", "BLR-IND", "Bengaluru", "South", 16),
        ("Koramangala", "BLR-KOR", "Bengaluru", "South", 13),
        ("Whitefield", "BLR-WHF", "Bengaluru", "South", 10),
        ("Mysuru Central", "MYS-CEN", "Mysuru", "South", 8),
        ("Chennai Adyar", "CHN-ADY", "Chennai", "South", 6),
    ],
    catalogue={
        "Coffee": [
            ("Cappuccino", 180, 55, 10),
            ("Latte", 190, 60, 9),
            ("Espresso", 120, 35, 5),
            ("Cold Brew", 210, 65, 6),
            ("Filter Coffee", 90, 25, 8),
        ],
        "Tea": [
            ("Masala Chai", 80, 20, 7),
            ("Green Tea", 110, 30, 3),
            ("Iced Lemon Tea", 140, 40, 4),
        ],
        "Bakery": [
            ("Butter Croissant", 150, 60, 6),
            ("Blueberry Muffin", 160, 65, 5),
            ("Banana Bread", 130, 50, 3),
            ("Cinnamon Roll", 170, 70, 1),
        ],
        "Sandwiches": [
            ("Paneer Tikka Sandwich", 260, 110, 5),
            ("Chicken Club Sandwich", 320, 150, 4),
            ("Veg Grilled Sandwich", 220, 90, 4),
        ],
        "Desserts": [
            ("Chocolate Brownie", 190, 75, 5),
            ("Cheesecake Slice", 260, 110, 3),
            ("Tiramisu", 280, 125, 1),
        ],
        "Beverages": [
            ("Bottled Water", 40, 12, 6),
            ("Fresh Lime Soda", 110, 30, 4),
            ("Mango Smoothie", 230, 85, 3),
        ],
    },
    channels=[("dine_in", 50), ("takeaway", 25), ("delivery", 20), ("online", 5)],
    users=[
        ("admin@brewbite.example", "Asha Rao", "admin", None),
        ("analyst@brewbite.example", "Vikram Shah", "analyst", None),
        ("viewer@brewbite.example", "Neha Iyer", "viewer", [0, 1]),
    ],
)

URBANMART = Company(
    slug="urbanmart",
    name="UrbanMart Retail",
    outlets=[
        ("Pune Kothrud", "PUN-KOT", "Pune", "West", 14),
        ("Pune Baner", "PUN-BAN", "Pune", "West", 11),
        ("Mumbai Andheri", "MUM-AND", "Mumbai", "West", 9),
        ("Nashik Road", "NSK-RD", "Nashik", "West", 6),
    ],
    catalogue={
        "Groceries": [
            ("Basmati Rice 5kg", 650, 520, 8),
            ("Toor Dal 1kg", 180, 150, 9),
            ("Sunflower Oil 1L", 160, 138, 9),
            ("Wheat Flour 10kg", 480, 410, 7),
        ],
        "Snacks": [
            ("Potato Chips", 30, 21, 10),
            ("Salted Peanuts", 60, 42, 6),
            ("Digestive Biscuits", 90, 66, 7),
            ("Dark Chocolate Bar", 150, 105, 1),
        ],
        "Beverages": [
            ("Bottled Water", 20, 9, 10),
            ("Orange Juice 1L", 130, 95, 6),
            ("Cola 750ml", 45, 31, 8),
        ],
        "Household": [
            ("Dish Wash Liquid", 110, 78, 6),
            ("Laundry Detergent 2kg", 320, 245, 5),
            ("Floor Cleaner", 140, 99, 4),
        ],
        "Personal Care": [
            ("Bath Soap 4-pack", 160, 118, 7),
            ("Shampoo 340ml", 290, 205, 5),
            ("Toothpaste", 95, 66, 8),
            ("Beard Oil", 350, 240, 1),
        ],
        "Dairy": [("Milk 1L", 68, 60, 10), ("Curd 400g", 45, 36, 7), ("Paneer 200g", 95, 76, 5)],
    },
    channels=[("dine_in", 0), ("takeaway", 75), ("delivery", 15), ("online", 10)],
    users=[
        ("admin@urbanmart.example", "Rohan Kulkarni", "admin", None),
        ("viewer@urbanmart.example", "Sneha Patil", "viewer", [3]),
    ],
)

COMPANIES = [BREWBITE, URBANMART]
SEGMENTS = [("new", 30), ("regular", 50), ("vip", 12), ("corporate", 8)]
PAYMENTS = [("upi", 50), ("card", 25), ("cash", 20), ("wallet", 5)]
STATUSES = [("completed", 94), ("cancelled", 4), ("refunded", 2)]
EXPENSES = [
    ("Rent", 90000),
    ("Salaries", 160000),
    ("Utilities", 22000),
    ("Marketing", 15000),
    ("Maintenance", 9000),
]
CUSTOMERS_PER_COMPANY = 300


@dataclass
class Data:
    """Rows per table, in an order that respects the foreign keys."""

    tables: dict[str, list[Row]] = field(default_factory=dict)

    def add(self, table: str, row: Row) -> None:
        self.tables.setdefault(table, []).append(row)


def pick(rng: random.Random, weighted: list[tuple[Any, int]]) -> Any:
    return rng.choices([w[0] for w in weighted], weights=[w[1] for w in weighted])[0]


def generate(as_of: date, days: int = 120, seed: int = 42) -> Data:
    """Everything up to and including the day before `as_of`."""
    data = Data()
    for company in COMPANIES:
        _company(data, company, as_of, days, random.Random(f"{seed}:{company.slug}"))
    data.add(
        "app.users",
        (uid("user", "root"), None, "root@bi.example", "Platform Admin", "super_admin", True),
    )
    return data


def _company(data: Data, c: Company, as_of: date, days: int, rng: random.Random) -> None:
    tz = ZoneInfo("Asia/Kolkata")
    tenant = uid("tenant", c.slug)
    start = as_of - timedelta(days=days)
    data.add("app.tenants", (tenant, c.name, c.slug, "INR", "Asia/Kolkata"))

    outlets = []
    for name, code, city, region, daily in c.outlets:
        oid = uid(c.slug, "outlet", code)
        outlets.append((oid, daily))
        data.add(
            "biz.outlets", (oid, tenant, name, code, city, region, start - timedelta(days=400))
        )

    for email, full_name, role, limited in c.users:
        user = uid("user", email)
        data.add("app.users", (user, tenant, email, full_name, role, limited is None))
        for index in limited or []:
            data.add("app.user_outlets", (tenant, user, outlets[index][0]))

    products = []  # (id, price, cost, weight)
    for category, items in c.catalogue.items():
        cid = uid(c.slug, "category", category)
        data.add("biz.categories", (cid, tenant, category))
        for n, (name, list_price, list_cost, weight) in enumerate(items, 1):
            pid = uid(c.slug, "product", name)
            sku = f"{category[:3].upper()}-{n:03d}"
            products.append((pid, Decimal(list_price), Decimal(list_cost), weight))
            data.add("biz.products", (pid, tenant, cid, sku, name, "each", list_price, list_cost))

    customers = []
    cities = sorted({o[2] for o in c.outlets})
    for n in range(CUSTOMERS_PER_COMPANY):
        cust = uid(c.slug, "customer", n)
        customers.append(cust)
        joined = start - timedelta(days=300) + timedelta(days=rng.randrange(days + 300))
        data.add(
            "biz.customers",
            (
                cust,
                tenant,
                f"Customer {c.slug}-{n:04d}",
                f"+91-90000-{n:05d}",
                f"customer{n}@{c.slug}.example",
                pick(rng, SEGMENTS),
                rng.choice(cities),
                joined,
            ),
        )

    order_no = 0
    for offset in range(days):
        day = start + timedelta(days=offset)
        weekend = 1.35 if day.weekday() >= 5 else 1.0
        for oid, daily in outlets:
            for _ in range(max(1, round(rng.gauss(daily * weekend, daily * 0.15)))):
                order_no += 1
                order = uid(c.slug, "order", order_no)
                at = datetime.combine(day, time(rng.randrange(8, 22), rng.randrange(60)), tz)
                customer = rng.choice(customers) if rng.random() < 0.6 else None
                channel = pick(rng, c.channels)
                status = pick(rng, STATUSES)
                lines = rng.sample(products, k=rng.choice([1, 1, 2, 2, 3, 4]))
                weights = [p[3] for p in products]
                lines = list(
                    {p[0]: p for p in rng.choices(products, weights=weights, k=len(lines))}.values()
                )
                subtotal = Decimal(0)
                for n, (pid, price, cost, _) in enumerate(lines):
                    qty = rng.choice([1, 1, 1, 2, 2, 3])
                    line = price * qty
                    subtotal += line
                    data.add(
                        "biz.order_items",
                        (
                            uid(c.slug, "item", order_no, n),
                            tenant,
                            order,
                            oid,
                            day,
                            pid,
                            qty,
                            price,
                            cost,
                            0,
                            line,
                        ),
                    )
                discount = money(subtotal * Decimal(rng.choice([0, 0, 0, 0, 5, 10])) / 100)
                tax = money((subtotal - discount) * TAX_RATE)
                data.add(
                    "biz.orders",
                    (
                        order,
                        tenant,
                        oid,
                        customer,
                        f"{c.slug[:2].upper()}-{order_no:06d}",
                        at,
                        day,
                        channel,
                        status,
                        pick(rng, PAYMENTS),
                        subtotal,
                        discount,
                        tax,
                        subtotal - discount + tax,
                    ),
                )
                if customer:
                    source = {"delivery": "delivery", "online": "online_order"}.get(
                        channel, "walk_in"
                    )
                    data.add(
                        "biz.customer_visits",
                        (
                            uid(c.slug, "visit", order_no),
                            tenant,
                            oid,
                            customer,
                            day,
                            at,
                            source,
                            rng.choice([1, 1, 2, 2, 3, 4]),
                            order,
                        ),
                    )
            # a few visits that end without a purchase
            for n in range(rng.choice([0, 1, 1, 2])):
                at = datetime.combine(day, time(rng.randrange(9, 21), rng.randrange(60)), tz)
                data.add(
                    "biz.customer_visits",
                    (
                        uid(c.slug, "visit", "np", oid, offset, n),
                        tenant,
                        oid,
                        rng.choice(customers),
                        day,
                        at,
                        rng.choice(["walk_in", "reservation"]),
                        rng.choice([1, 2, 2, 3]),
                        None,
                    ),
                )

    suppliers = []
    for n in range(4):
        sid = uid(c.slug, "supplier", n)
        suppliers.append(sid)
        data.add(
            "biz.suppliers",
            (
                sid,
                tenant,
                f"{c.name.split()[0]} Supplier {n + 1}",
                f"Contact {n + 1}",
                f"+91-80000-{n:05d}",
                rng.choice(cities),
            ),
        )

    for oid, _ in outlets:
        for index, (pid, _, _, weight) in enumerate(products):
            reorder = 10 + weight * 2
            # every seventh product is below its reorder level, at the first outlet only
            low = index % 7 == 0 and oid == outlets[0][0]
            on_hand = rng.randrange(0, reorder) if low else reorder + rng.randrange(5, 60)
            data.add("biz.inventory_levels", (tenant, oid, pid, on_hand, reorder))
        for n in range(max(1, days // 30)):
            ordered = start + timedelta(days=n * 30 + rng.randrange(5))
            po = uid(c.slug, "po", oid, n)
            total = Decimal(0)
            for k, (pid, _, cost, _) in enumerate(rng.sample(products, k=5)):
                qty = rng.choice([20, 40, 60])
                total += cost * qty
                data.add(
                    "biz.purchase_order_items",
                    (uid(c.slug, "poi", oid, n, k), tenant, po, pid, qty, cost, cost * qty),
                )
                received_at = datetime.combine(ordered + timedelta(days=3), time(10, 0), tz)
                data.add(
                    "biz.stock_movements",
                    (
                        uid(c.slug, "move", oid, n, k),
                        tenant,
                        oid,
                        pid,
                        "purchase",
                        qty,
                        cost,
                        ordered + timedelta(days=3),
                        received_at,
                        None,
                    ),
                )
            data.add(
                "biz.purchase_orders",
                (
                    po,
                    tenant,
                    oid,
                    rng.choice(suppliers),
                    f"PO-{c.slug[:2].upper()}-{uid(po).hex[:8]}",
                    "received",
                    ordered,
                    ordered + timedelta(days=3),
                    total,
                ),
            )

    for name, _ in EXPENSES:
        data.add("biz.expense_categories", (uid(c.slug, "expcat", name), tenant, name))
    month = date(start.year, start.month, 1)
    while month < as_of:
        for oid, daily in outlets:
            for name, base in EXPENSES:
                amount = money(base * (0.6 + daily / 20) * rng.uniform(0.95, 1.05))
                data.add(
                    "biz.expenses",
                    (
                        uid(c.slug, "expense", oid, month, name),
                        tenant,
                        oid,
                        uid(c.slug, "expcat", name),
                        month + timedelta(days=4),
                        amount,
                        None,
                        f"{name} for {month:%B %Y}",
                        "card",
                    ),
                )
        month = date(month.year + month.month // 12, month.month % 12 + 1, 1)
