-- Heritage Ring — colonna `kind` su tw_pixel_daily: distingue l'origine del dato per etichettare
-- ONESTAMENTE la dashboard: "pixel" = vero pixel TW per-canale; "platform-reported" = numeri
-- dichiarati dalla piattaforma ed esposti da TW. Esegui dopo 014. Idempotente.
alter table tw_pixel_daily
    add column if not exists kind text not null default 'platform-reported';
