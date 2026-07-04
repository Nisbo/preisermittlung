from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, Optional

from core.price_details import normalize_price_details


ALDI_SUED_BASE_URL = "https://www.aldi-sued.de"
DESKTOP_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)
USER_AGENT_OVERRIDE = ""
APP_ROOT = Path(__file__).resolve().parent.parent
BROWSER_CACHE_DIR = APP_ROOT.joinpath(".browser-cache", "aldi-sued")
LOCAL_PLAYWRIGHT_BROWSERS = APP_ROOT.joinpath(".playwright-browsers")


def set_user_agent(user_agent: str) -> None:
    global USER_AGENT_OVERRIDE
    USER_AGENT_OVERRIDE = user_agent.strip()


def normalize_aldi_sued_url(url: str) -> str:
    cleaned = url.strip()
    if cleaned.startswith("/produkt/"):
        return ALDI_SUED_BASE_URL + cleaned
    return cleaned


def article_number_from_url(url: str) -> str:
    cleaned = normalize_aldi_sued_url(url)
    match = re.search(r"-(\d{6,})(?:[/?#].*)?$", cleaned.rstrip("/"))
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
        "provider": "aldi_sued",
        "label": "ALDI Süd",
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


def value(data: list, ref: Any) -> Any:
    if isinstance(ref, int) and 0 <= ref < len(data):
        return data[ref]
    return ref


def product_json_ld(page: Any) -> Dict[str, Any]:
    return page.evaluate(
        """() => {
            for (const script of document.querySelectorAll('script[type="application/ld+json"]')) {
                try {
                    const data = JSON.parse(script.textContent);
                    if (data && data['@type'] === 'Product') return data;
                } catch (_) {}
            }
            return {};
        }"""
    )


def nuxt_product_data(page: Any) -> Dict[str, Any]:
    raw = page.locator("#__NUXT_DATA__").text_content(timeout=10000)
    if not raw:
        raise RuntimeError("ALDI Süd Produktdaten nicht gefunden.")

    data = json.loads(raw)
    state_index = next(
        (
            index
            for index, item in enumerate(data)
            if isinstance(item, dict)
            and any(str(key).startswith("$spdp-product-") for key in item)
        ),
        None,
    )
    if state_index is None:
        raise RuntimeError("ALDI Süd Produktblock nicht gefunden.")

    state = data[state_index]
    product_key = next(key for key in state if str(key).startswith("$spdp-product-"))
    product = data[state[product_key]]
    price = data[product["price"]]

    return {
        "product_key": product_key,
        "sku": value(data, product.get("sku")),
        "name": value(data, product.get("name")),
        "brand": value(data, product.get("brandName")),
        "selling_size": value(data, product.get("sellingSize")),
        "price_cents": value(data, price.get("amount")),
        "price_text": value(data, price.get("amountRelevantDisplay")),
        "old_price_text": value(data, price.get("wasPriceDisplay")),
        "discount": value(data, price.get("savingsDisplay")),
        "unit_price": value(data, price.get("comparisonDisplay")),
        "currency": value(data, price.get("currencyCode")) or "EUR",
    }


def read_aldi_sued_product(product: Dict[str, str], _market: Dict[str, Any], _postal_code: str = "") -> Dict[str, Any]:
    if LOCAL_PLAYWRIGHT_BROWSERS.exists():
        os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(LOCAL_PLAYWRIGHT_BROWSERS))
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError("Playwright fehlt. Bitte `pip install -r requirements.txt` ausführen.") from exc

    url = normalize_aldi_sued_url(product.get("product_url") or product.get("url") or "")
    if not url:
        raise RuntimeError("ALDI Süd Produkt braucht product_url.")

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
            title = page.title()
            text = page.locator("body").inner_text(timeout=5000)
            if "Access Denied" in title or "Access Denied" in text or "Just a moment" in text:
                raise RuntimeError("ALDI Süd blockiert die Browserabfrage.")

            json_ld = product_json_ld(page)
            nuxt = nuxt_product_data(page)
            peak_memory_bytes = max(peak_memory_bytes, chromium_memory_bytes())
        finally:
            peak_memory_bytes = max(peak_memory_bytes, chromium_memory_bytes())
            context.close()

    article_number = product.get("article_number") or str(nuxt.get("sku") or article_number_from_url(url))
    price_cents = int(nuxt["price_cents"])
    old_price_cents = cents_from_text(nuxt.get("old_price_text"))
    price_details = normalize_price_details(
        package_size=str(nuxt.get("selling_size") or ""),
        unit_price=str(nuxt.get("unit_price") or ""),
    )
    image = json_ld.get("image")
    image_url = image[0] if isinstance(image, list) and image else image if isinstance(image, str) else None
    name = str(nuxt.get("name") or json_ld.get("name") or product.get("name") or article_number)

    return {
        "id": product["id"],
        "name": product.get("name") or name,
        "title": name,
        "article_number": article_number,
        "provider_article_number": str(nuxt.get("sku") or article_number),
        "price": price_cents / 100,
        "price_cents": price_cents,
        "price_text": euro_text_from_cents(price_cents),
        "currency": str(nuxt.get("currency") or "EUR"),
        "old_price": old_price_cents / 100 if old_price_cents is not None else None,
        "old_price_cents": old_price_cents,
        "old_price_text": euro_text_from_cents(old_price_cents),
        "discount": nuxt.get("discount"),
        "unit_price": nuxt.get("unit_price"),
        **price_details,
        "available_service": "ONLINE",
        "market_id": "online",
        "seller": "ALDI Süd",
        "url": url,
        "image_url": image_url,
        "browser_memory": {
            "provider": "aldi_sued",
            "label": "ALDI Süd",
            "peak_bytes": peak_memory_bytes,
            "peak_text": format_bytes(peak_memory_bytes),
        },
    }
