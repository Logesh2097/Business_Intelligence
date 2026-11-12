# Data model

**Status: approved by the owner on 2026-11-11 and built.** `backend/app/store/migrations/0001_init.sql` creates schema `biz` (14 tables), and in schema `app` the tables in use so far: `tenants`, `users`, `user_outlets`, `query_log`. The other `app` tables of §3 (`refresh_tokens`, `login_attempts`, `audit_log`, `llm_usage_daily`) are created by the phases that use them. Row security, the grants and the `set_config` lock of §2 are built and tested against a real PostgreSQL (`tests/integration/test_schema_live.py`). The demo seed is the **small version**: 120 days and about 12,000 orders, with the planted stories S3, S4, S6, S8 and S9 only in simple form; the full-volume seed with every story of §7 is still to do. The MongoDB and Qdrant parts (§5, §6) are not built. The questions of §8 were not answered; the proposal's stated assumptions were taken.

**The entities the owner listed, and where each is in this proposal:**

| Entity | Tables | Section |
|---|---|---|
| Tenants | `app.tenants` | §3 |
| Users | `app.users`, `app.user_outlets` | §3 |
| Outlets | `biz.outlets` | §4 Sales |
| Sales | `biz.orders`, `biz.order_items` | §4 Sales |
| Products | `biz.categories`, `biz.products` | §4 Sales |
| Inventory | `biz.inventory_levels`, `biz.stock_movements` | §4 Inventory |
| Customers | `biz.customers` | §4 Customers |
| Customer visits | `biz.customer_visits` (**new in this revision**) | §4 Customers |
| also proposed earlier | suppliers and purchase orders, expenses | §4 |

**Questions for the owner before any table is created** are at the end, §8.

**Conventions.** `id uuid primary key default gen_random_uuid()`. `created_at timestamptz not null default now()`. Money is `numeric(14,2)`, quantities `numeric(14,3)`. Names are `snake_case`, tables plural. Status and type columns are `text` with a `CHECK` list, so the allowed values are visible in the schema the model reads. **Rules that must never be broken live in the database**: constraints, composite keys, row security.

## 1. Tenancy

A **tenant** is one company. Everything a company owns carries `tenant_id`.

```
tenant ─┬─ users ── user_outlets (which outlets a user may see)
        ├─ outlets
        ├─ categories ── products
        ├─ customers ── customer_visits
        ├─ orders ── order_items
        ├─ suppliers ── purchase_orders ── purchase_order_items
        ├─ inventory_levels      stock_movements
        └─ expense_categories ── expenses
```

Three rules make "company A can never reach company B" a fact of the database and not only of the code:

1. **Every `biz` table has `tenant_id not null`** and a unique key `(tenant_id, id)`.
2. **A child points at its parent with a composite key** `(tenant_id, parent_id) references parent (tenant_id, id)`. A row that names another company's row cannot be written at all.
3. **Row security is on, and forced, for every `biz` table** (§2).

## 2. Database roles and row security

| Role | Used by | May do |
|---|---|---|
| `bi_owner` | migrations and the seed only | owns every object; row security does not bind it. Its password is not given to the running API in the cloud |
| `bi_app` | the API | read and write `app`; read `biz` (for admin screens such as the outlet list) under row security |
| `bi_readonly` | **the query tool only**, for AI-written SQL | `USAGE` on `biz`, `SELECT` on listed columns of `biz` tables; nothing on `app`; cannot create anything; cannot call `set_config` |

**As built:** the two session values are read through `biz.current_tenant()` and `biz.outlet_visible(outlet_id)`, which the policies call. They treat an empty value like a missing one, because PostgreSQL leaves a custom setting as `''` (not `NULL`) on a pooled connection after a transaction that set it. `schema_migrations` is in `public`, written by `store/migrate.py`.

**How the company reaches the database.** Before a statement, the code opens a transaction and sets two session values that last only for that transaction:

```sql
BEGIN READ ONLY;
SET LOCAL statement_timeout = '15s';
SET LOCAL app.tenant_id  = '<uuid from the signed-in user>';
SET LOCAL app.outlet_ids = '*';            -- or 'uuid,uuid' for a user limited to some outlets
<the one checked SELECT>
ROLLBACK;
```

