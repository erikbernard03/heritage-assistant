#!/usr/bin/env python3
"""
Diagnostica LIVE Klaviyo (sola lettura) — versione CLI.

NON espone mai la API key. Stessa logica del comando Telegram /klaviyo_check
(vedi src/diagnostics.py).

Esecuzione:
    KLAVIYO_API_KEY=pk_xxx python scripts/klaviyo_check.py
"""
import sys

sys.path.insert(0, ".")  # consente l'esecuzione dalla root del repo

from src.diagnostics import klaviyo_diagnostic  # noqa: E402

if __name__ == "__main__":
    print(klaviyo_diagnostic())
