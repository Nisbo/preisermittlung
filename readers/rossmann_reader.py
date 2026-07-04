from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, Optional

from core.price_details import normalize_price_details


ROSSMANN_BASE_URL = "https://www.rossmann.de"
DESKTOP_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)
USER_AGENT_OVERRIDE = ""
APP_ROOT = Path(__file__).resolve().parent.parent
BROWSER_CACHE_DIR = APP_ROOT.joinpath(".browser-cache", "rossmann")
LOCAL_PLAYWRIGHT_BROWSERS = APP_ROOT.joinpath(".playwright-browsers")


def set_user_agent(user_agent: str) -> None:
    global USER_AGENT_OVERRIDE
    USER_AGENT_OVERRIDE = user_agent.strip()


def normalize_rossmann_url(url: str) -> str:
    cleaned = url.strip()
    if cleaned.startswith("/de/"):
        return ROSSMANN_BASE_URL + cleaned
    return cleaned


def article_number_from_url(url: str) -> str:
    cleaned = normalize_rossmann_url(url).rstrip("/")
    match = re.search(r"/p/([^/?#]+)", cleaned)
    return match.group(1) if match else ""


def cents_from_text(value: Optional[str]) -> Optional[int]:
    if not value:
        return None
    match = re.search(r"(\d+(?:[.,]\d{2})?)", value.replace("\xa0", " "))
    if not match:
        return None
    return int(round(float(match.group(1).replace(",", ".")) * 100))


def euro_text_from_cents(cents: Optional[int]) -> Optional[str]:
    if cents is None:
        return None
    euros, remainder = divmod(int(cents), 100)
    return f"{euros},{remainder:02d} €"


def cache_size_bytes() -> int:
    if not BROWSER_CACHE_DIR.exists():
        return 0
    return sum(path.stat().st_size for path in BROWSER_CACHE_DIR.rglob("*") if path.is_file())


def format_bytes(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{value:.1f} GB"


def cache_info() -> Dict[str, Any]:
    size = cache_size_bytes()
    return {
        "provider": "rossmann",
        "label": "Rossmann",
        "path": str(BROWSER_CACHE_DIR),
        "size_bytes": size,
        "size_text": format_bytes(size),
    }


def chromium_memory_bytes() -> int:
    try:
        result = subprocess.run(
            ["ps", "-axo", "rss=,command="],
            check=False,
            capture_output=True,
            text=True,
            timeout=3,
        )
    except Exception:
        return 0
    total_kb = 0
    cache_marker = str(BROWSER_CACHE_DIR)
    browser_marker = str(LOCAL_PLAYWRIGHT_BROWSERS)
    for line in result.stdout.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) != 2 or not parts[0].isdigit():
            continue
        command = parts[1]
        if ("Chromium" in command or "chrome" in command) and (
            cache_marker in command or browser_marker in command or "playwright" in command
        ):
            total_kb += int(parts[0])
    return total_kb * 1024


def clear_cache() -> None:
    if BROWSER_CACHE_DIR.exists():
        shutil.rmtree(BROWSER_CACHE_DIR)


def first_text(page: Any, selector: str) -> str:
    try:
        return re.sub(r"\s+", " ", page.locator(selector).first.inner_text(timeout=1500)).strip()
    except Exception:
        return ""


def first_attr(page: Any, selector: str, attribute: str) -> str:
    try:
        return page.locator(selector).first.get_attribute(attribute, timeout=1500) or ""
    except Exception:
        return ""


def extract_unit_price(lines: list[str]) -> Optional[str]:
    for line in lines:
        if re.search(r"\((?:1|10|100)\s*(?:kg|g|l|ml|stück|stk\.?)\s*=\s*[\d,.]+\s*€\)", line, re.I):
            return line
    for line in lines:
        if re.search(r"\(1\s*(?:kg|l)\s*=\s*[\d,.]+\s*€\)", line, re.I):
            return line
    return None