Both values are written by our code from the session; they are validated as UUIDs before they are placed in the `SET` (which cannot take bind parameters).

**The policies.**

```sql
-- every biz table
USING (tenant_id = current_setting('app.tenant_id', true)::uuid)

-- and, on tables that have outlet_id
AND (current_setting('app.outlet_ids', true) = '*'
     OR outlet_id = ANY (string_to_array(current_setting('app.outlet_ids', true), ',')::uuid[]))
```

- A missing setting is `NULL`, which matches nothing: **no company set, no rows** (fail closed).
- `ALTER TABLE … FORCE ROW LEVEL SECURITY` so that even the table's owner is bound when it connects as a normal session.
- **The model's SQL cannot change the company.** Three locks: the guard refuses any function outside its allow-list (`set_config` and `current_setting` are not on it) and any statement that is not a single `SELECT`; `EXECUTE` on `set_config` is revoked from `PUBLIC` and granted back only to `bi_app` and `bi_owner`; and a test tries it as `bi_readonly` and expects a refusal.

**Outlet scope.** Tables with `outlet_id` (orders, order_items, inventory_levels, stock_movements, purchase_orders, expenses, outlets itself by `id`) are limited to the user's outlets. Company-wide tables (categories, products, customers, suppliers, expense_categories) are limited by company only.

**`app` tables** are not readable by `bi_readonly` at all. They are reached only through hand-written store functions that always take the company from the session; the isolation test suite covers each one (`testing.md` §4).

## 3. Schema `app` (the product's own data)

| Table | Columns | Notes |
|---|---|---|
| `tenants` | `id`, `name`, `slug` (unique), `currency` (default `INR`), `timezone` (default `Asia/Kolkata`), `is_active`, `created_at` | a deactivated company cannot sign in |
| `users` | `id`, `tenant_id` (null only for `super_admin`), `email` (unique, lower-case), `password_hash`, `full_name`, `role` (`super_admin` \| `admin` \| `analyst` \| `viewer`), `all_outlets` bool, `is_active`, `token_version` int, `last_login_at`, `created_at` | `CHECK ((role = 'super_admin') = (tenant_id IS NULL))`. Raising `token_version` ends every access token of the user |
| `user_outlets` | `tenant_id`, `user_id`, `outlet_id`; primary key all three | used when `all_outlets` is false; composite keys to `users` and `biz.outlets` |
| `refresh_tokens` | `id`, `user_id`, `token_hash` (SHA-256, unique), `expires_at`, `revoked_at`, `replaced_by`, `user_agent`, `ip`, `created_at` | rotation and reuse detection (`security.md` §2) |
| `login_attempts` | `id`, `email`, `ip`, `succeeded`, `created_at` | the login rate limit counts these |
| `audit_log` | `id`, `tenant_id`, `user_id`, `action`, `detail jsonb`, `ip`, `created_at` | who changed what: users, outlet access, documents, reports, examples |
| `query_log` | `id`, `tenant_id`, `user_id`, `conversation_id`, `message_id`, `question`, `sql`, `status` (`ok` \| `rejected` \| `error` \| `timeout`), `reason`, `row_count`, `duration_ms`, `created_at` | **every** piece of AI-written SQL, run or refused |
| `llm_usage_daily` | `tenant_id`, `day`, `questions`, `input_tokens`, `output_tokens`; primary key (`tenant_id`, `day`) | the daily limits read this |
| `schema_migrations` | `version`, `applied_at` | written by the migration runner |

## 4. Schema `biz` (the data people ask about)

Every table also has `tenant_id uuid not null` and `UNIQUE (tenant_id, id)`; they are not repeated in each row below. Columns marked **private** are not granted to `bi_readonly`, so the model can neither select them nor see them in the schema it is given.

### Sales

