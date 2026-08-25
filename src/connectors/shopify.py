"""
Connettore Shopify — autenticazione tramite CLIENT CREDENTIALS GRANT.

Da gennaio 2026 le custom app admin e il token statico `shpat_` sono deprecati.
L'app vive nel Dev Dashboard e fornisce Client ID + Client Secret. Il backend
scambia (client_id + client_secret) per un access token a runtime; il token NON è
copiabile dalla UI ed è a vita breve, quindi viene messo in cache e rinnovato.

Scope Admin API necessari (da abilitare sull'app + installare l'app sullo store):
  read_orders, read_all_orders, read_products, read_fulfillments, read_inventory,
  read_reports
  - read_all_orders: ordini oltre i 60 giorni (backfill storico).
  - read_reports:    ShopifyQL FROM sessions (CVR + visitatori reali).

Valuta: lo store opera in USD => i valori Shopify sono già in USD (valuta base).
Nessun LLM tocca questi numeri.
"""
from __future__ import annotations

import json
import time
from datetime import datetime
from typing import Iterator, Optional

import requests

from config import settings


def shopifyql_error(gql: dict) -> Optional[str]:
    """
    Ritorna il messaggio d'errore REALE di una risposta shopifyqlQuery, oppure None se
    la query è andata a buon fine. Sorgenti: `errors` GraphQL (es. ACCESS_DENIED per
    read_reports mancante) e `parseErrors` di ShopifyQL (sintassi / data source non valido).
    Serve alla diagnostica per NON confondere un errore di query con "scope mancante".
    """
    errs = gql.get("errors")
    if errs:
        msgs = [e.get("message") or str(e) for e in errs] if isinstance(errs, list) else [str(errs)]
        return "GraphQL: " + " | ".join(m for m in msgs if m)
    node = (gql.get("data") or {}).get("shopifyqlQuery") or {}
    pe = node.get("parseErrors")
    if pe:
        parts = [p if isinstance(p, str) else (p.get("message") or str(p)) for p in pe]
        return "ShopifyQL parseErrors: " + " | ".join(parts)
    return None


def _shopifyql_first_row(gql: dict) -> Optional[dict]:
    """
    Prima riga di una risposta shopifyqlQuery come dict {colonna: valore}, o None.

    ROBUSTO ai due formati di `rows` (JSON): l'API 2026-04 restituisce una LISTA DI
    OGGETTI già keyati per colonna (es. [{"sessions":"6097", ...}]); versioni/percorsi
    più vecchi usavano LISTE POSIZIONALI ([[...]]). Gestiamo entrambi.
    """
    if shopifyql_error(gql):
        return None
    table = ((gql.get("data") or {}).get("shopifyqlQuery") or {}).get("tableData") or {}
    rows = table.get("rows") or []
    if not rows:
        return None
    row0 = rows[0]
    if isinstance(row0, dict):
        return row0
    cols = [c.get("name") for c in table.get("columns", [])]
    return dict(zip(cols, row0))


def parse_session_conversion_rate(gql: dict) -> Optional[float]:
    """
    Estrae la CVR di negozio (frazione) dalla risposta GraphQL shopifyqlQuery.

    Preferisce la metrica nativa `conversion_rate` di Shopify (combacia col dashboard);
    in mancanza, calcola sessions_that_completed_checkout / sessions. None se i dati
    non sono disponibili o l'accesso è negato (scope read_reports mancante).
    """
    rec = _shopifyql_first_row(gql)
    if rec is None:
        return None

    def _f(key) -> float:
        try:
            return float(rec.get(key))
        except (TypeError, ValueError):
            return 0.0

    if rec.get("conversion_rate") not in (None, ""):
        return _f("conversion_rate")
    sessions = _f("sessions")
    completed = _f("sessions_that_completed_checkout")
    return (completed / sessions) if sessions > 0 else None


