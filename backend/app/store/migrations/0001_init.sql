-- 0001: the approved schema (docs/design/data-model.md).
--
-- Run as bi_owner. The users bi_app and bi_readonly must already exist
-- (deploy/postgres/init-roles.sh creates them).
--
--   app   the product's own data. bi_app reads and writes it; bi_readonly cannot see it.
--   biz   the business data people ask about. Every table has tenant_id, a composite
--         key, forced row security, and column grants that leave out private columns.

CREATE SCHEMA app;
CREATE SCHEMA biz;

-- ---------------------------------------------------------------------------
-- app
-- ---------------------------------------------------------------------------

CREATE TABLE app.tenants (
    id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name       text NOT NULL,
    slug       text NOT NULL UNIQUE,
    currency   text NOT NULL DEFAULT 'INR',
    timezone   text NOT NULL DEFAULT 'Asia/Kolkata',
    is_active  boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE app.users (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id     uuid REFERENCES app.tenants (id),
    email         text NOT NULL UNIQUE CHECK (email = lower(email)),
    -- '!' means no password is set; sign-in arrives with the authentication phase
    password_hash text NOT NULL DEFAULT '!',
    full_name     text NOT NULL,
    role          text NOT NULL CHECK (role IN ('super_admin', 'admin', 'analyst', 'viewer')),
    all_outlets   boolean NOT NULL DEFAULT true,
    is_active     boolean NOT NULL DEFAULT true,
    token_version integer NOT NULL DEFAULT 0,
    last_login_at timestamptz,
    created_at    timestamptz NOT NULL DEFAULT now(),
    CHECK ((role = 'super_admin') = (tenant_id IS NULL)),
    UNIQUE (tenant_id, id)
);

-- ---------------------------------------------------------------------------
-- biz: who is asking
-- ---------------------------------------------------------------------------
-- The company and the outlets of the caller are set on the transaction by our code
-- (SET LOCAL app.tenant_id / app.outlet_ids). Nothing set means no rows: fail closed.
-- After a transaction a custom setting reads as '' rather than NULL, hence NULLIF.

CREATE FUNCTION biz.current_tenant() RETURNS uuid
    LANGUAGE sql STABLE PARALLEL SAFE
    RETURN NULLIF(current_setting('app.tenant_id', true), '')::uuid;

CREATE FUNCTION biz.outlet_visible(outlet uuid) RETURNS boolean
    LANGUAGE sql STABLE PARALLEL SAFE
    RETURN CASE
        WHEN NULLIF(current_setting('app.outlet_ids', true), '') IS NULL THEN false
        WHEN current_setting('app.outlet_ids', true) = '*' THEN true
        ELSE outlet = ANY (string_to_array(current_setting('app.outlet_ids', true), ',')::uuid[])
    END;

-- ---------------------------------------------------------------------------
-- biz: sales
-- ---------------------------------------------------------------------------

CREATE TABLE biz.outlets (
    id        uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES app.tenants (id),
    name      text NOT NULL,
    code      text NOT NULL,
    city      text NOT NULL,
    region    text NOT NULL,
    opened_on date NOT NULL,
    is_active boolean NOT NULL DEFAULT true,
    UNIQUE (tenant_id, id),
    UNIQUE (tenant_id, code)
);

CREATE TABLE app.user_outlets (
    tenant_id uuid NOT NULL,
    user_id   uuid NOT NULL,
    outlet_id uuid NOT NULL,
    PRIMARY KEY (tenant_id, user_id, outlet_id),
    FOREIGN KEY (tenant_id, user_id) REFERENCES app.users (tenant_id, id),
    FOREIGN KEY (tenant_id, outlet_id) REFERENCES biz.outlets (tenant_id, id)
);

CREATE TABLE biz.categories (
    id        uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES app.tenants (id),
    name      text NOT NULL,
    UNIQUE (tenant_id, id),
    UNIQUE (tenant_id, name)
);

CREATE TABLE biz.products (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id   uuid NOT NULL REFERENCES app.tenants (id),
    category_id uuid NOT NULL,
    sku         text NOT NULL,
    name        text NOT NULL,
    unit        text NOT NULL DEFAULT 'each',
    unit_price  numeric(14,2) NOT NULL CHECK (unit_price >= 0),
    cost_price  numeric(14,2) NOT NULL CHECK (cost_price >= 0),
    is_active   boolean NOT NULL DEFAULT true,
    created_at  timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, id),
    UNIQUE (tenant_id, sku),
    FOREIGN KEY (tenant_id, category_id) REFERENCES biz.categories (tenant_id, id)
);

