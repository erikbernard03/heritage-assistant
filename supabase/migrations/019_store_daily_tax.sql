-- Heritage Ring — store_daily.tax_total: tasse incassate (IVA/sales tax) per giorno/store.
-- Le tasse NON sono revenue (sono dovute allo Stato): da ora revenue le ESCLUDE. Persistiamo
-- comunque il totale tasse incassato, così la riconciliazione Stripe (gross include le tasse
-- pagate dal cliente) torna anche sui mesi vecchi: Stripe gross ≈ revenue + tax_total.
-- Valore = Σ current_total_tax (netto dei rimborsi) in USD. Esegui dopo 001..018. Idempotente.
alter table store_daily add column if not exists tax_total numeric(12,2) not null default 0;
