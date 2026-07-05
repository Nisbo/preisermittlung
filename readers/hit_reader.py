from __future__ import annotations

import html
import json
import math
import re
import urllib.error
import urllib.parse
import urllib.request
from http.cookiejar import CookieJar
from typing import Any, Dict, List, Optional

from core.price_details import normalize_price_details


HIT_BASE_URL = "https://www.hit.de"
ZIPPOPOTAMUS_URL = "https://api.zippopotam.us/de/{postal_code}"
DESKTOP_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)
USER_AGENT_OVERRIDE = ""


def set_user_agent(user_agent: str) -> None:
    global USER_AGENT_OVERRIDE
    USER_AGENT_OVERRIDE = user_agent.strip()


def default_headers() -> Dict[str, str]:
    return {
        "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "accept-language": "de-DE,de;q=0.9,en;q=0.7",
        "cache-control": "no-cache",
        "pragma": "no-cache",
        "user-agent": USER_AGENT_OVERRIDE or DESKTOP_USER_AGENT,
    }


def get_text(url: str, cookie_jar: Optional[CookieJar] = None) -> str:
    request = urllib.request.Request(url, headers=default_headers())
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cookie_jar)) if cookie_jar else urllib.request.build_opener()
    try:
        with opener.open(request, timeout=25) as response:
            charset = response.headers.get_content_charset() or "utf-8"
            return response.read().decode(charset, errors="replace")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HIT Fehler {exc.code} bei {url}: {body[:500]}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"HIT nicht erreichbar bei {url}: {exc}") from exc


def normalize_hit_url(url: str) -> str:
    cleaned = url.strip()
    if cleaned.startswith("/sortiment/") or cleaned.startswith("/maerkte/"):
        return HIT_BASE_URL + cleaned
    return cleaned


def article_number_from_url(url: str) -> str:
    cleaned = normalize_hit_url(url)
    match = re.search(r"/([^/?#]+?-\d+ST)(?:[?#]|$)", cleaned)
    if match:
        return match.group(1).rsplit("-", 1)[-1]
    match = re.search(r"(\d+ST)(?:[?#]|$)", cleaned)
    return match.group(1) if match else ""


def add_store_to_url(url: str, store_id: str) -> str:
    parsed = urllib.parse.urlparse(normalize_hit_url(url))
    query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
    if store_id:
        query["store"] = [store_id]
    return urllib.parse.urlunparse(parsed._replace(query=urllib.parse.urlencode(query, doseq=True)))


def cents_from_value(value: Any) -> Optional[int]:
    if value is None:
        return None
    text = str(value).strip()
    if "," in text:
        text = text.replace(".", "").replace(",", ".")
    try:
        return int(round(float(text) * 100))
    except ValueError:
        return None


def cents_from_price_parts(euro: Any, cent: Any) -> Optional[int]:
    if euro is None or cent is None:
        return None
    try:
        return int(str(euro).strip()) * 100 + int(str(cent).strip().zfill(2)[:2])
    except ValueError:
        return None


def euro_text_from_cents(value: Optional[int]) -> Optional[str]:
    if value is None:
        return None
    euros, cents = divmod(int(value), 100)
    return f"{euros},{cents:02d} €"


def clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", html.unescape(str(value or ""))).strip()


def extract_data_attribute(raw_html: str, component: str, attribute: str) -> Any:
    component_match = re.search(
        rf'<[^>]+data-component="{re.escape(component)}"[^>]*>',
        raw_html,
        flags=re.DOTALL,
    )
    if not component_match:
        return None
    tag = component_match.group(0)
    attr_match = re.search(rf'{re.escape(attribute)}="([^"]*)"', tag, flags=re.DOTALL)
    if not attr_match:
        return None
    return json.loads(html.unescape(attr_match.group(1)))


def extract_product_data(raw_html: str) -> Dict[str, Any]:
    data = extract_data_attribute(raw_html, "assortment/leaflet/leaflet", "data-leaflet")
    if isinstance(data, dict):
        return data
    fallback = re.search(r'data-leaflet="([^"]+)"', raw_html, flags=re.DOTALL)
    if fallback:
        return json.loads(html.unescape(fallback.group(1)))
    raise RuntimeError("Kein HIT-Produktdatenblock gefunden.")


def extract_list_items(raw_html: str) -> List[Dict[str, Any]]:
    data = extract_data_attribute(raw_html, "assortment/list", "data-data")
    if not isinstance(data, dict):
        return []
    items = data.get("data") or data.get("items") or data.get("articles") or data.get("products") or []
    if isinstance(items, list):
        return [item for item in items if isinstance(item, dict)]
    return []


