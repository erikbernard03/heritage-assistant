"""
Attribuzione delle vendite per SORGENTE — LAST-CLICK dai dati di atterraggio dell'ordine
Shopify (deterministico, nessuna AI, nessuna rete).

Classifica ogni ordine leggendo, in ordine di priorità:
  1. i parametri UTM del `landing_site` (utm_source / utm_medium), con fallback ai
     note_attributes;
  2. il dominio di `referring_site` (referrer).

Bucket: meta · google_paid · google_organic · tiktok · pinterest · email · direct · other.
Per "other" si conserva la STRINGA GREZZA della sorgente (utm_source o dominio referrer), così
non si perde informazione.

ATTENZIONE (caveat metodologico): questa è attribuzione LAST-CLICK. Sotto-conta gli ads
rispetto ai numeri che Meta/Google si auto-attribuiscono (view-through, finestre più larghe,
click non taggati). Va letta come "dove è atterrato l'ordine", non come la verità sull'efficacia
degli ads. Confrontala con l'auto-attribuzione della piattaforma e col pixel Triple Whale.
"""
from __future__ import annotations

from urllib.parse import parse_qs, urlparse

# Bucket canonici (tutto il resto è "other" con stringa grezza conservata).
CANONICAL_SOURCES = (
    "meta", "google_paid", "google_organic", "tiktok", "pinterest", "email", "direct",
)

_META_SOURCES = {"facebook", "instagram", "fb", "ig", "meta", "facebook_ads", "ig_ads", "an"}
# NB: "faceb" cattura anche i referrer TRONCATI (es. "Faceb") oltre a facebook.com/l.facebook.
_META_REF = ("faceb", "instagram.com", "instagr", "fb.me", "l.facebook", "lm.facebook",
             "m.facebook", "l.instagram")
_TIKTOK_SOURCES = {"tiktok", "tiktok_ads", "tt", "ttclid"}
_PINTEREST_SOURCES = {"pinterest", "pin"}
_EMAIL_SOURCES = {"klaviyo", "email", "newsletter"}
_EMAIL_MEDIUMS = {"email", "e-mail", "flow", "campaign"}
# Klaviyo aggiunge questi query param ai link ANCHE quando il tracking UTM è disattivato:
# sono un segnale forte di click da email/flow Klaviyo.
_KLAVIYO_CLICK_PARAMS = ("_kx", "klclid")
_PAID_MEDIUMS = {"cpc", "ppc", "paid", "paidsearch", "paid_search", "paid-search",
                 "paid_social", "paidsocial"}

# Domini INTERNI / UTILITY del flusso d'acquisto: non sono mai una "sorgente" di traffico
# (la pagina ring-sizer su heritagering.com, il dominio myshopify dello store, e il widget
# esterno di ring-sizing ringsizer.app che compare nel checkout). Referrer interno/utility ->
# ignorato -> classifica per UTM, altrimenti "direct".
_INTERNAL_DOMAINS = ("heritagering.com", "ringsizer.app")


def _is_internal_domain(domain: str) -> bool:
    if not domain:
        return False
    d = domain.lower()
    if d.endswith(".myshopify.com"):
        return True
    for base in _INTERNAL_DOMAINS:
        if d == base or d.endswith("." + base):
            return True
    try:  # dominio dello store da settings (best-effort, non blocca la funzione pura)
        from config import settings

        store = (settings.SHOPIFY_STORE or "").lower()
        store = store.replace("https://", "").replace("http://", "").strip("/")
        if len(store) >= 4 and (d == store or store in d):
            return True
    except Exception:  # noqa: BLE001
        pass
    return False