| Table | Columns | Notes |
|---|---|---|
| `outlets` | `id`, `name`, `code`, `city`, `region`, `opened_on`, `is_active` | `UNIQUE (tenant_id, code)` |
| `categories` | `id`, `name` | product categories |
| `products` | `id`, `category_id`, `sku`, `name`, `unit` (`each`, `kg`, `litre`…), `unit_price`, `cost_price`, `is_active`, `created_at` | `UNIQUE (tenant_id, sku)` |
| `orders` | `id`, `outlet_id`, `customer_id` (null for a walk-in), `order_no`, `ordered_at timestamptz`, `order_date date`, `channel` (`dine_in` \| `takeaway` \| `delivery` \| `online`), `status` (`completed` \| `cancelled` \| `refunded`), `payment_method` (`cash` \| `card` \| `upi` \| `wallet`), `subtotal`, `discount_amount`, `tax_amount`, `total_amount` | `order_date` is the date in the company's time zone, stored so that "last month" needs no time-zone arithmetic. Index `(tenant_id, order_date)`, `(tenant_id, outlet_id, order_date)` |
| `order_items` | `id`, `order_id`, `outlet_id`, `order_date`, `product_id`, `quantity`, `unit_price`, `unit_cost`, `discount_amount`, `line_total` | `outlet_id` and `order_date` are copied from the order on purpose: the outlet policy needs no join, and "sales by product by month" is a one-table query. `unit_cost` is the cost at the time of sale. Index `(tenant_id, product_id, order_date)` |

### Customers

| Table | Columns | Notes |
|---|---|---|
| `customers` | `id`, `name` **private**, `phone` **private**, `email` **private**, `segment` (`new` \| `regular` \| `vip` \| `corporate`), `city`, `joined_on` | the model can count, group and join customers; it cannot read who they are |
| `customer_visits` | `id`, `outlet_id`, `customer_id`, `visit_date`, `visited_at timestamptz`, `source` (`walk_in` \| `reservation` \| `online_order` \| `delivery`), `party_size` (may be empty), `order_id` (empty when the visit had no purchase) | one row each time a known customer comes to an outlet. Limited by outlet like orders. `UNIQUE (tenant_id, order_id)` so one order is one visit at most. Index `(tenant_id, customer_id, visit_date)`, `(tenant_id, outlet_id, visit_date)` |

**Why visits are their own table and not derived from orders:** an order tells us a purchase happened; a visit can happen with no purchase (a reservation that ordered nothing, a browse in a store), and several orders can belong to one visit. With the table, the assistant can answer "how often do customers come back", "how many visits end without a purchase", "which outlet has the most repeat visitors". Without a source of visit data it would only repeat what orders already say (§8, question 1).

### Inventory and purchasing

| Table | Columns | Notes |
|---|---|---|
| `suppliers` | `id`, `name`, `contact_name` **private**, `phone` **private**, `city`, `is_active` | |
| `inventory_levels` | `outlet_id`, `product_id`, `quantity_on_hand`, `reorder_level`, `updated_at` | primary key (`tenant_id`, `outlet_id`, `product_id`): the stock now |
| `stock_movements` | `id`, `outlet_id`, `product_id`, `movement_type` (`purchase` \| `sale` \| `wastage` \| `transfer_in` \| `transfer_out` \| `adjustment`), `quantity` (signed: in is positive), `unit_cost`, `movement_date`, `moved_at`, `note` | the history of stock. Index `(tenant_id, product_id, movement_date)` |
| `purchase_orders` | `id`, `outlet_id`, `supplier_id`, `po_no`, `status` (`draft` \| `ordered` \| `received` \| `cancelled`), `ordered_on`, `received_on`, `total_amount` | |
| `purchase_order_items` | `id`, `purchase_order_id`, `product_id`, `quantity`, `unit_cost`, `line_total` | |

### Expenses

| Table | Columns | Notes |
|---|---|---|
| `expense_categories` | `id`, `name` | rent, salaries, utilities, marketing, maintenance… |
| `expenses` | `id`, `outlet_id`, `category_id`, `expense_date`, `amount`, `vendor`, `description`, `payment_method` | Index `(tenant_id, expense_date)` |

Every table and column gets a `COMMENT`, and the same words appear in `semantic/tables.yaml` (a test keeps the two in step). The meaning of *revenue*, *profit* and the other business words is not in the schema; it is in `semantic-layer.md`.

### Relationships

Every relationship is inside one company: the foreign key is `(tenant_id, …)` on both sides.