def extract_head_store_id(raw_html: str) -> str:
    match = re.search(r'<head[^>]*data-store-id="([^"]*)"', raw_html)
    return match.group(1) if match else ""


def market_slug_from_url(url: str) -> str:
    path = urllib.parse.urlparse(url).path.rstrip("/")
    return path.rsplit("/", 1)[-1] if path else ""


def market_url_with_select(url: str) -> str:
    parsed = urllib.parse.urlparse(normalize_hit_url(url))
    query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
    query["mein-markt"] = ["1"]
    return urllib.parse.urlunparse(parsed._replace(query=urllib.parse.urlencode(query, doseq=True)))


def resolve_market_id_from_url(url: str) -> str:
    cookie_jar = CookieJar()
    raw_html = get_text(market_url_with_select(url), cookie_jar)
    store_id = extract_head_store_id(raw_html)
    if store_id:
        return store_id
    for cookie in cookie_jar:
        if cookie.name == "mein-markt" and cookie.value:
            return str(cookie.value)
    raise RuntimeError("HIT Store-ID konnte nicht ermittelt werden.")


def geocode_postal_code(postal_code: str) -> Optional[Dict[str, float]]:
    url = ZIPPOPOTAMUS_URL.format(postal_code=urllib.parse.quote(postal_code))
    try:
        data = json.loads(get_text(url))
    except Exception:
        return None
    places = data.get("places") if isinstance(data, dict) else None
    if not places:
        return None
    first = places[0]
    try:
        return {"latitude": float(first["latitude"]), "longitude": float(first["longitude"])}
    except (KeyError, TypeError, ValueError):
        return None


def distance_km(origin: Dict[str, float], location: Dict[str, Any]) -> Optional[float]:
    try:
        lat1 = math.radians(float(origin["latitude"]))
        lon1 = math.radians(float(origin["longitude"]))
        lat2 = math.radians(float(location["latitude"]))
        lon2 = math.radians(float(location["longitude"]))
    except (KeyError, TypeError, ValueError):
        return None
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * 6371 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def market_to_config(store: Dict[str, Any], postal_code: str, origin: Optional[Dict[str, float]]) -> Dict[str, str]:
    url = str(store.get("url") or "")
    store_id = resolve_market_id_from_url(url)
    distance = distance_km(origin, store.get("location") or {}) if origin else None
    name = clean_text(store.get("name")) or store_id
    return {
        "provider": "hit",
        "market_id": store_id,
        "postal_code": clean_text(store.get("zip")),
        "service": "store",
        "market_name": name,
        "market_company": "HIT",
        "market_street": clean_text(store.get("street")),
        "market_city": clean_text(store.get("city")),
        "market_match": market_slug_from_url(url),
        "market_match_2": clean_text(f"{store.get('zip') or ''} {store.get('city') or ''}"),
        "hit_market_url": normalize_hit_url(url),
        "hit_search_postal_code": postal_code,
        "hit_use_app_price": "false",
        "distance_km": f"{distance:.1f}" if distance is not None else "",
    }


def find_hit_markets_by_postal_code(postal_code: str) -> List[Dict[str, str]]:
    raw_html = get_text(f"{HIT_BASE_URL}/maerkte")
    stores = extract_data_attribute(raw_html, "store/marketfinder/marketfinder", "data-stores")
    if not isinstance(stores, list):
        raise RuntimeError("HIT-Marktliste konnte nicht gelesen werden.")
    origin = geocode_postal_code(postal_code)
    rows: List[tuple[float, Dict[str, Any]]] = []
    for store in stores:
        if not isinstance(store, dict):
            continue
        exact_score = 0.0 if str(store.get("zip") or "") == postal_code else 10000.0
        distance = distance_km(origin, store.get("location") or {}) if origin else None
        rows.append((exact_score + (distance if distance is not None else 5000.0), store))
    results = []
    for _score, store in sorted(rows, key=lambda item: item[0])[:12]:
        results.append(market_to_config(store, postal_code, origin))
    return results


def item_image_url(item: Dict[str, Any]) -> Optional[str]:
    image = item.get("image") or item.get("productImage") or item.get("picture")
    if isinstance(image, str):
        return image
    if isinstance(image, dict):
        for key in ("desktop", "tablet", "mobile", "src", "url"):
            if image.get(key):
                return str(image[key])
    images = item.get("images")
    if isinstance(images, list):
        for entry in images:
            if isinstance(entry, str):
                return entry
            if isinstance(entry, dict):
                for key in ("desktop", "tablet", "mobile", "src", "url"):
                    if entry.get(key):
                        return str(entry[key])
    return None


