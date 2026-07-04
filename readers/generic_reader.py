from __future__ import annotations

import hashlib
import html
import os
import re
import shutil
import subprocess
import urllib.error
import urllib.request
import base64
import struct
from pathlib import Path
from typing import Any, Dict, List, Optional


GENERIC_BASE_URL = "generic://"
DESKTOP_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)
USER_AGENT_OVERRIDE = ""
APP_ROOT = Path(__file__).resolve().parent.parent
BROWSER_CACHE_DIR = APP_ROOT.joinpath(".browser-cache", "generic")
LOCAL_PLAYWRIGHT_BROWSERS = APP_ROOT.joinpath(".playwright-browsers")
PRICE_PATTERN = re.compile(
    r"(?<![\w])(\d{1,3}(?:\.\d{3})*\s*,\s*\d{2}|\d+\s*,\s*\d{2})\s*€(?:\s*(?:VAT incl\.?|incl\. VAT|inkl\. MwSt\.?|inkl\. USt\.?))?",
    re.I,
)


def set_user_agent(user_agent: str) -> None:
    global USER_AGENT_OVERRIDE
    USER_AGENT_OVERRIDE = user_agent.strip()


def normalize_generic_url(url: str) -> str:
    return url.strip()


def article_number_from_url(url: str) -> str:
    return hashlib.sha1(normalize_generic_url(url).encode("utf-8")).hexdigest()[:12]


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
        "provider": "generic",
        "label": "Generic",
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


def cents_from_price_text(value: str) -> Optional[int]:
    match = re.search(r"(\d{1,3}(?:\.\d{3})*\s*,\s*\d{2}|\d+\s*,\s*\d{2})", value)
    if not match:
        return None
    number = match.group(1).replace(" ", "").replace(".", "").replace(",", ".")
    return int(round(float(number) * 100))


def euro_text_from_cents(cents: Optional[int]) -> Optional[str]:
    if cents is None:
        return None
    euros, remainder = divmod(int(cents), 100)
    return f"{euros},{remainder:02d} €"


def png_dimensions(data: bytes) -> tuple[int, int]:
    if len(data) >= 24 and data.startswith(b"\x89PNG\r\n\x1a\n"):
        width, height = struct.unpack(">II", data[16:24])
        return int(width), int(height)
    return 0, 0


def short_context(text: str, start: int, end: int, raw: str) -> str:
    before_words = text[:start].split()
    after_words = text[end:].split()
    before = " ".join(before_words[-7:])
    after = " ".join(after_words[:9])
    parts = []
    if before:
        parts.append(f"... {before}")
    parts.append(f"[{raw}]")
    if after:
        parts.append(f"{after} ...")
    return " ".join(parts)


def http_html(url: str) -> str:
    request = urllib.request.Request(
        normalize_generic_url(url),
        headers={
            "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "accept-language": "de-DE,de;q=0.9,en;q=0.7",
            "user-agent": USER_AGENT_OVERRIDE or DESKTOP_USER_AGENT,
        },
    )
    with urllib.request.urlopen(request, timeout=25) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        return response.read().decode(charset, errors="replace")


def text_from_html(raw_html: str) -> str:
    cleaned = re.sub(r"(?is)<(script|style|noscript|svg)[^>]*>.*?</\1>", " ", raw_html)
    cleaned = re.sub(r"(?is)<br\s*/?>", "\n", cleaned)
    cleaned = re.sub(r"(?is)</(div|p|li|span|h[1-6]|td|tr)>", "\n", cleaned)
    cleaned = re.sub(r"(?is)<[^>]+>", " ", cleaned)
    cleaned = html.unescape(cleaned)
    return re.sub(r"[ \t]+", " ", cleaned)


def title_from_html(raw_html: str, fallback: str) -> str:
    match = re.search(r"(?is)<title[^>]*>(.*?)</title>", raw_html)
    if not match:
        return fallback
    title = re.sub(r"\s+", " ", html.unescape(match.group(1))).strip()
    return title or fallback