| From | To | Kind | Meaning |
|---|---|---|---|
| `app.users` | `app.tenants` | many to one | a user belongs to one company (the platform admin to none) |
| `app.user_outlets` | `app.users`, `biz.outlets` | many to many | the outlets a limited user may see |
| `outlets` | tenant | many to one | a company's branches |
| `products` | `categories` | many to one | |
| `orders` | `outlets` | many to one | where the sale happened |
| `orders` | `customers` | many to one, optional | empty for a walk-in |
| `order_items` | `orders`, `products` | many to one each | the lines of a bill |
| `customer_visits` | `customers`, `outlets` | many to one each | who came, and where |
| `customer_visits` | `orders` | one to one, optional | the purchase made on that visit, if any |
| `inventory_levels` | `outlets`, `products` | one row per pair | the stock now |
| `stock_movements` | `outlets`, `products` | many to one each | the history of stock |
| `purchase_orders` | `outlets`, `suppliers` | many to one each | |
| `purchase_order_items` | `purchase_orders`, `products` | many to one each | |
| `expenses` | `outlets`, `expense_categories` | many to one each | |

## 5. MongoDB

Database `bi`. **Every document carries `tenant_id`, every query filters on it, and every index starts with it.** The store functions take the company as their first argument; there is no function that reads a collection without one.

| Collection | A document | Indexes |
|---|---|---|
| `conversations` | `_id`, `tenant_id`, `user_id`, `title`, `created_at`, `updated_at`, `archived` | `(tenant_id, user_id, updated_at desc)` |
| `messages` | `_id`, `tenant_id`, `conversation_id`, `user_id`, `role` (`user` \| `assistant`), `content` (the text), and for an assistant message: `route`, `answer` (below), `trace[]`, `usage` (`input_tokens`, `output_tokens`), `status` (`ok` \| `clarify` \| `error`), `error`, `feedback` (`rating`, `comment`, `at`), `created_at` | `(tenant_id, conversation_id, created_at)` |
| `reports` | `_id`, `tenant_id`, `owner_id`, `title`, `description`, `question`, `sql`, `chart`, `snapshot` (`columns`, `rows`, `generated_at`), `shared` bool, `source_message_id`, `created_at`, `updated_at` | `(tenant_id, owner_id)`, `(tenant_id, shared)` |
| `documents` | `_id`, `tenant_id`, `uploaded_by`, `filename`, `title`, `mime`, `size`, `sha256`, `storage_path`, `status` (`uploaded` \| `processing` \| `ready` \| `failed`), `error`, `chunk_count`, `created_at` | `(tenant_id, created_at desc)`, unique `(tenant_id, sha256)` |
| `examples` | `_id`, `tenant_id`, `question`, `sql`, `status` (`pending` \| `approved` \| `rejected`), `source_message_id`, `reviewed_by`, `created_at` | `(tenant_id, status)` |

**The `answer` of an assistant message:**

```json
{
  "text": "Revenue last month was ₹18,42,300, up 6.1% on September.",
  "tables": [{ "title": "Revenue by outlet", "columns": [{"name": "outlet", "type": "text"}, {"name": "revenue", "type": "money"}], "rows": [["Indiranagar", 512300.00]], "truncated": false }],
  "chart": { "type": "bar", "title": "Revenue by outlet", "x": "outlet", "y": ["revenue"], "unit": "money" },
  "recommendations": [{ "text": "…", "based_on": "…" }],
  "queries": [{ "sql": "SELECT …", "row_count": 5, "duration_ms": 41 }],
  "sources": [{ "document_id": "…", "filename": "ops-notes-october.pdf", "page": 2, "quote": "…" }],
  "scope_note": "Limited to your outlets: Indiranagar, Koramangala."
}
```

A stored table keeps at most 200 rows (the screen never shows more); the query is kept, so the full result can be fetched again for export. `queries` is removed from what a viewer receives (`security.md` §3).

## 6. Qdrant

Vectors are 1,536 numbers, cosine distance. **Every point carries `tenant_id`, and every search has a `tenant_id` filter added by the tool, not by the caller.**