def item_url(item: Dict[str, Any]) -> str:
    url = str(item.get("url") or item.get("href") or "")
    return normalize_hit_url(url) if url else ""


def price_from_product_data(data: Dict[str, Any], use_app_price: bool) -> Dict[str, Any]:
    tag = data.get("priceTag") if isinstance(data.get("priceTag"), dict) else {}
    normal_cents = cents_from_value(data.get("price")) or cents_from_price_parts(tag.get("priceEuro"), tag.get("priceCent"))
    app_cents = cents_from_value(data.get("appPrice")) or cents_from_price_parts(tag.get("appPriceEuro"), tag.get("appPriceCent"))
    selected_cents = app_cents if use_app_price and app_cents is not None else normal_cents
    if selected_cents is None:
        raise RuntimeError("Kein HIT-Preis gefunden.")
    old_cents = normal_cents if use_app_price and app_cents is not None and normal_cents != app_cents else None
    unit_price = clean_text(tag.get("appBasePriceText") if use_app_price and app_cents is not None else tag.get("basePriceText"))
    if not unit_price:
        unit_price = clean_text(data.get("appPriceBelowString") if use_app_price and app_cents is not None else data.get("basePriceText"))
    unit_price = unit_price.replace("=", " = ").replace("€", " €")
    unit_price = re.sub(r"\s+", " ", unit_price).strip()
    return {
        "price_cents": selected_cents,
        "old_price_cents": old_cents,
        "unit_price": unit_price or None,
    }


def read_hit_product(product: Dict[str, str], market: Dict[str, Any], _postal_code: str = "") -> Dict[str, Any]:
    store_id = str(market.get("market_id") or product.get("market_id") or "").strip()
    url = product.get("product_url") or product.get("url") or ""
    if not url:
        raise RuntimeError("HIT-Produkt braucht product_url.")
    url = add_store_to_url(url, store_id)
    raw_html = get_text(url)
    data = extract_product_data(raw_html)
    use_app_price = str(market.get("hit_use_app_price") or "").lower() in {"1", "true", "yes", "on"}
    prices = price_from_product_data(data, use_app_price)
    price_cents = prices["price_cents"]
    old_price_cents = prices["old_price_cents"]
    title = clean_text(data.get("headline")) or clean_text(data.get("title")) or product.get("name") or article_number_from_url(url)
    article_number = clean_text(data.get("external_id")) or product.get("article_number") or article_number_from_url(url)
    overview = clean_text(data.get("overview"))
    image_url = item_image_url(data)
    price_details = normalize_price_details(package_size=overview, unit_price=prices.get("unit_price"))
    return {
        "id": product["id"],
        "name": product.get("name") or title or article_number,
        "title": title or product.get("name") or article_number,
        "article_number": article_number,
        "provider_article_number": article_number,
        "price": price_cents / 100,
        "price_cents": price_cents,
        "price_text": euro_text_from_cents(price_cents),
        "currency": "EUR",
        "old_price": old_price_cents / 100 if old_price_cents is not None else None,
        "old_price_cents": old_price_cents,
        "old_price_text": euro_text_from_cents(old_price_cents),
        "unit_price": prices.get("unit_price"),
        **price_details,
        "available_service": "STORE",
        "availability": clean_text(data.get("inventory")),
        "market_id": store_id,
        "seller": "HIT",
        "url": url,
        "image_url": image_url,
    }


def list_hit_products(url: str, market: Dict[str, Any]) -> List[Dict[str, Any]]:
    store_id = str(market.get("market_id") or "").strip()
    raw_html = get_text(add_store_to_url(url, store_id))
    items = extract_list_items(raw_html)
    candidates = []
    use_app_price = str(market.get("hit_use_app_price") or "").lower() in {"1", "true", "yes", "on"}
    for index, item in enumerate(items):
        article_number = clean_text(item.get("external_id")) or clean_text(item.get("id"))
        item_product_url = item_url(item)
        if not article_number or not item_product_url:
            continue
        try:
            prices = price_from_product_data(item, use_app_price)
            price_text = euro_text_from_cents(prices["price_cents"])
        except Exception:
            price_text = ""
        candidates.append(
            {
                "index": index,
                "article_number": article_number,
                "title": clean_text(item.get("headline")) or article_number,
                "overview": clean_text(item.get("overview")),
                "price_text": price_text,
                "url": add_store_to_url(item_product_url, store_id),
                "image_url": item_image_url(item),
            }
        )
    return candidates
