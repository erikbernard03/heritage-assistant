-- Heritage Ring — aggiunge la CVR di negozio (store conversion rate) ai dati Google/store.
-- Fonte: Triple Whale Summary, metricId "averageGaTransactionsPerSession" (GA transactions
-- per session = conversion rate del negozio). È una metrica a livello STORE, salvata qui
-- insieme ai totali Google perché proviene dalla stessa pull Summary.
-- Esegui questo SQL nel SQL Editor di Supabase (dopo 005_google.sql).

alter table google_daily
    add column if not exists store_cvr numeric(12,6) not null default 0;  -- es. 0.0234 = 2.34%
