-- Heritage Ring — schema Fase 1 (solo Shopify)
-- Valuta base = USD. Tutti i valori monetari sono in USD.
-- Esegui questo SQL nel SQL Editor di Supabase (o via CLI).

-- ============================================================
-- ORDINI (uno per ordine Shopify)
-- ============================================================
create table if not exists orders (
    id                bigint primary key,            -- Shopify order id
    order_number      text,
    created_at        timestamptz,                   -- created_at Shopify (UTC)
    processed_at      timestamptz,
    cancelled_at      timestamptz,
    financial_status  text,
    fulfillment_status text,
    currency          text,                          -- valuta originale dell'ordine
    total_price       numeric(12,2),                 -- revenue dell'ordine (USD)
    subtotal_price    numeric(12,2),
    total_tax         numeric(12,2),
    total_shipping    numeric(12,2),                 -- spedizione pagata dal cliente
    num_line_items    integer,
    day_rome          date,                          -- giorno di riferimento (Europe/Rome)
    raw               jsonb,                          -- payload grezzo per audit
    synced_at         timestamptz default now()
);

create index if not exists idx_orders_day_rome on orders (day_rome);
create index if not exists idx_orders_created_at on orders (created_at);

-- ============================================================
-- LINE ITEMS (uno per riga prodotto di un ordine)
-- ============================================================
create table if not exists line_items (
    id            bigint primary key,                -- Shopify line item id
    order_id      bigint references orders (id) on delete cascade,
    product_id    bigint,
    variant_id    bigint,
    handle        text,                              -- handle Shopify (per il COGS)
    title         text,
    sku           text,
    quantity      integer,
    price         numeric(12,2),                     -- prezzo unitario (USD)
    unit_cogs     numeric(12,2),                     -- COGS unitario applicato (USD)
    line_cogs     numeric(12,2),                     -- COGS riga = unit_cogs * quantity
    synced_at     timestamptz default now()
);

create index if not exists idx_line_items_order_id on line_items (order_id);
create index if not exists idx_line_items_handle on line_items (handle);

-- ============================================================
-- METRICHE GIORNALIERE (una riga per giorno Europe/Rome)
-- ============================================================
create table if not exists daily_metrics (
    day                   date primary key,          -- giorno Europe/Rome
    num_orders            integer not null default 0,
    revenue               numeric(12,2) not null default 0,   -- revenue reale Shopify (USD)
    cogs_total            numeric(12,2) not null default 0,
    shipping_total        numeric(12,2) not null default 0,   -- $7 * num_orders
    payment_fees          numeric(12,2) not null default 0,   -- 7.5% * revenue
    ads_spend             numeric(12,2) not null default 0,   -- Fase 1 Shopify: 0
    fixed_cost_daily      numeric(12,2) not null default 0,   -- 5668/30
    net_profit_operativo  numeric(12,2) not null default 0,   -- senza costi fissi
    net_profit_netto      numeric(12,2) not null default 0,   -- con costi fissi
    aov                   numeric(12,2) not null default 0,
    computed_at           timestamptz default now()
);
