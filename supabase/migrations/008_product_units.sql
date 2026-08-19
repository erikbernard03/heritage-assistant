-- Heritage Ring — Unità vendute per prodotto per giorno + sessioni (visitatori) giornaliere.
-- Esegui nel SQL Editor di Supabase dopo 001..007. Idempotente.
--
-- product_units_daily: riempita dal pull Shopify notturno (e da /backfill). Ogni riga =
-- (giorno Europe/Rome, chiave famiglia prodotto, unità vendute quel giorno).
-- Le chiavi (product_key) corrispondono a src/metrics/product_units.py PRODUCT_KEYS.
create table if not exists product_units_daily (
    day          date    not null,                 -- giorno Europe/Rome
    product_key  text    not null,                 -- es. gold_signet_round, square_signet, ...
    units        integer not null default 0,       -- unità vendute (somma quantity dei line item)
    primary key (day, product_key)
);
create index if not exists idx_product_units_day on product_units_daily (day);

-- Sessioni (visitatori) reali da Shopify per giorno, per la vista mensile "visitors".
-- NULL = non disponibili quel giorno (es. scope read_reports mancante) -> la dashboard
-- ripiega su una stima (ordini ÷ store CVR) etichettata "est.".
alter table daily_metrics
    add column if not exists store_sessions integer;   -- visitatori reali (Shopify sessions), nullable
