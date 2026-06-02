-- Heritage Ring — schema Fase 2 (Google Ads via Triple Whale, SOLA LETTURA)
-- SOLO dati Google (estratti dal Summary di Triple Whale). Valori già in USD.
-- Solo totali a livello account: il breakdown per campagna arriverà via Google Ads
-- API quando il developer token sarà approvato.
-- Esegui questo SQL nel SQL Editor di Supabase (dopo 001/002/003/004).

create table if not exists google_daily (
    day              date primary key,            -- giorno Europe/Rome
    spend            numeric(12,2) not null default 0,   -- ga_adCost (USD)
    revenue          numeric(12,2) not null default 0,   -- conversion value (USD)
    orders           integer       not null default 0,   -- conversioni/transazioni
    clicks           integer       not null default 0,
    impressions      bigint        not null default 0,
    roas             numeric(12,4) not null default 0,    -- ga_ROAS
    cpa              numeric(12,2) not null default 0,    -- googleCpa / googleAllCpa
    account_currency text,
    fx_to_usd        numeric(12,6) not null default 1,
    synced_at        timestamptz default now()
);
