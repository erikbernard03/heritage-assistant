-- Heritage Ring — Secondo store (heritagering.co) MERGED nei report (Option A: totali combinati).
-- Aggiunge una colonna `store` alle tabelle granulari (default 'com'; le nuove righe .co usano
-- 'co'), così i dati grezzi restano separabili ma i report SOMMANO tra store.
-- daily_metrics resta UNA riga COMBINATA per giorno (i report la leggono già pre-sommata e la
-- pipeline break-even/mensile assume una riga/giorno): la separabilità per-store dei totali
-- giornalieri vive nella nuova tabella store_daily.
-- Esegui dopo 001..015. Idempotente.

-- 1) Colonna `store` + PK estesa sulle tabelle granulari.
alter table product_units_daily   add column if not exists store text not null default 'com';
alter table sales_by_country_daily add column if not exists store text not null default 'com';
alter table sales_by_hour_daily    add column if not exists store text not null default 'com';
alter table orders_by_source_daily add column if not exists store text not null default 'com';
alter table refunds_daily          add column if not exists store text not null default 'com';

-- PK estese per includere lo store (idempotente: solo se `store` non è già nella PK).
do $$
declare
  t record;
begin
  for t in
    select * from (values
      ('product_units_daily',   'day, store, product_key'),
      ('sales_by_country_daily','day, store, country'),
      ('sales_by_hour_daily',   'day, store, hour'),
      ('orders_by_source_daily','day, store, source'),
      ('refunds_daily',         'day, store')
    ) as v(tbl, cols)
  loop
    if not exists (
      select 1
      from pg_constraint c
      join pg_attribute a on a.attrelid = c.conrelid and a.attnum = any (c.conkey)
      where c.conrelid = t.tbl::regclass and c.contype = 'p' and a.attname = 'store'
    ) then
      execute format('alter table %I drop constraint if exists %I', t.tbl, t.tbl || '_pkey');
      execute format('alter table %I add primary key (%s)', t.tbl, t.cols);
    end if;
  end loop;
end $$;

-- 2) Totali giornalieri PER STORE (separabilità + tabella "Per store" della dashboard).
--    daily_metrics resta combinata; questa tabella conserva la scomposizione per store.
create table if not exists store_daily (
    day                   date not null,
    store                 text not null,               -- 'com' | 'co'
    revenue               numeric(12,2) not null default 0,   -- USD
    orders                integer       not null default 0,
    cogs_total            numeric(12,2) not null default 0,
    payment_fees          numeric(12,2) not null default 0,   -- revenue × fee rate dello store
    net_profit_operativo  numeric(12,2) not null default 0,
    store_sessions        integer,                            -- sessioni Shopify (nullable)
    primary key (day, store)
);
create index if not exists idx_store_daily_day on store_daily (day);
