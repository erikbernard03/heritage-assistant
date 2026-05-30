-- Heritage Ring — schema Fase 4 (Klaviyo, SOLA LETTURA, SOLO CAMPAGNE)
-- NB: SOLO dati a livello CAMPAGNA. I flows NON sono inclusi per scelta.
-- Valuta base = USD (revenue = conversion_value, già in valuta dello store).
-- Esegui questo SQL nel SQL Editor di Supabase (dopo 001_init.sql e 002_meta.sql).

-- ============================================================
-- KLAVIYO — totali giornalieri delle CAMPAGNE (una riga per giorno)
-- ============================================================
create table if not exists klaviyo_daily (
    day          date primary key,                  -- giorno Europe/Rome
    revenue      numeric(12,2) not null default 0,   -- conversion_value attribuito alle campagne (USD)
    opens        bigint        not null default 0,
    clicks       bigint        not null default 0,
    conversions  bigint        not null default 0,
    recipients   bigint        not null default 0,
    open_rate    numeric(12,4) not null default 0,   -- opens / recipients (calcolato)
    click_rate   numeric(12,4) not null default 0,   -- clicks / recipients (calcolato)
    synced_at    timestamptz default now()
);

-- ============================================================
-- KLAVIYO — breakdown per singola CAMPAGNA (una riga per giorno+campagna)
-- ============================================================
create table if not exists klaviyo_campaigns (
    day            date not null,
    campaign_id    text not null,
    campaign_name  text,
    revenue        numeric(12,2) not null default 0,   -- USD
    opens          bigint        not null default 0,
    clicks         bigint        not null default 0,
    conversions    bigint        not null default 0,
    recipients     bigint        not null default 0,
    open_rate      numeric(12,4) not null default 0,
    click_rate     numeric(12,4) not null default 0,
    synced_at      timestamptz default now(),
    primary key (day, campaign_id)
);

create index if not exists idx_klaviyo_campaigns_day on klaviyo_campaigns (day);
create index if not exists idx_klaviyo_campaigns_name on klaviyo_campaigns (campaign_name);
