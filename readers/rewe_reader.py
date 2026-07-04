from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional

from core.price_details import normalize_price_details


REWE_BASE_URL = "https://www.rewe.de"
DESKTOP_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)
USER_AGENT_OVERRIDE = ""


def set_user_agent(user_agent: str) -> None:
    global USER_AGENT_OVERRIDE
    USER_AGENT_OVERRIDE = user_agent.strip()


def get_json(path: str, query: Optional[Dict[str, Any]] = None, headers: Optional[Dict[str, str]] = None) -> Any:
    url = REWE_BASE_URL + path
    if query:
        url += "?" + urllib.parse.urlencode(query, doseq=True)

    request = urllib.request.Request(
        url,
        headers={
            "accept": "application/json",
            "user-agent": USER_AGENT_OVERRIDE or DESKTOP_USER_AGENT,
            **(headers or {}),
        },
    )

    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            charset = response.headers.get_content_charset() or "utf-8"
            return json.loads(response.read().decode(charset))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"REWE API Fehler {exc.code} bei {path}: {body[:500]}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"REWE API nicht erreichbar bei {path}: {exc}") from exc


def normalize(text: Any) -> str:
    return " ".join(str(text or "").lower().split())


def market_text(market: Dict[str, Any]) -> str:
    parts = [
        market.get("displayName"),
        market.get("companyName"),
        market.get("street"),
        market.get("zipCode"),
        market.get("city"),
        market.get("pickupVariant"),
        market.get("wwIdent"),
    ]
    return " ".join(str(part) for part in parts if part)


def market_to_config(market: Dict[str, Any], fallback_postal_code: str = "") -> Dict[str, str]:
    return {
        "provider": str(market.get("provider") or "rewe"),
        "market_id": str(market.get("wwIdent") or market.get("market_id") or ""),
        "postal_code": str(market.get("zipCode") or market.get("postal_code") or fallback_postal_code),
        "service": "pickup",
        "market_name": str(market.get("displayName") or market.get("market_name") or "REWE Markt"),
        "market_company": str(market.get("companyName") or market.get("market_company") or "REWE"),
        "market_street": str(market.get("street") or market.get("market_street") or ""),
        "market_city": str(market.get("city") or market.get("market_city") or ""),
        "market_match": str(market.get("street") or market.get("market_match") or ""),
        "market_match_2": " ".join(
            part
            for part in [str(market.get("zipCode") or fallback_postal_code or ""), str(market.get("city") or "")]
            if part
        ),
    }


def find_pickup_markets_by_postal_code(postal_code: str) -> List[Dict[str, str]]:
    markets = get_json(f"/api/marketselection/zipcodes/{postal_code}/services/pickup")
    if not isinstance(markets, list):
        raise RuntimeError("Unerwartete Antwort der REWE-Marktsuche.")
    return [market_to_config(market, postal_code) for market in markets]


def store_from_market(market: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "market_id": market.get("wwIdent"),
        "name": market.get("displayName"),
        "company": market.get("companyName"),
        "street": market.get("street"),
        "zip": market.get("zipCode"),
        "city": market.get("city"),
        "service": market.get("pickupVariant"),
    }


def find_pickup_market(store: Dict[str, str]) -> Dict[str, Any]:
    if store.get("market_id"):
        return {
            "provider": store.get("provider") or "rewe",
            "wwIdent": store["market_id"],
            "displayName": store.get("market_name") or "REWE Markt",
            "companyName": store.get("market_company") or "REWE",
            "street": store.get("market_street") or store.get("market_match"),
            "zipCode": store.get("postal_code"),
            "city": store.get("market_city") or store.get("market_match_2"),
            "pickupVariant": "Abholservice",
        }

    markets = get_json(f"/api/marketselection/zipcodes/{store['postal_code']}/services/pickup")
    if not isinstance(markets, list):
        raise RuntimeError("Unerwartete Antwort der REWE-Marktsuche.")

    match_1 = normalize(store["market_match"])
    match_2 = normalize(store.get("market_match_2"))
    matches = []

    for market in markets:
        text = normalize(market_text(market))
        if match_1 in text and (not match_2 or match_2 in text):
            matches.append(market)

    if not matches:
        found = [market_text(market) for market in markets[:10]]
        raise RuntimeError("Kein passender Markt gefunden. Erste Treffer:\n- " + "\n- ".join(found))
    if len(matches) > 1:
        found = [market_text(market) for market in matches]
        raise RuntimeError("Mehrere passende Maerkte gefunden. market_match genauer machen:\n- " + "\n- ".join(found))

    return matches[0]