def extract_product_image(page: Any, title: str) -> Optional[str]:
    images = page.locator("img.rm-product__image").evaluate_all(
        """(els) => els.map(img => ({
            src: img.currentSrc || img.src,
            alt: img.alt || '',
            width: img.naturalWidth || 0,
            height: img.naturalHeight || 0
        })).filter(item => item.src)"""
    )
    title_key = title.strip().lower()
    for image in images:
        if title_key and str(image.get("alt") or "").strip().lower() == title_key:
            return str(image["src"])
    for image in images:
        src = str(image.get("src") or "")
        if "SHOP_IMAGE" in src and int(image.get("width") or 0) >= 300:
            return src
    return str(images[0]["src"]) if images else None


def read_rossmann_product(product: Dict[str, str], _market: Dict[str, Any], _postal_code: str = "") -> Dict[str, Any]:
    if LOCAL_PLAYWRIGHT_BROWSERS.exists():
        os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(LOCAL_PLAYWRIGHT_BROWSERS))
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError("Playwright fehlt. Bitte `pip install -r requirements.txt` ausführen.") from exc

    url = normalize_rossmann_url(product.get("product_url") or product.get("url") or "")
    if not url:
        raise RuntimeError("Rossmann-Produkt braucht product_url.")

    BROWSER_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    peak_memory_bytes = 0
    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            user_data_dir=str(BROWSER_CACHE_DIR),
            headless=True,
            locale="de-DE",
            timezone_id="Europe/Berlin",
            viewport={"width": 1280, "height": 720},
            user_agent=USER_AGENT_OVERRIDE or DESKTOP_USER_AGENT,
            args=["--disable-dev-shm-usage"],
        )
        page = context.pages[0] if context.pages else context.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(7000)
            body_text = page.locator("body").inner_text(timeout=5000)
            if "Client Challenge" in page.title() or "Please enable JavaScript" in body_text:
                raise RuntimeError("Rossmann Browser-Challenge wurde nicht abgeschlossen.")

            title = first_text(page, "h1") or page.title()
            price_value = first_attr(page, "[itemprop=price]", "content")
            currency = first_attr(page, "[itemprop=priceCurrency]", "content") or "EUR"
            availability = first_attr(page, "[itemprop=availability]", "content")
            price_text = first_text(page, ".rm-price")
            old_price_text = first_text(
                page,
                ".rm-price__old, .rm-price--old, [class*=old-price], [class*=strike], [class*=uvp]",
            )
            lines = [line.strip() for line in body_text.splitlines() if line.strip()]
            unit_price = extract_unit_price(lines)
            image_url = extract_product_image(page, title)
            peak_memory_bytes = max(peak_memory_bytes, chromium_memory_bytes())
        finally:
            peak_memory_bytes = max(peak_memory_bytes, chromium_memory_bytes())
            context.close()

    price_cents = cents_from_text(price_value or price_text)
    if price_cents is None:
        raise RuntimeError(f"Kein Rossmann-Preis fuer {url} gefunden.")

    old_price_cents = cents_from_text(old_price_text)
    article_number = product.get("article_number") or article_number_from_url(url)
    price_details = normalize_price_details(unit_price=unit_price)

    return {
        "id": product["id"],
        "name": product.get("name") or title or article_number,
        "title": title or product.get("name") or article_number,
        "article_number": article_number,
        "provider_article_number": article_number,
        "price": price_cents / 100,
        "price_cents": price_cents,
        "price_text": euro_text_from_cents(price_cents),
        "currency": currency,
        "old_price": old_price_cents / 100 if old_price_cents is not None else None,
        "old_price_cents": old_price_cents,
        "old_price_text": euro_text_from_cents(old_price_cents),
        "unit_price": unit_price,
        **price_details,
        "available_service": "ONLINE",
        "availability": availability,
        "market_id": "online",
        "seller": "Rossmann",
        "url": url,
        "image_url": image_url,
        "browser_memory": {
            "provider": "rossmann",
            "label": "Rossmann",
            "peak_bytes": peak_memory_bytes,
            "peak_text": format_bytes(peak_memory_bytes),
        },
    }
