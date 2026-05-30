-- Heritage Ring — schema Fase 2 (Meta, SOLA LETTURA)
-- Valuta base = USD. Spese/ricavi Meta convertiti in USD prima del salvataggio.
-- Esegui questo SQL nel SQL Editor di Supabase (dopo 001_init.sql).

-- ============================================================
-- META — totali giornalieri a livello account (una riga per giorno)
-- ============================================================
create table if not exists meta_daily (
    day              date primary key,            -- giorno Europe/Rome
    spend            numeric(12,2) not null default 0,   -- spesa (USD)
    revenue          numeric(12,2) not null default 0,   -- revenue attribuito Meta (USD)
    orders           integer       not null default 0,   -- acquisti riportati da Meta
    clicks           integer       not null default 0,
    impressions      bigint        not null default 0,
    roas             numeric(12,4) not null default 0,    -- revenue / spend (piattaforma)
    cpa              numeric(12,2) not null default 0,    -- spend / orders
    account_currency text,                                -- valuta originale account
    fx_to_usd        numeric(12,6) not null default 1,    -- tasso applicato -> USD
    synced_at        timestamptz default now()
);

-- ============================================================
-- META — breakdown per singola campagna (una riga per giorno+campagna)
-- ============================================================
create table if not exists meta_campaigns (
    day           date not null,
    campaign_id   text not null,
    campaign_name text,
    spend         numeric(12,2) not null default 0,   -- USD
    revenue       numeric(12,2) not null default 0,   -- USD
    orders        integer       not null default 0,
    clicks        integer       not null default 0,
    impressions   bigint        not null default 0,
    roas          numeric(12,4) not null default 0,   -- revenue / spend
    cpa           numeric(12,2) not null default 0,   -- spend / orders
    cvr           numeric(12,4) not null default 0,   -- orders / clicks
    synced_at     timestamptz default now(),
    primary key (day, campaign_id)
);

create index if not exists idx_meta_campaigns_day on meta_campaigns (day);
create index if not exists idx_meta_campaigns_name on meta_campaigns (campaign_name);
