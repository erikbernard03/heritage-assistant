-- Heritage Ring — schema Fase 3 (TikTok via Triple Whale, SOLA LETTURA)
-- SOLO dati TikTok (estratti dal Summary di Triple Whale). Meta/Shopify/Klaviyo
-- sono già collegati direttamente e NON passano da qui.
-- Valuta base = USD. Se Triple Whale riporta in EUR, i valori sono convertiti in
-- USD (EUR_TO_USD) PRIMA del salvataggio.
-- Esegui questo SQL nel SQL Editor di Supabase (dopo 001/002/003).

-- ============================================================
-- TIKTOK — totali giornalieri a livello account/canale (una riga per giorno)
-- ============================================================
create table if not exists tiktok_daily (
    day              date primary key,            -- giorno Europe/Rome
    spend            numeric(12,2) not null default 0,   -- spesa (USD)
    revenue          numeric(12,2) not null default 0,   -- revenue attribuito TikTok (USD)
    orders           integer       not null default 0,   -- conversioni/acquisti
    clicks           integer       not null default 0,
    impressions      bigint        not null default 0,
    roas             numeric(12,4) not null default 0,    -- revenue / spend
    cpa              numeric(12,2) not null default 0,    -- spend / orders
    account_currency text,                                -- valuta originale riportata
    fx_to_usd        numeric(12,6) not null default 1,    -- tasso applicato -> USD
    synced_at        timestamptz default now()
);

-- ============================================================
-- TIKTOK — breakdown per campagna (se disponibile dal Summary)
-- ============================================================
create table if not exists tiktok_campaigns (
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

create index if not exists idx_tiktok_campaigns_day on tiktok_campaigns (day);
create index if not exists idx_tiktok_campaigns_name on tiktok_campaigns (campaign_name);
