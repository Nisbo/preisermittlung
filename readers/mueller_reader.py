from __future__ import annotations

import html
import json
import re
import shutil
import subprocess
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


class MuellerBlockedError(RuntimeError):
    pass


def set_user_agent(user_agent: str) -> None:
    global USER_AGENT_OVERRIDE
    USER_AGENT_OVERRIDE = user_agent.strip()


def get_html(url: str) -> str:
    request = urllib.request.Request(
        normalize_mueller_url(url),
        headers={
            "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "accept-language": "de-DE,de;q=0.9,en;q=0.7",
            "cache-control": "no-cache",
            "pragma": "no-cache",
            "user-agent": USER_AGENT_OVERRIDE or DESKTOP_USER_AGENT,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            charset = response.headers.get_content_charset() or "utf-8"
            return response.read().decode(charset, errors="replace")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        if exc.code == 403 and ("_fs-ch-" in body or "Client Challenge" in body):
            raise MuellerBlockedError(f"Mueller Client-Challenge bei {url}.") from exc
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


def float_value(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return None


def number(pattern: str, text: str) -> Optional[float]:
    match = re.search(pattern, text)
    return float(match.group(1)) if match else None


def german_price_value(value: str) -> Optional[float]:
    cleaned = value.strip().replace(".", "").replace(",", ".")
    return float_value(cleaned)


def price_pattern_for(value: float) -> str:
    return re.escape(euro_text(value).replace(" €", "")).replace(",", r"\s*,\s*") + r"\s*€"


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


def visible_text(raw_html: str) -> str:
    text = re.sub(r"<script\b[^>]*>.*?</script>", " ", raw_html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<style\b[^>]*>.*?</style>", " ", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", html.unescape(text))


def extract_visible_offer_price(raw_html: str, old_price: Optional[float]) -> Optional[float]:
    text = visible_text(raw_html)
    if old_price is not None:
        old_pattern = price_pattern_for(old_price)
        match = re.search(rf"([0-9]+(?:[.,][0-9]{{2}})?)\s*€\s*UVP\s*{old_pattern}", text, flags=re.IGNORECASE)
        if match:
            return german_price_value(match.group(1))
        match = re.search(rf"UVP\s*{old_pattern}\s*([0-9]+(?:[.,][0-9]{{2}})?)\s*€(?!\s*/)", text, flags=re.IGNORECASE)
        if match:
            return german_price_value(match.group(1))
    match = re.search(r"([0-9]+(?:[.,][0-9]{2})?)\s*€\s*UVP\s*[0-9]+(?:[.,][0-9]{2})?\s*€", text, flags=re.IGNORECASE)
    if match:
        return german_price_value(match.group(1))
    return None


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


def reader_url(url: str) -> str:
    return "https://r.jina.ai/http://" + normalize_mueller_url(url)


def get_reader_markdown(url: str) -> str:
    target_url = reader_url(url)
    request = urllib.request.Request(
        target_url,
        headers={
            "accept": "text/plain,text/markdown,*/*",
            "accept-language": "de-DE,de;q=0.9,en;q=0.7",
            "user-agent": USER_AGENT_OVERRIDE or DESKTOP_USER_AGENT,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            charset = response.headers.get_content_charset() or "utf-8"
            return response.read().decode(charset, errors="replace")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        curl_result = get_reader_markdown_with_curl(target_url)
        if curl_result:
            return curl_result
        raise RuntimeError(f"Mueller Reader-Fehler {exc.code} bei {url}: {body[:300]}") from exc
    except urllib.error.URLError as exc:
        curl_result = get_reader_markdown_with_curl(target_url)
        if curl_result:
            return curl_result
        raise RuntimeError(f"Mueller Reader nicht erreichbar bei {url}: {exc}") from exc


def get_reader_markdown_with_curl(target_url: str) -> str:
    curl = shutil.which("curl")
    if not curl:
        return ""
    try:
        result = subprocess.run(
            [
                curl,
                "-L",
                "--silent",
                "--show-error",
                "--max-time",
                "35",
                "-H",
                "accept: text/plain,text/markdown,*/*",
                "-H",
                "accept-language: de-DE,de;q=0.9,en;q=0.7",
                target_url,
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=40,
        )
    except Exception:
        return ""
    if result.returncode != 0:
        return ""
    if "Title:" not in result.stdout and "Markdown Content:" not in result.stdout:
        return ""
    return result.stdout


def parse_search_fallback(markdown: str, code: str, product_id: str, original_url: str) -> Dict[str, Any]:
    lines = [line.strip() for line in markdown.splitlines() if line.strip()]
    code_pattern = re.escape(code)
    product_link_pattern = re.compile(
        rf"^\[(?!\!)(?P<title>[^\]]+)\]\((?P<url>https://www\.mueller\.de/p/[^)]*(?:IPN|PPN)?{code_pattern}/?)\)"
    )
    generic_product_link_pattern = re.compile(
        r"^\[(?!\!)(?P<title>[^\]]+)\]\((?P<url>https://www\.mueller\.de/p/[^)]+)\)"
    )
    wish_marker = re.compile(rf"Produkt\s+{code_pattern}\s+zur Wunschliste", re.I)

    for index, line in enumerate(lines):
        match = product_link_pattern.search(line)
        has_nearby_wish_marker = any(
            wish_marker.search(candidate) for candidate in lines[max(0, index - 8) : index]
        )
        if not match and not has_nearby_wish_marker:
            continue
        if not match:
            match = generic_product_link_pattern.search(line)
        if not match:
            continue

        price_line = ""
        old_price_line = ""
        unit_price = None
        availability_items: list[str] = []
        for candidate in lines[index + 1 : index + 8]:
            if not price_line and re.search(r"\d+(?:[.,]\d{2})?\s*€", candidate):
                price_line = candidate
                continue
            if price_line and not old_price_line and re.search(r"^UVP\s+\d+(?:[.,]\d{2})?\s*€", candidate, re.I):
                old_price_line = candidate
                continue
            if price_line and unit_price is None and re.search(r"\d+(?:[.,]\d{2})?\s*€\s*/\s*(?:1\s*)?(?:kg|g|l|ml|stk\.?|stück)", candidate, re.I):
                unit_price = candidate
                continue
            for label in ("Online verfügbar", "In die Filiale lieferbar"):
                if label in candidate and label not in availability_items:
                    availability_items.append(label)

        price_match = re.search(r"(\d+(?:[.,]\d{2})?)\s*€", price_line)
        if not price_match:
            continue
        current_price = german_price_value(price_match.group(1))
        if current_price is None:
            continue

        old_price = None
        old_match = re.search(r"UVP\s+(\d+(?:[.,]\d{2})?)\s*€", old_price_line, re.I)
        if old_match:
            old_price = german_price_value(old_match.group(1))

        image_url = None
        for previous in reversed(lines[max(0, index - 5) : index]):
            image_match = re.search(r"!\[[^\]]*\]\((https://images\.prod\.ecom\.mueller\.de[^)]+)\)", previous)
            if image_match:
                image_url = image_match.group(1)
                break

        title = html.unescape(match.group("title"))
        if "itemId=" in original_url:
            product_url = normalize_mueller_url(original_url)
        else:
            product_url = match.group("url") or normalize_mueller_url(original_url)
        price_details = normalize_price_details(unit_price=unit_price)
        price_cents = cents(current_price)
        old_price_cents = cents(old_price)
        return {
            "id": product_id,
            "name": title or code,
            "title": title or code,
            "article_number": code,
            "provider_article_number": code,
            "price": current_price,
            "price_cents": price_cents,
            "price_text": euro_text(current_price),
            "currency": "EUR",
            "old_price": old_price,
            "old_price_cents": old_price_cents,
            "old_price_text": euro_text(old_price),
            "unit_price": unit_price,
            **price_details,
            "available_service": "ONLINE",
            "stock_level": None,
            "availability": ", ".join(availability_items) or None,
            "market_id": "online",
            "url": product_url,
            "image_url": image_url,
        }

    raise RuntimeError(f"Kein Mueller-Preis im Reader-Fallback fuer {original_url} gefunden.")


def read_mueller_search_fallback(product: Dict[str, str], url: str, code: str) -> Dict[str, Any]:
    if not code:
        raise RuntimeError(f"Mueller Client-Challenge bei {url}. Keine Artikelnummer fuer Fallback gefunden.")
    search_url = f"{MUELLER_BASE_URL}/search/?q={urllib.parse.quote(code)}"
    markdown = get_reader_markdown(search_url)
    return parse_search_fallback(markdown, code, product["id"], url)


def read_mueller_product(product: Dict[str, str], _market: Dict[str, Any], _postal_code: str = "") -> Dict[str, Any]:
    url = normalize_mueller_url(product.get("product_url") or product.get("url") or "")
    if not url:
        raise RuntimeError("Mueller-Produkt braucht product_url.")

    preferred_code = product.get("article_number") or article_number_from_url(url)
    try:
        raw_html = get_html(url)
    except MuellerBlockedError:
        return read_mueller_search_fallback(product, url, preferred_code)
    chunk = product_chunk(raw_html, preferred_code)
    json_ld = extract_json_ld(raw_html)
    offers = json_ld.get("offers") or []
    first_offer = offers[0] if isinstance(offers, list) and offers else {}

    structured_price = float_value(first_offer.get("price"))
    current_price = number(
        r'"currentPrice":\{"currencyIso":"EUR","valueWithoutTax":[0-9.]+,"valueWithTax":([0-9.]+)',
        chunk,
    )
    old_price = number(
        r'"recommendedRetailPrice":\{"currencyIso":"EUR","valueWithoutTax":[0-9.]+,"valueWithTax":([0-9.]+)',
        chunk,
    )
    if structured_price is not None:
        current_price = structured_price
    visible_offer_price = extract_visible_offer_price(raw_html, old_price)
    if visible_offer_price is not None:
        current_price = visible_offer_price

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
