-- Heritage Ring — Stripe (riconciliazione, fee reali, payout, dispute). Esegui dopo 001..011.
-- Idempotente. Tutti gli importi in USD.

-- Aggregato giornaliero dalle balance transactions (giorno Europe/Rome).
create table if not exists stripe_daily (
    day            date    not null primary key,
    gross_amount   numeric(12,2) not null default 0,   -- Σ charge/payment processati
    fee_amount     numeric(12,2) not null default 0,   -- Σ fee Stripe reali
    net_amount     numeric(12,2) not null default 0,   -- gross − fee − refund
    charge_count   integer not null default 0,
    refund_amount  numeric(12,2) not null default 0,   -- Σ |refund|
    refund_count   integer not null default 0
);

-- Payout effettivi (bonifici sul conto). arrival_date = giorno di arrivo dei soldi.
create table if not exists stripe_payouts (
    id            text not null primary key,
    arrival_date  date,
    amount        numeric(12,2) not null default 0,
    status        text,
    created       date
);
create index if not exists idx_stripe_payouts_arrival on stripe_payouts (arrival_date);

-- Dispute/chargeback.
create table if not exists stripe_disputes (
    id            text not null primary key,
    amount        numeric(12,2) not null default 0,
    status        text,
    reason        text,
    created       date,
    evidence_due  date
);
create index if not exists idx_stripe_disputes_created on stripe_disputes (created);
