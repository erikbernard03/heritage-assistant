-- Heritage Ring — Refund giornalieri dai record ordine SHOPIFY (visibilità; Stripe non
-- vede i refund PayPal). Esegui dopo 001..012. Idempotente. USD.
create table if not exists refunds_daily (
    day            date    not null primary key,       -- giorno Europe/Rome (dell'ordine)
    refund_amount  numeric(12,2) not null default 0,
    refund_count   integer not null default 0
);
create index if not exists idx_refunds_day on refunds_daily (day);
