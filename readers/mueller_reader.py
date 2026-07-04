from __future__ import annotations

import html
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, Optional

from core.price_details import normalize_price_details


MUELLER_BASE_URL = "https://www.mueller.de"
DESKTOP_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)
USER_AGENT_OVERRIDE = ""


def set_user_agent(user_agent: str) -> None:
    global USER_AGENT_OVERRIDE
    USER_AGENT_OVERRIDE = user_agent.strip()


def get_html(url: str) -> str:
    request = urllib.request.Request(
        normalize_mueller_url(url),
        headers={
            "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "accept-language": "de-DE,de;q=0.9,en;q=0.7",
            "user-agent": USER_AGENT_OVERRIDE or DESKTOP_USER_AGENT,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            charset = response.headers.get_content_charset() or "utf-8"
            return response.read().decode(charset, errors="replace")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Mueller Fehler {exc.code} bei {url}: {body[:500]}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Mueller nicht erreichbar bei {url}: {exc}") from exc


def normalize_mueller_url(url: str) -> str:
    cleaned = url.strip()
    if cleaned.startswith("/p/"):
        return MUELLER_BASE_URL + cleaned
    return cleaned


def article_number_from_url(url: str) -> str:
    cleaned = url.strip().rstrip("/")
    parsed = urllib.parse.urlparse(cleaned)
    query = urllib.parse.parse_qs(parsed.query)
    if query.get("itemId") and query["itemId"][0]:
        return query["itemId"][0]
    cleaned_path = parsed.path.rstrip("/")
    match = re.search(r"-(?:IPN|PPN)?([A-Za-z0-9]+)$", cleaned_path)
    if match:
        return match.group(1)
    fallback = re.search(r"/([^/]+)$", cleaned_path)
    return fallback.group(1) if fallback else ""


def euro_text(value: Optional[float]) -> Optional[str]:
    if value is None:
        return None
    euros = int(round(value * 100))
    whole, cents = divmod(euros, 100)
    return f"{whole},{cents:02d} EUR".replace(" EUR", " €")


def cents(value: Optional[float]) -> Optional[int]:
    if value is None:
        return None
    return int(round(value * 100))


def number(pattern: str, text: str) -> Optional[float]:
    match = re.search(pattern, text)
    return float(match.group(1)) if match else None


def text_value(pattern: str, text: str) -> Optional[str]:
    match = re.search(pattern, text)
    return html.unescape(match.group(1)) if match else None


def product_chunk(raw_html: str, preferred_code: str = "") -> str:
    decoded = raw_html.replace('\\"', '"')
    position = -1
    if preferred_code:
        code_position = decoded.find(f'"code":"{preferred_code}"')
        if code_position >= 0:
            price_position = decoded.find('"currentPrice"', code_position)
            next_code_position = decoded.find('"code":"', code_position + 8)
            if price_position >= 0 and (next_code_position < 0 or price_position < next_code_position):
                position = price_position
    if position < 0:
        position = decoded.find('"currentPrice"')
    if position < 0:
        raise RuntimeError("Kein Mueller-Preisblock gefunden.")
    return decoded[max(0, position - 12000) : position + 18000]


def extract_base_price(chunk: str) -> Optional[str]:
    match = re.search(
        r'"basePrice":\{"value":([0-9.]+),"capacity":\{"unitCode":"([^"]+)","value":"?([^,"}]+)',
        chunk,
    )
    if not match:
        return None
    value = euro_text(float(match.group(1)))
    unit = match.group(2).lower()
    capacity = match.group(3)
    unit_labels = {
        "kg": "kg",
        "liter": "l",
        "l": "l",
        "g": "g",
        "ml": "ml",
    }
    return f"{value} / {capacity} {unit_labels.get(unit, unit)}" if value else None


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
        if isinstance(data, dict) and data.get("@type") == "Product":
            return data
    return {}


def extract_image_url(chunk: str, json_ld: Dict[str, Any]) -> Optional[str]:
    match = re.search(r'"images":\[(.*?)\],"manufacturer"', chunk, flags=re.DOTALL)
    image_block = match.group(1) if match else chunk
    for image_url in re.findall(r'"url":"([^"]+)"', image_block):
        if image_url.startswith("https://static.prod.ecom.mueller.de/products/"):
            return html.unescape(image_url)
    for image_url in re.findall(r'"url":"([^"]+)"', image_block):
        if image_url.startswith("https://static.prod.ecom.mueller.de/_default_upload_bucket/"):
            return html.unescape(image_url)

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


def read_mueller_product(product: Dict[str, str], _market: Dict[str, Any], _postal_code: str = "") -> Dict[str, Any]:
    url = normalize_mueller_url(product.get("product_url") or product.get("url") or "")
    if not url:
        raise RuntimeError("Mueller-Produkt braucht product_url.")

    raw_html = get_html(url)
    preferred_code = product.get("article_number") or article_number_from_url(url)
    chunk = product_chunk(raw_html, preferred_code)
    json_ld = extract_json_ld(raw_html)
    offers = json_ld.get("offers") or []
    first_offer = offers[0] if isinstance(offers, list) and offers else {}

    current_price = number(
        r'"currentPrice":\{"currencyIso":"EUR","valueWithoutTax":[0-9.]+,"valueWithTax":([0-9.]+)',
        chunk,
    )
    old_price = number(
        r'"recommendedRetailPrice":\{"currencyIso":"EUR","valueWithoutTax":[0-9.]+,"valueWithTax":([0-9.]+)',
        chunk,
    )
    if current_price is None and first_offer.get("price") is not None:
        current_price = float(first_offer["price"])

    if current_price is None:
        raise RuntimeError(f"Kein Mueller-Preis fuer {url} gefunden.")

    code_candidates = re.findall(r'"code":"([^"]+)"', chunk[:12000])
    code = code_candidates[-1] if code_candidates else article_number_from_url(url)
    name = text_value(r'"name":"([^"]+)"', chunk[chunk.find('"currentPrice"') :]) or json_ld.get("name")
    stock_level = text_value(r'"stockLevel":([0-9]+)', chunk)
    base_price = extract_base_price(chunk)
    image_url = extract_image_url(chunk, json_ld)
    price_details = normalize_price_details(unit_price=base_price)

    price_cents = cents(current_price)
    old_price_cents = cents(old_price)
    return {
        "id": product["id"],
        "name": product.get("name") or name or code,
        "title": name or product.get("name") or code,
        "article_number": code,
        "provider_article_number": code,
        "price": current_price,
        "price_cents": price_cents,
        "price_text": euro_text(current_price),
        "currency": "EUR",
        "old_price": old_price,
        "old_price_cents": old_price_cents,
        "old_price_text": euro_text(old_price),
        "unit_price": base_price,
        **price_details,
        "available_service": "ONLINE",
        "stock_level": int(stock_level) if stock_level else None,
        "market_id": "online",
        "url": url,
        "image_url": image_url,
    }