def candidates_from_text(text: str, title: str, source: str, url: str, browser_memory: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    candidates: List[Dict[str, Any]] = []
    compact_text = re.sub(r"\s+", " ", text).strip()
    seen: set[tuple[int, str]] = set()
    for match in PRICE_PATTERN.finditer(compact_text):
        raw = re.sub(r"\s*,\s*", ",", match.group(0).strip())
        cents = cents_from_price_text(raw)
        if cents is None:
            continue
        context = short_context(compact_text, match.start(), match.end(), raw)
        context = re.sub(r"\s*,\s*", ",", context)
        dedupe_key = (cents, context)
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        candidates.append(
            {
                "index": len(candidates),
                "price_text": euro_text_from_cents(cents),
                "raw_text": raw,
                "price_cents": cents,
                "context": context[:260],
                "line": 0,
            }
        )
    return {
        "ok": bool(candidates),
        "url": url,
        "title": title,
        "source": source,
        "candidates": candidates[:80],
        "browser_memory": browser_memory,
    }


def merge_visual_candidates(analysis: Dict[str, Any], visual_items: List[Dict[str, Any]]) -> Dict[str, Any]:
    candidates = analysis.get("candidates") or []
    used: set[int] = set()
    mapped_items: List[Dict[str, Any]] = []
    for item in visual_items:
        price_text = euro_text_from_cents(cents_from_price_text(str(item.get("raw_text") or "")))
        if not price_text:
            continue
        match_index = None
        for candidate in candidates:
            candidate_index = int(candidate.get("index") or 0)
            if candidate_index in used:
                continue
            if candidate.get("price_text") == price_text:
                match_index = candidate_index
                break
        if match_index is None:
            continue
        used.add(match_index)
        mapped_items.append({**item, "candidate_index": match_index, "price_text": price_text})
    if mapped_items:
        analysis["visual_candidates"] = mapped_items[:60]
    return analysis


def playwright_text(url: str) -> Dict[str, Any]:
    if LOCAL_PLAYWRIGHT_BROWSERS.exists():
        os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(LOCAL_PLAYWRIGHT_BROWSERS))
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError("Playwright fehlt. Bitte `pip install -r requirements.txt` ausführen.") from exc

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
            page.wait_for_timeout(5000)
            title = page.title() or url
            text = page.locator("body").inner_text(timeout=7000)
            screenshot = page.screenshot(full_page=True, type="png")
            viewport_size = page.viewport_size or {"width": 1280, "height": 720}
            screenshot_width, screenshot_height = png_dimensions(screenshot)
            viewport_width = int(viewport_size.get("width") or 1280)
            coordinate_scale = (screenshot_width / viewport_width) if screenshot_width and viewport_width else 1
            visual_candidates = page.evaluate(
                """
                () => {
                  const re = /(\\d{1,3}(?:\\.\\d{3})*\\s*,\\s*\\d{2}|\\d+\\s*,\\s*\\d{2})\\s*€/gi;
                  const items = [];
                  const visible = (element) => {
                    let current = element;
                    while (current && current !== document.body) {
                      const style = window.getComputedStyle(current);
                      if (style.display === "none" || style.visibility === "hidden" || Number(style.opacity || "1") === 0) return false;
                      current = current.parentElement;
                    }
                    return true;
                  };
                  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
                  let node;
                  while ((node = walker.nextNode())) {
                    const text = node.textContent || "";
                    if (!text.trim()) continue;
                    let match;
                    re.lastIndex = 0;
                    while ((match = re.exec(text))) {
                      const range = document.createRange();
                      range.setStart(node, match.index);
                      range.setEnd(node, match.index + match[0].length);
                      const rect = range.getBoundingClientRect();
                      range.detach();
                      if (!rect || rect.width < 4 || rect.height < 4) continue;
                      if (!visible(node.parentElement || document.body)) continue;
                      items.push({
                        raw_text: match[0].replace(/\\s*,\\s*/, ",").trim(),
                        x: rect.left + window.scrollX,
                        y: rect.top + window.scrollY,
                        width: rect.width,
                        height: rect.height,
                      });
                    }
                  }
                  const deduped = [];
                  for (const item of items.sort((a, b) => a.y - b.y || a.x - b.x)) {
                    const same = deduped.some((other) =>
                      other.raw_text === item.raw_text &&
                      Math.abs(other.x - item.x) < 3 &&
                      Math.abs(other.y - item.y) < 3
                    );
                    if (!same) deduped.push(item);
                  }
                  return deduped;
                }
                """
            )
            for item in visual_candidates:
                item["x"] = float(item.get("x") or 0) * coordinate_scale
                item["y"] = float(item.get("y") or 0) * coordinate_scale
                item["width"] = float(item.get("width") or 0) * coordinate_scale
                item["height"] = float(item.get("height") or 0) * coordinate_scale
            visual_candidates = [
                item
                for item in visual_candidates
                if 0 <= float(item.get("x") or 0) <= float(screenshot_width or viewport_width)
                and 0 <= float(item.get("y") or 0) <= float(screenshot_height or viewport_size.get("height") or 720)
            ]
            peak_memory_bytes = max(peak_memory_bytes, chromium_memory_bytes())
        finally:
            peak_memory_bytes = max(peak_memory_bytes, chromium_memory_bytes())
            context.close()

    return {
        "title": title,
        "text": text,
        "screenshot": {
            "data_url": "data:image/png;base64," + base64.b64encode(screenshot).decode("ascii"),
            "width": int(screenshot_width or viewport_width),
            "height": int(screenshot_height or viewport_size.get("height") or 720),
        },
        "visual_candidates": visual_candidates,
        "browser_memory": {
            "provider": "generic",
            "label": "Generic",
            "peak_bytes": peak_memory_bytes,
            "peak_text": format_bytes(peak_memory_bytes),
        },
    }