def markets_from_config(config: Dict[str, Any]) -> List[Dict[str, str]]:
    markets = [dict(market) for market in config.get("markets") or []]
    store = config.get("store") or {}
    if store.get("market_id") and not any(market.get("market_id") == store.get("market_id") for market in markets):
        markets.insert(0, market_to_config(store, store.get("postal_code", "")))
    return markets


def market_for_product(config: Dict[str, Any], product: Dict[str, str]) -> Dict[str, str]:
    markets = markets_from_config(config)
    market_id = product.get("market_id")
    provider = product.get("provider") or "rewe"
    if market_id:
        for market in markets:
            market_provider = market.get("provider") or "rewe"
            if str(market.get("market_id")) == str(market_id) and (not provider or provider == market_provider):
                return market
    raise RuntimeError(f"Kein REWE-Markt fuer Artikel {product.get('id') or product.get('article_number')} konfiguriert.")


def format_euro(cents: int) -> str:
    euros, remainder = divmod(cents, 100)
    return f"{euros},{remainder:02d} €"


def read_rewe_product(product: Dict[str, str], store: Dict[str, Any], postal_code: str) -> Dict[str, Any]:
    article_number = product["article_number"]
    market_id = str(store["wwIdent"])
    data = get_json(
        "/shop/api/products",
        query={
            "search": article_number,
            "objectsPerPage": 10,
            "page": 1,
            "serviceTypes": "PICKUP",
            "marketId": market_id,
        },
        headers={
            "accept": "application/vnd.rewe.digital.products+json;client=web;version=2",
            "x-rd-market-id": market_id,
            "x-rd-chosen-service": "PICKUP",
            "x-rd-customer-zip": postal_code,
        },
    )

    hits = data.get("hits") if isinstance(data, dict) else None
    if not hits:
        raise RuntimeError(f"Kein Treffer fuer Artikel {article_number}.")

    hit = next((item for item in hits if str(item.get("productId")) == article_number), hits[0])
    pricing = hit.get("pricing") or {}
    price_cents = pricing.get("currentRetailPrice")
    if price_cents is None:
        raise RuntimeError(f"Kein Preis fuer Artikel {article_number} im Markt {market_id}.")

    details_url = str(hit.get("detailsUrl", f"/shop/productList?search={article_number}"))
    if details_url.startswith("/p/"):
        details_url = "/shop" + details_url

    price_details = normalize_price_details(unit_price=pricing.get("grammage"))

    return {
        "id": product["id"],
        "name": product.get("name") or hit.get("title"),
        "title": hit.get("title"),
        "article_number": article_number,
        "price": price_cents / 100,
        "price_cents": price_cents,
        "price_text": format_euro(int(price_cents)),
        "currency": "EUR",
        "unit_price": pricing.get("grammage"),
        **price_details,
        "available_service": hit.get("serviceType"),
        "market_id": market_id,
        "url": REWE_BASE_URL + details_url,
        "image_url": hit.get("imageURL"),
    }


def read_prices(config: Dict[str, Any]) -> Dict[str, Any]:
    products = []
    first_market = None
    for product in config["products"]:
        store_config = market_for_product(config, product)
        market = find_pickup_market(store_config)
        first_market = first_market or market
        products.append(read_rewe_product(product, market, store_config["postal_code"]))

    return {
        "ok": True,
        "store": store_from_market(first_market) if first_market else None,
        "products": products,
    }
