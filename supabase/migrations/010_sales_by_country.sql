-- Heritage Ring — Vendite per PAESE per giorno (sales by location). Esegui dopo 001..009.
-- Idempotente.
--
-- Riempita dal pull Shopify notturno e da /backfill: ogni riga = (giorno Europe/Rome,
-- paese di spedizione, revenue USD, numero ordini) di quel giorno.
create table if not exists sales_by_country_daily (
    day      date    not null,                     -- giorno Europe/Rome
    country  text    not null,                     -- ISO2 (es. US, IT) o 'Unknown'
    revenue  numeric(12,2) not null default 0,     -- USD (total_price)
    orders   integer not null default 0,
    primary key (day, country)
);
create index if not exists idx_sales_country_day on sales_by_country_daily (day);
