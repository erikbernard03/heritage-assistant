-- Heritage Ring — Vendite per ORA del giorno (sales by hour). Esegui dopo 001..010.
-- Idempotente.
--
-- Riempita dal pull Shopify notturno e da /backfill: ogni riga = (giorno Europe/Rome,
-- ora 0–23 nel fuso Roma dal created_at dell'ordine, revenue USD, numero ordini).
create table if not exists sales_by_hour_daily (
    day      date    not null,                     -- giorno Europe/Rome
    hour     integer not null,                     -- 0–23 (ora locale Europe/Rome)
    revenue  numeric(12,2) not null default 0,     -- USD (total_price)
    orders   integer not null default 0,
    primary key (day, hour)
);
create index if not exists idx_sales_hour_day on sales_by_hour_daily (day);
