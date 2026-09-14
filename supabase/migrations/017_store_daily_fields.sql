-- Heritage Ring — store_daily arricchita per la separazione PER STORE completa (sezioni report
-- e card dashboard per store). Aggiunge shipping e ad spend così ogni store è auto-contenuto.
-- Esegui dopo 001..016. Idempotente. USD.
alter table store_daily add column if not exists shipping_total numeric(12,2) not null default 0;
alter table store_daily add column if not exists ads_spend      numeric(12,2) not null default 0;
