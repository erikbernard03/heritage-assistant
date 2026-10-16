-- Heritage Ring — cache PERSISTENTE della revenue Klaviyo a finestra piena per mese.
-- Il reporting Klaviyo ha un rate limit bassissimo: la cache in-process si perde ad ogni
-- restart/redeploy Railway, e il primo load della dashboard ri-tira TUTTI i mesi in sequenza
-- (lentissimo). Persistendola qui, il cold start legge dal DB (veloce) e NON chiama Klaviyo.
-- Mesi PASSATI (chiusi) = immutabili: fetch una volta sola nella vita, poi solo letture.
-- Mese CORRENTE = stale-while-revalidate (TTL 6h), rinfrescato anche dal cron notturno.
-- Esegui dopo 001..017. Idempotente.
create table if not exists klaviyo_monthly_cache (
    month       text not null,                 -- 'YYYY-MM' (Europe/Rome)
    kind        text not null,                 -- 'campaign' | 'flow'
    payload     jsonb not null,                -- porzione del risultato load_klaviyo_period
    fetched_at  timestamptz not null default now(),
    primary key (month, kind)
);
create index if not exists idx_klaviyo_monthly_cache_month on klaviyo_monthly_cache (month);