CREATE TABLE biz.customers (
    id        uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES app.tenants (id),
    name      text NOT NULL,   -- private
    phone     text,            -- private
    email     text,            -- private
    segment   text NOT NULL CHECK (segment IN ('new', 'regular', 'vip', 'corporate')),
    city      text NOT NULL,
    joined_on date NOT NULL,
    UNIQUE (tenant_id, id)
);

CREATE TABLE biz.orders (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       uuid NOT NULL REFERENCES app.tenants (id),
    outlet_id       uuid NOT NULL,
    customer_id     uuid,
    order_no        text NOT NULL,
    ordered_at      timestamptz NOT NULL,
    order_date      date NOT NULL,
    channel         text NOT NULL CHECK (channel IN ('dine_in', 'takeaway', 'delivery', 'online')),
    status          text NOT NULL CHECK (status IN ('completed', 'cancelled', 'refunded')),
    payment_method  text NOT NULL CHECK (payment_method IN ('cash', 'card', 'upi', 'wallet')),
    subtotal        numeric(14,2) NOT NULL,
    discount_amount numeric(14,2) NOT NULL DEFAULT 0,
    tax_amount      numeric(14,2) NOT NULL DEFAULT 0,
    total_amount    numeric(14,2) NOT NULL,
    UNIQUE (tenant_id, id),
    UNIQUE (tenant_id, order_no),
    FOREIGN KEY (tenant_id, outlet_id) REFERENCES biz.outlets (tenant_id, id),
    FOREIGN KEY (tenant_id, customer_id) REFERENCES biz.customers (tenant_id, id)
);
CREATE INDEX orders_by_date ON biz.orders (tenant_id, order_date);
CREATE INDEX orders_by_outlet_date ON biz.orders (tenant_id, outlet_id, order_date);

CREATE TABLE biz.order_items (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       uuid NOT NULL REFERENCES app.tenants (id),
    order_id        uuid NOT NULL,
    outlet_id       uuid NOT NULL,   -- copied from the order: the outlet rule needs no join
    order_date      date NOT NULL,   -- copied from the order: product-by-period is one table
    product_id      uuid NOT NULL,
    quantity        numeric(14,3) NOT NULL CHECK (quantity > 0),
    unit_price      numeric(14,2) NOT NULL,
    unit_cost       numeric(14,2) NOT NULL,
    discount_amount numeric(14,2) NOT NULL DEFAULT 0,
    line_total      numeric(14,2) NOT NULL,
    UNIQUE (tenant_id, id),
    FOREIGN KEY (tenant_id, order_id) REFERENCES biz.orders (tenant_id, id),
    FOREIGN KEY (tenant_id, outlet_id) REFERENCES biz.outlets (tenant_id, id),
    FOREIGN KEY (tenant_id, product_id) REFERENCES biz.products (tenant_id, id)
);
CREATE INDEX order_items_by_product_date ON biz.order_items (tenant_id, product_id, order_date);
CREATE INDEX order_items_by_order ON biz.order_items (tenant_id, order_id);

CREATE TABLE biz.customer_visits (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id   uuid NOT NULL REFERENCES app.tenants (id),
    outlet_id   uuid NOT NULL,
    customer_id uuid NOT NULL,
    visit_date  date NOT NULL,
    visited_at  timestamptz NOT NULL,
    source      text NOT NULL CHECK (source IN ('walk_in', 'reservation', 'online_order', 'delivery')),
    party_size  integer CHECK (party_size > 0),
    order_id    uuid,
    UNIQUE (tenant_id, id),
    UNIQUE (tenant_id, order_id),
    FOREIGN KEY (tenant_id, outlet_id) REFERENCES biz.outlets (tenant_id, id),
    FOREIGN KEY (tenant_id, customer_id) REFERENCES biz.customers (tenant_id, id),
    FOREIGN KEY (tenant_id, order_id) REFERENCES biz.orders (tenant_id, id)
);
CREATE INDEX customer_visits_by_customer ON biz.customer_visits (tenant_id, customer_id, visit_date);
CREATE INDEX customer_visits_by_outlet ON biz.customer_visits (tenant_id, outlet_id, visit_date);

