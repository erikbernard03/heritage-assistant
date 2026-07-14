# Heritage Ring — comandi di avvio (riferimento per i due servizi Railway).
# Su Railway imposta lo "Start Command" di ciascun servizio (vedi DEPLOY_RAILWAY.md):
#   - servizio BOT       (always-on):  python -m src.bot.telegram_bot
#   - servizio CRON      (report):     python -m src.run_daily
#   - servizio DASHBOARD (web):        streamlit run src/dashboard/app.py --server.port $PORT --server.address 0.0.0.0
worker: python -m src.bot.telegram_bot
cron: python -m src.run_daily
dashboard: streamlit run src/dashboard/app.py --server.port $PORT --server.address 0.0.0.0 --server.headless true