| Collection | A point | Payload indexes |
|---|---|---|
| `doc_chunks` | one chunk of an uploaded document. Payload: `tenant_id`, `document_id`, `filename`, `title`, `page`, `chunk_index`, `text` | `tenant_id` (keyword, marked as the tenant key), `document_id` |
| `sql_examples` | one example question with its SQL. Payload: `tenant_id` (a company's id, or `global` for the examples shipped in the repository), `question`, `sql`, `source` (`repo` \| `learned`) | `tenant_id` |

Point ids are deterministic (`uuid5` of document id + chunk index, or of the example's id), so re-ingesting replaces instead of duplicating. Deleting a document deletes its points by a `document_id` + `tenant_id` filter.

## 7. The demo seed

`python -m app.seed --as-of 2026-11-01` creates two companies, deterministic for a given date and random seed, so tests and the golden question set always see the same numbers.

| | Brew & Bite Cafés (`brewbite`) | UrbanMart Retail (`urbanmart`) |
|---|---|---|
| Outlets | 5 (Bengaluru ×3, Mysuru, Chennai) | 4 (Pune ×2, Mumbai, Nashik) |
| Categories / products | 8 / about 80 | 12 / about 200 |
| Customers | about 6,000 | about 9,000 |
| Orders, 12 months | about 100,000 | about 100,000 |
| Suppliers / purchase orders | 12 / about 600 | 25 / about 900 |
| Expenses | monthly rent, salaries, utilities, marketing, maintenance per outlet | the same |
| Documents | promotions calendar, refund policy, monthly operations notes | pricing policy, supplier terms, monthly operations notes |

**Users** (password from `BI_SEED_PASSWORD`):

| Email | Role | Outlets |
|---|---|---|
| `root@bi.example` | super_admin | — |
| `admin@brewbite.example` | admin | all |
| `analyst@brewbite.example` | analyst | all |
| `viewer@brewbite.example` | viewer | 2 of 5 |
| `admin@urbanmart.example` | admin | all |
| `viewer@urbanmart.example` | viewer | 1 of 4 |

**Shape of the data:** weekly rhythm (weekends higher), a mild upward trend, festival peaks, channel mix that differs by outlet, a long tail of products.

**Stories planted on purpose** (the golden questions check that the assistant finds them):

| # | Story | What should surface |
|---|---|---|
| S1 | Brew & Bite: one outlet is closed for nine days in the last full month (renovation), and the operations notes say so | "Why did sales decrease last month?" names the outlet, the size of the fall and the note |
| S2 | Brew & Bite: the best-selling product is out of stock at two outlets for a week in the same month | the same question lists the stock-out as a second cause |
| S3 | Both: about 10% of products sell fewer than 5 units a month | "Which products have low sales?" |
| S4 | Both: one outlet is clearly the top by revenue, a different one by profit margin | "Which outlet has the highest revenue?" and "…the best margin?" give different answers |
| S5 | UrbanMart: marketing expenses triple in one month with no matching rise in sales | "Where did expenses rise?" and a recommendation |
| S6 | UrbanMart: 15 products sit below their reorder level | "Which items need reordering?" |
| S7 | Both: a group of `regular` customers stops ordering three months before the as-of date | "How many customers have not ordered in 90 days?" |
| S8 | Isolation: the two companies share some product names with different prices and sales | any answer that mixed companies would show the wrong figure |
| S9 | Brew & Bite: one outlet has many visits that end without a purchase | "Which outlet has the most visits with no purchase?" (only if `customer_visits` is approved) |

## 8. Questions for the owner before any table is created

| # | Question | What the proposal assumes until answered |
|---|---|---|
| 1 | **Where will customer-visit data come from** in real use: a reservation system, a loyalty app check-in, the point of sale? | visits are generated in the demo data: one for each order that has a customer, plus some with no purchase |
| 2 | Is a **visit without a known customer** worth recording (footfall counts)? | no: `customer_id` is required; anonymous footfall would be a separate daily count per outlet |
| 3 | Are **suppliers, purchase orders and expenses** still wanted? They were approved earlier ("need all") but are not in the latest list of entities | yes, kept. Without expenses there is no net profit, only gross profit |
| 4 | Is **one currency and one time zone per company** enough? | yes |
| 5 | May a user limited to some outlets **see all customers** of the company? | yes for counts and segments (personal details are hidden from everyone); their orders and visits are limited to the user's outlets |
| 6 | Should **deleted or deactivated** products and outlets keep their history? | yes: `is_active` flags, no deletion, so past sales still add up |