-- ---------------------------------------------------------------------------
-- biz: inventory and purchasing
-- ---------------------------------------------------------------------------

CREATE TABLE biz.suppliers (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id    uuid NOT NULL REFERENCES app.tenants (id),
    name         text NOT NULL,
    contact_name text,   -- private
    phone        text,   -- private
    city         text NOT NULL,
    is_active    boolean NOT NULL DEFAULT true,
    UNIQUE (tenant_id, id)
);

CREATE TABLE biz.inventory_levels (
    tenant_id        uuid NOT NULL REFERENCES app.tenants (id),
    outlet_id        uuid NOT NULL,
    product_id       uuid NOT NULL,
    quantity_on_hand numeric(14,3) NOT NULL,
    reorder_level    numeric(14,3) NOT NULL,
    updated_at       timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, outlet_id, product_id),
    FOREIGN KEY (tenant_id, outlet_id) REFERENCES biz.outlets (tenant_id, id),
    FOREIGN KEY (tenant_id, product_id) REFERENCES biz.products (tenant_id, id)
);

CREATE TABLE biz.stock_movements (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id     uuid NOT NULL REFERENCES app.tenants (id),
    outlet_id     uuid NOT NULL,
    product_id    uuid NOT NULL,
    movement_type text NOT NULL CHECK (movement_type IN
                      ('purchase', 'sale', 'wastage', 'transfer_in', 'transfer_out', 'adjustment')),
    quantity      numeric(14,3) NOT NULL,   -- signed: stock coming in is positive
    unit_cost     numeric(14,2) NOT NULL,
    movement_date date NOT NULL,
    moved_at      timestamptz NOT NULL,
    note          text,
    UNIQUE (tenant_id, id),
    FOREIGN KEY (tenant_id, outlet_id) REFERENCES biz.outlets (tenant_id, id),
    FOREIGN KEY (tenant_id, product_id) REFERENCES biz.products (tenant_id, id)
);
CREATE INDEX stock_movements_by_product ON biz.stock_movements (tenant_id, product_id, movement_date);

CREATE TABLE biz.purchase_orders (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id    uuid NOT NULL REFERENCES app.tenants (id),
    outlet_id    uuid NOT NULL,
    supplier_id  uuid NOT NULL,
    po_no        text NOT NULL,
    status       text NOT NULL CHECK (status IN ('draft', 'ordered', 'received', 'cancelled')),
    ordered_on   date NOT NULL,
    received_on  date,
    total_amount numeric(14,2) NOT NULL,
    UNIQUE (tenant_id, id),
    UNIQUE (tenant_id, po_no),
    FOREIGN KEY (tenant_id, outlet_id) REFERENCES biz.outlets (tenant_id, id),
    FOREIGN KEY (tenant_id, supplier_id) REFERENCES biz.suppliers (tenant_id, id)
);

CREATE TABLE biz.purchase_order_items (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id         uuid NOT NULL REFERENCES app.tenants (id),
    purchase_order_id uuid NOT NULL,
    product_id        uuid NOT NULL,
    quantity          numeric(14,3) NOT NULL CHECK (quantity > 0),
    unit_cost         numeric(14,2) NOT NULL,
    line_total        numeric(14,2) NOT NULL,
    UNIQUE (tenant_id, id),
    FOREIGN KEY (tenant_id, purchase_order_id) REFERENCES biz.purchase_orders (tenant_id, id),
    FOREIGN KEY (tenant_id, product_id) REFERENCES biz.products (tenant_id, id)
);

-- ---------------------------------------------------------------------------
-- biz: expenses
-- ---------------------------------------------------------------------------

CREATE TABLE biz.expense_categories (
    id        uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES app.tenants (id),
    name      text NOT NULL,
    UNIQUE (tenant_id, id),
    UNIQUE (tenant_id, name)
);

