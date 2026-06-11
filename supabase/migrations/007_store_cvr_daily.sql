-- Heritage Ring — CVR di negozio a livello STORE in daily_metrics.
-- Sorgente primaria: Shopify (ShopifyQL FROM sessions, definizione di Shopify =
-- sessioni convertite / sessioni totali) -> combacia col dashboard Shopify.
-- Fallback: Triple Whale pixelConversionRate (già in google_daily.store_cvr).
-- Esegui questo SQL nel SQL Editor di Supabase (dopo 001..006). Idempotente.

alter table daily_metrics
    add column if not exists store_cvr numeric(12,6) not null default 0;  -- frazione, es. 0.0234 = 2.34%
