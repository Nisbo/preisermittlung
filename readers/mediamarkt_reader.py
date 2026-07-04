from __future__ import annotations

import html
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, Optional


MEDIAMARKT_BASE_URL = "https://www.mediamarkt.de"
DESKTOP_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)
USER_AGENT_OVERRIDE = ""


def set_user_agent(user_agent: str) -> None:
    global USER_AGENT_OVERRIDE
    USER_AGENT_OVERRIDE = user_agent.strip()


def default_user_agent() -> str:
    return DESKTOP_USER_AGENT


def normalize_mediamarkt_url(url: str) -> str:
    cleaned = url.strip()
    if cleaned.startswith("/de/product/"):
        return MEDIAMARKT_BASE_URL + cleaned
    return cleaned


def article_number_from_url(url: str) -> str:
    parsed = urllib.parse.urlparse(url.strip())
    match = re.search(r"-(\d+)\.html$", parsed.path)
    return match.group(1) if match else ""


def euro_text(value: Optional[float]) -> Optional[str]:
    if value is None:
        return None
    total_cents = int(round(value * 100))
    euros, cents = divmod(total_cents, 100)
    return f"{euros},{cents:02d} €"


def cents(value: Optional[float]) -> Optional[int]:
    if value is None:
        return None
    return int(round(value * 100))


def get_html(url: str) -> str:
    request = urllib.request.Request(
        normalize_mediamarkt_url(url),
        headers={
            "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "accept-language": "de-DE,de;q=0.9,en;q=0.8",
            "user-agent": USER_AGENT_OVERRIDE or DESKTOP_USER_AGENT,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=25) as response:
            charset = response.headers.get_content_charset() or "utf-8"
            return response.read().decode(charset, errors="replace")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"MediaMarkt Fehler {exc.code} bei {url}: {body[:500]}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"MediaMarkt nicht erreichbar bei {url}: {exc}") from exc


def extract_json_ld(raw_html: str) -> Dict[str, Any]:
    for match in re.finditer(
        r'<script[^>]+type="application/ld\+json"[^>]*>(.*?)</script>',
        raw_html,
        flags=re.DOTALL,
    ):
        try:
            data = json.loads(html.unescape(match.group(1)))
        except json.JSONDecodeError:
            continue
        product = data.get("object") if isinstance(data, dict) else None
        if isinstance(product, dict) and product.get("@type") in {"Product", "ProductGroup"}:
            return product
    return {}


def extract_preloaded_state(raw_html: str) -> Dict[str, Any]:
    match = re.search(r"window\.__PRELOADED_STATE__ = (.*?);</script>", raw_html, flags=re.DOTALL)
    if not match:
        return {}
    raw = match.group(1).replace("undefined", "null")
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"MediaMarkt-State konnte nicht gelesen werden: {exc}") from exc


def first_image(json_ld: Dict[str, Any]) -> Optional[str]:
    image = json_ld.get("image")
    if isinstance(image, str):
        return image
    if isinstance(image, list):
        for item in image:
            if isinstance(item, str):
                return item
            if isinstance(item, dict) and item.get("url"):
                return str(item["url"])
    return None


def price_feature(apollo_state: Dict[str, Any], article_number: str) -> Dict[str, Any]:
    features = [
        value
        for value in apollo_state.values()
        if isinstance(value, dict)
        and value.get("__typename") == "CofrPriceFeature"
        and value.get("id") == f"Media:de:{article_number}"
    ]
    retail = next((item for item in features if not item.get("isProductOfTypeMarketplace")), None)
    if retail:
        return retail
    marketplace = next((item for item in features if item.get("isProductOfTypeMarketplace")), None)
    if marketplace:
        return marketplace
    raise RuntimeError(f"Kein MediaMarkt-Preis fuer Artikel {article_number} gefunden.")


def product_data(apollo_state: Dict[str, Any], article_number: str) -> Dict[str, Any]:
    product = apollo_state.get(f"GraphqlProduct:Media:de-DE:{article_number}")
    return product if isinstance(product, dict) else {}


def marketplace_offer(apollo_state: Dict[str, Any], article_number: str) -> Dict[str, Any]:
    for value in apollo_state.values():
        if (
            isinstance(value, dict)
            and value.get("__typename") == "GraphqlMarketplaceOfferV3"
            and str(value.get("productId")) == str(article_number)
            and str(value.get("type") or "").upper() != "MARKETPLACE_REFURBISHED"
        ):
            return value
    return {}


def offer_from_json_ld(json_ld: Dict[str, Any]) -> Dict[str, Any]:
    offers = json_ld.get("offers") or []
    if isinstance(offers, list) and offers:
        return offers[0] if isinstance(offers[0], dict) else {}
    return offers if isinstance(offers, dict) else {}


def read_mediamarkt_product(product: Dict[str, str], _market: Dict[str, Any], _postal_code: str = "") -> Dict[str, Any]:
    url = normalize_mediamarkt_url(product.get("product_url") or product.get("url") or "")
    if not url:
        raise RuntimeError("MediaMarkt-Produkt braucht product_url.")

    raw_html = get_html(url)
    if "vervollständigen Sie bitte nachfolgendes Captcha" in raw_html or "cf_chl" in raw_html:
        raise RuntimeError("MediaMarkt liefert eine Captcha-Seite. Browserkennung oder Abrufintervall pruefen.")

    article_number = product.get("article_number") or article_number_from_url(url)
    if not article_number:
        raise RuntimeError("MediaMarkt-Artikelnummer konnte nicht aus der URL gelesen werden.")

    json_ld = extract_json_ld(raw_html)
    state = extract_preloaded_state(raw_html)
    apollo_state = state.get("apolloState") or {}
    product_info = product_data(apollo_state, article_number)
    price_info = price_feature(apollo_state, article_number)
    price = (price_info.get("price") or {}).get("amount")
    strike = price_info.get("strikePrice") or {}
    strike_price = strike.get("amount") if isinstance(strike, dict) else None
    offer = marketplace_offer(apollo_state, article_number) or offer_from_json_ld(json_ld)

    if price is None:
        offer_price = offer.get("price") if isinstance(offer, dict) else None
        if offer_price is not None:
            price = float(offer_price)
    if price is None:
        raise RuntimeError(f"Kein MediaMarkt-Preis fuer Artikel {article_number} gefunden.")

    price_value = float(price)
    strike_value = float(strike_price) if strike_price is not None else None
    seller = offer.get("sellerName") if isinstance(offer, dict) else None
    if not seller and isinstance(offer, dict) and isinstance(offer.get("seller"), dict):
        seller = offer["seller"].get("name")

    title = product_info.get("displayName") or json_ld.get("name") or product.get("name") or article_number
    image_url = first_image(json_ld)
    price_cents = cents(price_value)

    return {
        "id": product["id"],
        "name": product.get("name") or title,
        "title": title,
        "article_number": article_number,
        "provider_article_number": article_number,
        "price": price_value,
        "price_cents": price_cents,
        "price_text": euro_text(price_value),
        "currency": "EUR",
        "old_price": strike_value,
        "old_price_cents": cents(strike_value),
        "old_price_text": euro_text(strike_value),
        "discount": (price_info.get("price") or {}).get("discount"),
        "discount_percentage": (price_info.get("price") or {}).get("discountPercentage"),
        "unit_price": None,
        "available_service": "ONLINE",
        "market_id": "online",
        "seller": seller or ("MediaMarkt" if not price_info.get("isProductOfTypeMarketplace") else None),
        "url": url,
        "image_url": image_url,
    }
