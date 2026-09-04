-- Heritage Ring — Attribuzione vendite per SORGENTE. Esegui dopo 001..013. Idempotente. USD.

-- 1) LAST-CLICK dai dati di atterraggio ordine Shopify (utm_source/medium, referrer).
--    Un record per (giorno, sorgente). `source` è un bucket canonico
--    (meta/google_paid/google_organic/tiktok/pinterest/email/direct) oppure la stringa grezza
--    per gli sconosciuti ("other").
create table if not exists orders_by_source_daily (
    day     date    not null,                 -- giorno Europe/Rome dell'ordine
    source  text    not null,
    orders  integer not null default 0,
    revenue numeric(12,2) not null default 0,
    primary key (day, source)
);
create index if not exists idx_orders_by_source_day on orders_by_source_daily (day);

-- 2) Pixel Triple Whale per canale (second opinion). Un record per (giorno, canale).
--    `orders` numeric perché l'attribuzione pixel può essere frazionaria.
create table if not exists tw_pixel_daily (
    day     date    not null,                 -- giorno Europe/Rome
    channel text    not null,                 -- meta | google | tiktok
    orders  numeric(12,2) not null default 0,
    revenue numeric(12,2) not null default 0,
    primary key (day, channel)
);
create index if not exists idx_tw_pixel_day on tw_pixel_daily (day);
