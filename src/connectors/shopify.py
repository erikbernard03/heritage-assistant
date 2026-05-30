"""
Connettore Shopify — autenticazione tramite CLIENT CREDENTIALS GRANT.

Da gennaio 2026 le custom app admin e il token statico `shpat_` sono deprecati.
L'app vive nel Dev Dashboard e fornisce Client ID + Client Secret. Il backend
scambia (client_id + client_secret) per un access token a runtime; il token NON è
copiabile dalla UI ed è a vita breve, quindi viene messo in cache e rinnovato.

Scope Admin API necessari (da abilitare sull'app + installare l'app sullo store):
  read_orders, read_products, read_fulfillments, read_inventory

Valuta: lo store opera in USD => i valori Shopify sono già in USD (valuta base).
Nessun LLM tocca questi numeri.
"""
from __future__ import annotations

import time
from datetime import datetime
from typing import Iterator, Optional

import requests

from config import settings


class ShopifyError(RuntimeError):
    """Errore generico del connettore Shopify."""


class ShopifyConnector:
    def __init__(
        self,
        store: Optional[str] = None,
        client_id: Optional[str] = None,
        client_secret: Optional[str] = None,
        api_version: Optional[str] = None,
        timeout: int = 30,
    ):
        self.store = (store or settings.SHOPIFY_STORE).strip()
        self.client_id = client_id or settings.SHOPIFY_CLIENT_ID
        self.client_secret = client_secret or settings.SHOPIFY_CLIENT_SECRET
        self.api_version = api_version or settings.SHOPIFY_API_VERSION
        self.timeout = timeout

        if not self.store:
            raise ShopifyError("SHOPIFY_STORE non configurato (.env).")
        if not (self.client_id and self.client_secret):
            raise ShopifyError(
                "SHOPIFY_CLIENT_ID / SHOPIFY_CLIENT_SECRET non configurati (.env)."
            )

        self._access_token: Optional[str] = None
        self._token_expiry: float = 0.0
        self._session = requests.Session()

    # ------------------------------------------------------------------ auth
    @property
    def base_url(self) -> str:
        return f"https://{self.store}/admin/api/{self.api_version}"

    def _fetch_access_token(self) -> str:
        """Client credentials grant: scambia client_id+secret per un access token."""
        url = f"https://{self.store}/admin/oauth/access_token"
        payload = {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "grant_type": "client_credentials",
        }
        resp = self._session.post(url, json=payload, timeout=self.timeout)
        if resp.status_code != 200:
            raise ShopifyError(
                f"Client credentials grant fallito ({resp.status_code}): {resp.text[:500]}. "
                "Verifica Client ID/Secret, che gli scope Admin API siano abilitati e "
                "che l'app sia installata sullo store."
            )
        data = resp.json()
        token = data.get("access_token")
        if not token:
            raise ShopifyError(f"Risposta token senza access_token: {data}")
        # Rinnova ~5 minuti prima della scadenza per sicurezza.
        expires_in = int(data.get("expires_in", 3600))
        self._token_expiry = time.time() + max(expires_in - 300, 60)
        self._access_token = token
        return token

    def _token(self) -> str:
        if not self._access_token or time.time() >= self._token_expiry:
            return self._fetch_access_token()
        return self._access_token

    # --------------------------------------------------------------- requests
    def _request(self, method: str, path: str, **kwargs) -> requests.Response:
        """Esegue una richiesta REST con header di auth, retry su 429/5xx e backoff."""
        url = path if path.startswith("http") else f"{self.base_url}{path}"
        max_retries = 5
        for attempt in range(max_retries):
            headers = kwargs.pop("headers", {})
            headers["X-Shopify-Access-Token"] = self._token()
            headers["Content-Type"] = "application/json"
            resp = self._session.request(
                method, url, headers=headers, timeout=self.timeout, **kwargs
            )

            if resp.status_code == 429:  # rate limited
                retry_after = float(resp.headers.get("Retry-After", 2 ** attempt))
                time.sleep(retry_after)
                continue
            if resp.status_code in (500, 502, 503, 504):
                time.sleep(2 ** attempt)
                continue
            if resp.status_code == 401:  # token scaduto/non valido: forza refresh
                self._access_token = None
                if attempt < max_retries - 1:
                    continue
            if resp.status_code >= 400:
                raise ShopifyError(
                    f"{method} {url} -> {resp.status_code}: {resp.text[:500]}"
                )
            return resp
        raise ShopifyError(f"{method} {url}: esauriti i retry.")

    def _paginate(self, path: str, params: dict) -> Iterator[dict]:
        """Itera su tutte le pagine usando il Link header (cursor-based)."""
        next_url: Optional[str] = None
        first = True
        while True:
            if first:
                resp = self._request("GET", path, params=params)
                first = False
            else:
                if not next_url:
                    break
                resp = self._request("GET", next_url)

            yield resp.json()

            # Pagination cursor-based via header Link rel="next"
            link = resp.headers.get("Link", "")
            next_url = None
            for part in link.split(","):
                if 'rel="next"' in part:
                    next_url = part[part.find("<") + 1 : part.find(">")]
                    break
            if not next_url:
                break

    # ------------------------------------------------------------------- API
    def ping(self) -> dict:
        """Verifica connessione + auth: ritorna info base dello shop."""
        resp = self._request("GET", "/shop.json")
        return resp.json().get("shop", {})

    def get_orders(
        self,
        created_at_min: datetime,
        created_at_max: datetime,
        status: str = "any",
    ) -> list[dict]:
        """
        Ritorna gli ordini creati nell'intervallo [min, max].

        I datetime devono essere timezone-aware (vengono inviati in ISO con offset).
        Include i line items (necessari per il COGS).
        """
        params = {
            "status": status,
            "created_at_min": created_at_min.isoformat(),
            "created_at_max": created_at_max.isoformat(),
            "limit": 250,
        }
        orders: list[dict] = []
        for page in self._paginate("/orders.json", params):
            orders.extend(page.get("orders", []))
        return orders

    def get_products_handle_map(self) -> dict[int, str]:
        """
        Mappa product_id -> handle (serve perché i line item degli ordini NON
        contengono l'handle, ma il COGS è indicizzato per handle).
        """
        params = {"limit": 250, "fields": "id,handle,title"}
        mapping: dict[int, str] = {}
        for page in self._paginate("/products.json", params):
            for prod in page.get("products", []):
                if prod.get("id") is not None and prod.get("handle"):
                    mapping[int(prod["id"])] = prod["handle"]
        return mapping
