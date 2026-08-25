-- Heritage Ring — Revenue FLOWS Klaviyo (accanto alle campagne). Esegui dopo 001..008.
-- Idempotente.
--
-- flow_revenue: snapshot notturno della revenue attribuita ai FLOWS Klaviyo per il giorno
-- (email/automazioni), separata dalle campagne (colonna revenue). NULL = non pullata
-- (es. scope Flows:Read mancante). Le viste di PERIODO usano invece la query a finestra
-- piena (flow-values-report) a runtime.
alter table klaviyo_daily
    add column if not exists flow_revenue numeric(12,2);   -- USD, nullable