CREATE TABLE biz.expenses (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id      uuid NOT NULL REFERENCES app.tenants (id),
    outlet_id      uuid NOT NULL,
    category_id    uuid NOT NULL,
    expense_date   date NOT NULL,
    amount         numeric(14,2) NOT NULL CHECK (amount >= 0),
    vendor         text,
    description    text,
    payment_method text,
    UNIQUE (tenant_id, id),
    FOREIGN KEY (tenant_id, outlet_id) REFERENCES biz.outlets (tenant_id, id),
    FOREIGN KEY (tenant_id, category_id) REFERENCES biz.expense_categories (tenant_id, id)
);
CREATE INDEX expenses_by_date ON biz.expenses (tenant_id, expense_date);

-- ---------------------------------------------------------------------------
-- app: the log of every piece of AI-written SQL, run or refused
-- ---------------------------------------------------------------------------

CREATE TABLE app.query_log (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id   uuid NOT NULL REFERENCES app.tenants (id),
    user_id     uuid NOT NULL REFERENCES app.users (id),
    question    text NOT NULL,
    sql         text,
    status      text NOT NULL CHECK (status IN ('ok', 'rejected', 'error', 'timeout')),
    reason      text,
    row_count   integer,
    duration_ms integer,
    created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX query_log_by_tenant ON app.query_log (tenant_id, created_at DESC);

-- ---------------------------------------------------------------------------
-- Row security: every biz table, forced, keyed on the caller's company and outlets
-- ---------------------------------------------------------------------------

DO $$
DECLARE
    t text;
    by_outlet text[] := ARRAY['orders', 'order_items', 'customer_visits', 'inventory_levels',
                              'stock_movements', 'purchase_orders', 'expenses'];
    by_tenant text[] := ARRAY['categories', 'products', 'customers', 'suppliers',
                              'purchase_order_items', 'expense_categories'];
BEGIN
    FOREACH t IN ARRAY by_outlet LOOP
        EXECUTE format('ALTER TABLE biz.%I ENABLE ROW LEVEL SECURITY', t);
        EXECUTE format('ALTER TABLE biz.%I FORCE ROW LEVEL SECURITY', t);
        EXECUTE format(
            'CREATE POLICY caller ON biz.%I USING (tenant_id = biz.current_tenant() AND biz.outlet_visible(outlet_id))', t);
    END LOOP;
    FOREACH t IN ARRAY by_tenant LOOP
        EXECUTE format('ALTER TABLE biz.%I ENABLE ROW LEVEL SECURITY', t);
        EXECUTE format('ALTER TABLE biz.%I FORCE ROW LEVEL SECURITY', t);
        EXECUTE format('CREATE POLICY caller ON biz.%I USING (tenant_id = biz.current_tenant())', t);
    END LOOP;
END $$;

-- an outlet is limited by its own id
ALTER TABLE biz.outlets ENABLE ROW LEVEL SECURITY;
ALTER TABLE biz.outlets FORCE ROW LEVEL SECURITY;
CREATE POLICY caller ON biz.outlets
    USING (tenant_id = biz.current_tenant() AND biz.outlet_visible(id));

-- ---------------------------------------------------------------------------
-- Grants
-- ---------------------------------------------------------------------------

-- bi_app: the product's own data, and business data for its own screens (under row security)
GRANT USAGE ON SCHEMA app, biz TO bi_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA app TO bi_app;
GRANT SELECT ON ALL TABLES IN SCHEMA biz TO bi_app;
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA biz TO bi_app;

-- bi_readonly: runs AI-written SQL. biz only, SELECT only, and never a private column.
GRANT USAGE ON SCHEMA biz TO bi_readonly;
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA biz TO bi_readonly;
GRANT SELECT ON biz.outlets, biz.categories, biz.products, biz.orders, biz.order_items,
                biz.customer_visits, biz.inventory_levels, biz.stock_movements,
                biz.purchase_orders, biz.purchase_order_items, biz.expense_categories,
                biz.expenses TO bi_readonly;
GRANT SELECT (id, tenant_id, segment, city, joined_on) ON biz.customers TO bi_readonly;
GRANT SELECT (id, tenant_id, name, city, is_active) ON biz.suppliers TO bi_readonly;
ALTER ROLE bi_readonly SET search_path = biz;

-- The read-only user must not be able to change who it is asking as. Our code uses
-- SET LOCAL (a statement the guard never lets through), so the function is not needed.
REVOKE EXECUTE ON FUNCTION pg_catalog.set_config(text, text, boolean) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION pg_catalog.set_config(text, text, boolean) TO bi_app;