def parse_sessions_count(gql: dict) -> Optional[int]:
    """
    Estrae il numero di SESSIONI (visitatori) dalla risposta GraphQL shopifyqlQuery.
    None se dati non disponibili o accesso negato (scope read_reports mancante).
    """
    rec = _shopifyql_first_row(gql)
    if rec is None:
        return None
    try:
        return int(float(rec.get("sessions")))
    except (TypeError, ValueError):
        return None


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
        self._granted_scopes: list[str] = []
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
        # Gli scope EFFETTIVAMENTE concessi arrivano nel campo `scope` del grant.
        self._granted_scopes = [
            s.strip() for s in (data.get("scope") or "").split(",") if s.strip()
        ]
        # Rinnova ~5 minuti prima della scadenza per sicurezza.
        expires_in = int(data.get("expires_in", 3600))
        self._token_expiry = time.time() + max(expires_in - 300, 60)
        self._access_token = token
        return token

    def get_granted_scopes(self) -> list[str]:
        """
        Scope Admin API EFFETTIVAMENTE concessi al token corrente (dal grant, campo
        `scope`). Se assenti, li chiede all'endpoint /admin/oauth/access_scopes.json.
        Serve a /shopify_check per confermare read_all_orders / read_reports.
        """
        self._token()  # assicura il fetch del token (popola _granted_scopes)
        if self._granted_scopes:
            return self._granted_scopes
        try:
            url = f"https://{self.store}/admin/oauth/access_scopes.json"
            resp = self._request("GET", url)
            items = (resp.json() or {}).get("access_scopes") or []
            self._granted_scopes = [it.get("handle") for it in items if it.get("handle")]
        except Exception:  # noqa: BLE001 — best effort
            pass
        return self._granted_scopes

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

    # --------------------------------------------------------------- analytics
    def shopifyql(self, ql: str) -> dict:
        """
        Esegue una query ShopifyQL via GraphQL Admin API (richiede scope read_reports).
        Ritorna il JSON grezzo della risposta GraphQL.
        """
        gql = (
            "{ shopifyqlQuery(query: %s) { tableData { columns { name dataType } rows } "
            "parseErrors } }" % json.dumps(ql)
        )
        resp = self._request("POST", "/graphql.json", json={"query": gql})
        return resp.json()

    def get_session_conversion_rate(self, day: str) -> Optional[float]:
        """
        CVR di negozio Shopify per il giorno `day` (YYYY-MM-DD), come FRAZIONE
        (es. 0.0234 = 2.34%). Usa la definizione di Shopify (sessioni convertite /
        sessioni totali) per combaciare col dashboard. None se non disponibile
        (incl. scope read_reports mancante).

        NB: ShopifyQL usa il fuso orario del negozio (qui Europe/Rome), quindi il
        giorno combacia con quello del dashboard.
        """
        ql = (
            "FROM sessions "
            "SHOW sessions, sessions_that_completed_checkout, conversion_rate "
            f"SINCE {day} UNTIL {day}"
        )
        data = self.shopifyql(ql)
        return parse_session_conversion_rate(data)

    def get_sessions(self, day: str) -> Optional[int]:
        """
        VISITATORI reali (sessioni Shopify) per il giorno `day` (YYYY-MM-DD).
        Stesso percorso ShopifyQL della CVR (richiede scope read_reports): None se non
        disponibile / accesso negato -> la dashboard stimerà i visitatori (ordini÷CVR).
        """
        ql = f"FROM sessions SHOW sessions SINCE {day} UNTIL {day}"
        return parse_sessions_count(self.shopifyql(ql))

    def get_sessions_debug(self, day: str) -> tuple[Optional[int], Optional[str], dict]:
        """
        Come get_sessions ma NON inghiotte l'errore: ritorna (sessioni|None, errore|None,
        risposta_grezza). `errore` è il messaggio REALE (GraphQL/parseErrors) se la query
        fallisce. Usato da /shopify_check per distinguere errore-di-query da scope mancante.
        """
        ql = f"FROM sessions SHOW sessions SINCE {day} UNTIL {day}"
        gql = self.shopifyql(ql)
        return parse_sessions_count(gql), shopifyql_error(gql), gql