def _to_float(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _landing_query(order: dict) -> dict:
    """Query param del landing_site come dict {chiave: [valori]} (minuscolo le chiavi)."""
    landing = order.get("landing_site") or order.get("landing_site_ref") or ""
    if not landing:
        return {}
    return {k.lower(): v for k, v in parse_qs(urlparse(landing).query).items()}


def _utm_from_order(order: dict) -> tuple[str, str]:
    """(utm_source, utm_medium) dal landing_site; fallback ai note_attributes. Minuscolo."""
    qs = _landing_query(order)
    src = (qs.get("utm_source") or [""])[0]
    med = (qs.get("utm_medium") or [""])[0]
    if not src or not med:
        for na in (order.get("note_attributes") or []):
            name = str(na.get("name") or "").lower()
            if name == "utm_source" and not src:
                src = str(na.get("value") or "")
            elif name == "utm_medium" and not med:
                med = str(na.get("value") or "")
    return src.strip().lower(), med.strip().lower()


def _has_klaviyo_click(order: dict) -> bool:
    """True se il landing_site porta un param di click Klaviyo (_kx / klclid)."""
    qs = _landing_query(order)
    return any(k in qs for k in _KLAVIYO_CLICK_PARAMS)


def _referrer_domain(order: dict) -> str:
    """Dominio del referring_site (senza www), minuscolo. '' se assente."""
    ref = order.get("referring_site") or ""
    if not ref:
        return ""
    netloc = (urlparse(ref).netloc or ref).lower().strip()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    return netloc


def classify_order(order: dict) -> str:
    """
    Bucket sorgente LAST-CLICK dell'ordine. UTM prima, dominio referrer come fallback.
    Ritorna un bucket canonico oppure, per gli sconosciuti, la stringa grezza della sorgente.
    """
    src, med = _utm_from_order(order)
    ref = _referrer_domain(order)

    # Referrer INTERNO (es. pagina ring-sizer di heritagering.com, o dominio myshopify):
    # non è mai una sorgente -> lo si ignora e si classifica per UTM, altrimenti "direct".
    if _is_internal_domain(ref):
        ref = ""

    # META (social a pagamento/organico: last-click non distingue, resta "meta")
    if src in _META_SOURCES or any(d in ref for d in _META_REF):
        return "meta"
    # TIKTOK
    if src in _TIKTOK_SOURCES or "tiktok.com" in ref:
        return "tiktok"
    # PINTEREST
    if src in _PINTEREST_SOURCES or "pinterest." in ref:
        return "pinterest"
    # EMAIL / KLAVIYO (utm klaviyo, medium email, o param di click _kx/klclid)
    if src in _EMAIL_SOURCES or med in _EMAIL_MEDIUMS or _has_klaviyo_click(order):
        return "email"
    # GOOGLE: paid vs organic
    is_google_src = (src == "google") or ("google." in ref) or (src == "googleads")
    is_paid = med in _PAID_MEDIUMS
    if src in ("google", "googleads") and is_paid:
        return "google_paid"
    if is_google_src and not is_paid:
        return "google_organic"
    # DIRECT: nessun referrer, nessun UTM
    if not src and not med and not ref:
        return "direct"
    # OTHER: conserva la stringa grezza (sorgente UTM o dominio referrer)
    return src or ref or "other"


def revenue_by_source(orders: list[dict]) -> dict[str, dict]:
    """
    {source: {orders, revenue}} dagli ordini del giorno (cancellati esclusi).
    Revenue in USD (total_price, come la revenue del report).
    """
    out: dict[str, dict] = {}
    for o in orders:
        if o.get("cancelled_at"):
            continue
        s = classify_order(o)
        acc = out.setdefault(s, {"revenue": 0.0, "orders": 0})
        acc["revenue"] += _to_float(o.get("total_price"))
        acc["orders"] += 1
    return out


def sales_by_source_by_month(rows: list[dict]) -> dict[str, dict[str, dict]]:
    """
    Aggrega le righe orders_by_source_daily per mese: {mese: {source: {revenue, orders}}}.
    `rows`: righe con day, source, revenue, orders.
    """
    by: dict[str, dict[str, dict]] = {}
    for r in rows:
        month = str(r.get("day", ""))[:7]
        source = r.get("source") or "other"
        acc = by.setdefault(month, {}).setdefault(source, {"revenue": 0.0, "orders": 0})
        acc["revenue"] += _to_float(r.get("revenue"))
        try:
            acc["orders"] += int(r.get("orders") or 0)
        except (TypeError, ValueError):
            pass
    return {k: by[k] for k in sorted(by)}


def aggregate_sources(by_source: dict[str, dict]) -> list[dict]:
    """
    Lista ordinata per revenue desc con la % sul totale: [{source, orders, revenue, pct}].
    `by_source`: {source: {orders, revenue}}.
    """
    total_rev = sum(_to_float(v.get("revenue")) for v in by_source.values()) or 0.0
    rows = [
        {"source": s, "orders": int(v.get("orders") or 0),
         "revenue": round(_to_float(v.get("revenue")), 2),
         "pct": (round(_to_float(v.get("revenue")) / total_rev * 100, 1) if total_rev else 0.0)}
        for s, v in by_source.items()
    ]
    return sorted(rows, key=lambda x: x["revenue"], reverse=True)


def top_sources(by_source: dict[str, dict], n: int = 4) -> list[dict]:
    """Prime `n` sorgenti per revenue (per la riga Telegram)."""
    return aggregate_sources(by_source)[:n]


# --------------------------------------------------------------------------- #
# ROLLUP a 4 gruppi headline — mapping CONFIGURABILE, così i bucket possono spostarsi tra
# gruppi senza toccare la logica.
#
#   FB ads     = bucket "meta" (last-click). Include l'organico Meta: senza UTM sugli ads non
#                è separabile dal traffico referral facebook/instagram, in gran parte a pagamento.
#   Google ads = "google_paid".
#   Klaviyo    = "email" (incl. click _kx/klclid).
#   Organic    = direct + motori di ricerca (google_organic/bing/yahoo/duckduckgo/brave) +
#                tiktok + pinterest + meta_organic (futuro) + qualsiasi altro/sconosciuto.
#
# È la vista LAST-CLICK. Le cifre auto-dichiarate da Meta/Google e il pixel Triple Whale
# restano nel confronto a 3 vie, NON in questo rollup.
# --------------------------------------------------------------------------- #
SOURCE_GROUPS: dict[str, tuple[str, ...]] = {
    "FB ads": ("meta",),
    "Google ads": ("google_paid",),
    "Klaviyo": ("email",),
    "Organic": ("direct", "google_organic", "tiktok", "pinterest", "meta_organic",
                "bing", "yahoo", "duckduckgo", "brave", "other"),
}
_GROUP_ORDER = ("FB ads", "Google ads", "Klaviyo", "Organic")


def group_of(source: str) -> str:
    """Gruppo di un bucket sorgente. Gli sconosciuti (stringhe grezze) -> Organic."""
    for group, buckets in SOURCE_GROUPS.items():
        if source in buckets:
            return group
    return "Organic"


def rollup_groups(by_source: dict[str, dict]) -> list[dict]:
    """
    Aggrega i bucket nei 4 gruppi headline: [{group, orders, revenue, pct}] in ordine
    FB ads, Google ads, Klaviyo, Organic. `by_source`: {source: {orders, revenue}}.
    """
    groups = {g: {"orders": 0, "revenue": 0.0} for g in _GROUP_ORDER}
    for source, v in by_source.items():
        g = group_of(source)
        groups[g]["orders"] += int(v.get("orders") or 0)
        groups[g]["revenue"] += _to_float(v.get("revenue"))
    total = sum(x["revenue"] for x in groups.values()) or 0.0
    return [
        {"group": g, "orders": groups[g]["orders"],
         "revenue": round(groups[g]["revenue"], 2),
         "pct": (round(groups[g]["revenue"] / total * 100, 1) if total else 0.0)}
        for g in _GROUP_ORDER
    ]


# --------------------------------------------------------------------------- #
# Triple Whale pixel (per canale) — aggregazione mensile (parsing nel connettore).
# --------------------------------------------------------------------------- #
def tw_pixel_by_month(rows: list[dict]) -> dict[str, dict[str, dict]]:
    """
    Aggrega tw_pixel_daily per mese: {mese: {channel: {orders, revenue}}}.
    `rows`: righe con day, channel, orders, revenue.
    """
    by: dict[str, dict[str, dict]] = {}
    for r in rows:
        month = str(r.get("day", ""))[:7]
        ch = r.get("channel") or "other"
        acc = by.setdefault(month, {}).setdefault(
            ch, {"orders": 0.0, "revenue": 0.0, "kind": r.get("kind") or "platform-reported"})
        acc["orders"] += _to_float(r.get("orders"))
        acc["revenue"] += _to_float(r.get("revenue"))
        if r.get("kind"):
            acc["kind"] = r.get("kind")
    return {k: by[k] for k in sorted(by)}