def analyze_generic_url(url: str, prefer_browser: bool = False) -> Dict[str, Any]:
    cleaned = normalize_generic_url(url)
    if not cleaned:
        raise RuntimeError("Generic-URL fehlt.")

    if not prefer_browser:
        try:
            raw_html = http_html(cleaned)
            text = text_from_html(raw_html)
            title = title_from_html(raw_html, cleaned)
            return candidates_from_text(text, title, "http", cleaned)
        except (urllib.error.URLError, TimeoutError, RuntimeError):
            return {
                "ok": False,
                "url": cleaned,
                "title": cleaned,
                "source": "http",
                "candidates": [],
                "browser_memory": None,
            }

    browser = playwright_text(cleaned)
    analysis = candidates_from_text(
        browser["text"],
        browser["title"],
        "browser",
        cleaned,
        browser.get("browser_memory"),
    )
    analysis["screenshot"] = browser.get("screenshot")
    return merge_visual_candidates(analysis, browser.get("visual_candidates") or [])


def read_generic_product(product: Dict[str, Any], _market: Dict[str, Any], _postal_code: str = "") -> Dict[str, Any]:
    url = normalize_generic_url(product.get("product_url") or product.get("url") or "")
    if not url:
        raise RuntimeError("Generic-Produkt braucht product_url.")

    generic = product.get("generic") or {}
    generic_source = generic.get("source") or product.get("generic_source")
    prefer_browser = str(generic_source or "").lower() == "browser"
    analysis = analyze_generic_url(url, prefer_browser=prefer_browser)
    candidates = analysis.get("candidates") or []
    if not candidates:
        raise RuntimeError(f"Kein Preis fuer {url} gefunden.")

    selected_index = int(generic.get("candidate_index") or product.get("generic_candidate_index") or 0)
    if selected_index < 0 or selected_index >= len(candidates):
        selected_index = 0
    candidate = candidates[selected_index]
    price_cents = int(candidate["price_cents"])
    article_number = product.get("article_number") or article_number_from_url(url)
    title = product.get("name") or analysis.get("title") or article_number

    result = {
        "id": product["id"],
        "name": product.get("name") or title,
        "title": title,
        "article_number": article_number,
        "provider_article_number": article_number,
        "price": price_cents / 100,
        "price_cents": price_cents,
        "price_text": euro_text_from_cents(price_cents),
        "currency": "EUR",
        "unit_price": None,
        "available_service": "ONLINE",
        "market_id": "online",
        "seller": "Generic",
        "url": url,
        "generic_candidate": candidate,
    }
    if isinstance(analysis.get("browser_memory"), dict):
        result["browser_memory"] = analysis["browser_memory"]
    return result
