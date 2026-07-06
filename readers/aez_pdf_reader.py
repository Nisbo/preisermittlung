from __future__ import annotations

import hashlib
import html
import logging
import re
import shutil
import subprocess
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.price_details import normalize_price_details


AEZ_OFFERS_URL = "https://aez.de/wochenangebote/"
FALLBACK_PDF_URL = "https://aez.de/wp-content/uploads/AEZ_KW27.pdf"
DESKTOP_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)
USER_AGENT_OVERRIDE = ""
APP_ROOT = Path(__file__).resolve().parent.parent
PDF_CACHE_DIR = APP_ROOT.joinpath(".pdf-cache", "aez")
GENERATED_DIR = APP_ROOT.joinpath("generated", "aez_pdf")
PDF_CONTEXT_TTL_SECONDS = 900
PDF_CONTEXT_CACHE: Dict[str, Any] = {}
logging.getLogger("pdfminer").setLevel(logging.ERROR)


def set_user_agent(user_agent: str) -> None:
    global USER_AGENT_OVERRIDE
    USER_AGENT_OVERRIDE = user_agent.strip()


def normalize_text(value: Any) -> str:
    text = str(value or "").lower()
    replacements = {"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"}
    for src, dst in replacements.items():
        text = text.replace(src, dst)
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def word_variants(value: Any) -> set[str]:
    text = str(value or "")
    variants = {normalize_text(text)}
    for match in re.finditer(r"[A-Z][a-z]{2,}(?:[A-Z][a-z]+)+", text):
        variants.add(normalize_text(match.group(0)))
    for match in re.finditer(r"[A-Z]?[a-z]{2,}", text):
        variants.add(normalize_text(match.group(0)))
    return {variant for variant in variants if variant}


def suspicious_pdf_word(value: Any) -> bool:
    letters = re.sub(r"[^A-Za-zÄÖÜäöüß]", "", str(value or ""))
    if len(letters) < 5:
        return False
    upper_count = len(re.findall(r"[A-ZÄÖÜ]", letters))
    lower_count = len(re.findall(r"[a-zäöüß]", letters))
    return upper_count >= 2 and lower_count >= 2


def fuzzy_token_match(term: str, variants: set[str], raw_text: Any = "") -> bool:
    if term in variants:
        return True
    if not suspicious_pdf_word(raw_text):
        return False
    if len(term) < 5:
        return False
    chunks = {term[index : index + 3] for index in range(0, len(term) - 2)}
    term_counts = {char: term.count(char) for char in set(term)}
    for variant in variants:
        if abs(len(variant) - len(term)) > 2:
            continue
        if not any(chunk in variant for chunk in chunks):
            continue
        common = sum(min(count, variant.count(char)) for char, count in term_counts.items())
        if common / len(term) >= 0.8:
            return True
    return False


def search_term_from_url(value: str) -> str:
    value = (value or "").strip()
    if value.startswith("aezpdf://"):
        return urllib.parse.unquote(value.removeprefix("aezpdf://")).strip()
    if value.startswith("manualpdf://"):
        return urllib.parse.unquote(value.removeprefix("manualpdf://")).strip()
    return value


def normalize_aez_pdf_url(value: str) -> str:
    term = search_term_from_url(value)
    return "aezpdf://" + urllib.parse.quote(term)


def article_number_from_url(value: str) -> str:
    term = search_term_from_url(value)
    if not term:
        return ""
    return hashlib.sha1(normalize_text(term).encode("utf-8")).hexdigest()[:12]


def euro_text_from_cents(cents: Optional[int]) -> Optional[str]:
    if cents is None:
        return None
    euros, remainder = divmod(int(cents), 100)
    return f"{euros},{remainder:02d} €"


def cents_from_price_text(value: str) -> Optional[int]:
    match = re.search(r"(\d{1,3}(?:[.,]\d{3})*[,.]\d{2}|\d+[,.]\d{2})", value)
    if not match:
        return None
    raw = match.group(1).replace(" ", "")
    if "," in raw:
        number = raw.replace(".", "").replace(",", ".")
    elif re.search(r"\.\d{2}$", raw):
        number = raw
    else:
        number = raw.replace(".", "")
    return int(round(float(number) * 100))


def fetch_text(url: str) -> str:
    request = urllib.request.Request(
        url,
        headers={
            "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "accept-language": "de-DE,de;q=0.9,en;q=0.7",
            "cache-control": "no-cache",
            "pragma": "no-cache",
            "user-agent": USER_AGENT_OVERRIDE or DESKTOP_USER_AGENT,
        },
    )
    with urllib.request.urlopen(request, timeout=25) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        return response.read().decode(charset, errors="replace")


def current_pdf_url() -> str:
    try:
        page = fetch_text(AEZ_OFFERS_URL)
    except Exception:
        return FALLBACK_PDF_URL
    links = re.findall(r"""href=["']([^"']+\.pdf(?:\?[^"']*)?)["']""", page, flags=re.I)
    if not links:
        return FALLBACK_PDF_URL
    absolute = [urllib.parse.urljoin(AEZ_OFFERS_URL, html.unescape(link)) for link in links]
    weekly_links = [link for link in absolute if re.search(r"/AEZ_KW\d+\.pdf(?:\?|$)", link, flags=re.I)]
    if weekly_links:
        return weekly_links[0]
    aez_links = [link for link in absolute if "aez" in link.lower()]
    return aez_links[0] if aez_links else absolute[0]


def canonical_pdf_url(url: str) -> str:
    parsed = urllib.parse.urlparse(str(url or "").strip())
    return urllib.parse.urlunparse(parsed._replace(fragment=""))


def pdf_url_from_product(product: Dict[str, Any]) -> Optional[str]:
    for key in ("url", "pdf_url"):
        value = str(product.get(key) or "").strip()
        if ".pdf" in urllib.parse.urlparse(value).path.lower():
            return canonical_pdf_url(value)
    return None


def cached_pdf_path(url: str) -> Path:
    PDF_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha1(canonical_pdf_url(url).encode("utf-8")).hexdigest()[:12]
    return PDF_CACHE_DIR.joinpath(f"{digest}.pdf")


def download_pdf(url: str) -> Path:
    url = canonical_pdf_url(url)
    path = cached_pdf_path(url)
    if path.exists() and path.stat().st_size > 0:
        return path
    request = urllib.request.Request(
        url,
        headers={
            "accept": "application/pdf,*/*;q=0.8",
            "cache-control": "no-cache",
            "pragma": "no-cache",
            "user-agent": USER_AGENT_OVERRIDE or DESKTOP_USER_AGENT,
        },
    )
    with urllib.request.urlopen(request, timeout=35) as response:
        path.write_bytes(response.read())
    return path


def price_words(words: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    items = []
    for index, word in enumerate(words):
        text = str(word.get("text") or "")
        if re.fullmatch(r"\d+[,.]\d{2}", text) and float(word.get("height") or 0) >= 18:
            cents = cents_from_price_text(text)
            if cents is not None:
                items.append({**word, "price_cents": cents, "price_text": euro_text_from_cents(cents)})
        if not re.fullmatch(r"\d{1,3}", text):
            continue
        if float(word.get("height") or 0) < 18:
            continue
        if index + 1 >= len(words):
            continue
        cents_word = words[index + 1]
        cents_text = str(cents_word.get("text") or "")
        if not re.fullmatch(r"\d{2}", cents_text):
            continue
        if abs(float(cents_word.get("top") or 0) - float(word.get("top") or 0)) > 28:
            continue
        if float(cents_word.get("x0") or 0) - float(word.get("x1") or 0) > 34:
            continue
        if float(cents_word.get("x0") or 0) < float(word.get("x0") or 0):
            continue
        cents = int(text) * 100 + int(cents_text)
        items.append(
            {
                **word,
                "text": f"{text}.{cents_text}",
                "x1": cents_word.get("x1", word.get("x1")),
                "bottom": max(float(word.get("bottom") or 0), float(cents_word.get("bottom") or 0)),
                "height": max(float(word.get("height") or 0), float(cents_word.get("height") or 0)),
                "width": float(cents_word.get("x1") or word.get("x1") or 0) - float(word.get("x0") or 0),
                "price_cents": cents,
                "price_text": euro_text_from_cents(cents),
            }
        )
    return items


def is_price_like_word(value: Any) -> bool:
    return bool(re.fullmatch(r"\d+[,.]\d{2}|\d+[,.]\d{2}\*+|-?\d+%|AKTION!?", str(value or ""), flags=re.I))


def match_box(words: List[Dict[str, Any]], indexes: List[int], score: float = 0) -> Dict[str, Any]:
    part = [words[index] for index in indexes]
    return {
        "start": indexes[0],
        "end": indexes[-1] + 1,
        "x0": min(float(word["x0"]) for word in part),
        "x1": max(float(word["x1"]) for word in part),
        "top": min(float(word["top"]) for word in part),
        "bottom": max(float(word["bottom"]) for word in part),
        "score": score,
    }


def find_ordered_phrase(words: List[Dict[str, Any]], word_tokens: List[set[str]], term_tokens: List[str]) -> List[Dict[str, Any]]:
    matches = []
    max_gap = 8
    max_span = len(term_tokens) + 14
    for start in range(0, len(words)):
        if term_tokens[0] not in word_tokens[start]:
            continue
        indexes = [start]
        position = start + 1
        skipped_score = 0.0
        failed = False
        for term_token in term_tokens[1:]:
            found_at: Optional[int] = None
            search_end = min(len(words), position + max_gap + 1)
            for index in range(position, search_end):
                if term_token in word_tokens[index]:
                    found_at = index
                    break
            if found_at is None:
                failed = True
                break
            for skipped in words[position:found_at]:
                skipped_score += 0.2 if is_price_like_word(skipped.get("text")) else 1.0
            indexes.append(found_at)
            position = found_at + 1
        if failed or indexes[-1] - indexes[0] + 1 > max_span:
            continue
        matches.append(match_box(words, indexes, skipped_score))
    matches.sort(key=lambda item: (float(item.get("score") or 0), item["start"]))
    return matches


def find_phrase(words: List[Dict[str, Any]], term: str, allow_fuzzy: bool = False) -> List[Dict[str, Any]]:
    term_tokens = [token for token in normalize_text(term).split() if token]
    if not term_tokens:
        return []
    word_tokens = [word_variants(word.get("text")) for word in words]
    matches = []
    if allow_fuzzy and len(term_tokens) > 1:
        return []
    for index in range(0, len(words) - len(term_tokens) + 1):
        if all(
            fuzzy_token_match(term_token, word_tokens[index + offset], words[index + offset].get("text"))
            if allow_fuzzy
            else term_token in word_tokens[index + offset]
            for offset, term_token in enumerate(term_tokens)
        ):
            matches.append(match_box(words, list(range(index, index + len(term_tokens)))))
    if not matches and not allow_fuzzy and len(term_tokens) > 1:
        matches = find_ordered_phrase(words, word_tokens, term_tokens)
    return matches


def choose_price(match: Dict[str, Any], prices: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not prices:
        return None
    hit_x = (match["x0"] + match["x1"]) / 2
    hit_y = (match["top"] + match["bottom"]) / 2
    below_prices = [
        price
        for price in prices
        if ((float(price["top"]) + float(price["bottom"])) / 2) >= hit_y
    ]
    near_below_prices = [
        price
        for price in below_prices
        if ((float(price["top"]) + float(price["bottom"])) / 2) - hit_y <= 220
    ]
    candidate_prices = near_below_prices or below_prices or prices
    ranked = []
    for price in candidate_prices:
        ranked.append((price_match_score(match, price), price))
    ranked.sort(key=lambda item: item[0])
    return {**ranked[0][1], "_match_score": ranked[0][0]}


def price_match_score(match: Dict[str, Any], price: Dict[str, Any]) -> float:
    hit_x = (match["x0"] + match["x1"]) / 2
    hit_y = (match["top"] + match["bottom"]) / 2
    price_x = (float(price["x0"]) + float(price["x1"])) / 2
    price_y = (float(price["top"]) + float(price["bottom"])) / 2
    horizontal = abs(price_x - hit_x)
    vertical = abs(price_y - hit_y)
    below_bonus = -35 if price_y >= hit_y else 35
    far_penalty = 120 if horizontal > 220 else 0
    return horizontal * 0.9 + vertical * 0.55 + below_bonus + far_penalty


def words_in_region(words: List[Dict[str, Any]], box: Dict[str, float]) -> List[Dict[str, Any]]:
    return [
        word
        for word in words
        if float(word["x0"]) >= box["x0"]
        and float(word["x1"]) <= box["x1"]
        and float(word["top"]) >= box["top"]
        and float(word["bottom"]) <= box["bottom"]
    ]


def title_from_region(region_words: List[Dict[str, Any]], price_top: float) -> str:
    title_words = []
    for word in sorted(region_words, key=lambda item: (float(item["top"]), float(item["x0"]))):
        text = str(word.get("text") or "")
        if float(word.get("top") or 0) >= price_top - 2:
            continue
        if re.fullmatch(r"[A-Z]", text):
            continue
        if text.upper() in {"OHNE", "EDEKA", "APP", "PREIS"}:
            continue
        if text.upper() in {"AKTION!", "AKTION"} or text.startswith("-"):
            continue
        if re.fullmatch(r"\d+[,.]\d{2}", text):
            continue
        title_words.append(text)
    joined = " ".join(title_words)
    joined = re.sub(r"\s+", " ", joined).strip()
    package_match = re.search(r"\b\d+\s*(?:g|kg|ml|l)\b(?:\s+\w+)?", joined, flags=re.I)
    if package_match:
        return joined[: package_match.start()].strip() or joined
    sort_match = re.search(r"\bversch\.", joined, flags=re.I)
    if sort_match:
        return joined[: sort_match.start()].strip() or joined
    return joined[:120] or "PDF Prospekt-Treffer"


def details_from_region(region_words: List[Dict[str, Any]]) -> Dict[str, Optional[str]]:
    text = " ".join(str(word.get("text") or "") for word in sorted(region_words, key=lambda item: (float(item["top"]), float(item["x0"]))))
    text = re.sub(r"\s+", " ", text)
    package = None
    package_match = re.search(r"\b(\d+\s*(?:g|kg|ml|l)\s+(?:Dose|Packung|Flasche|Schale|Beutel|Glas))\b", text, flags=re.I)
    if package_match:
        package = package_match.group(1)
    unit = None
    unit_match = re.search(r"\(\s*1\s*(kg|l|Stück|Stk)\s*=\s*(\d+[,.]\d{2})\s*\)", text, flags=re.I)
    if unit_match:
        unit = f"{unit_match.group(2).replace('.', ',')} € / 1 {unit_match.group(1)}"
    return {"package_size_text": package, "unit_price_text": unit}


def pdftoppm_command() -> str:
    bundled = Path.home().joinpath(".cache/codex-runtimes/codex-primary-runtime/dependencies/bin/pdftoppm")
    if bundled.exists():
        return str(bundled)
    return shutil.which("pdftoppm") or "pdftoppm"


def render_crop(
    pdf_path: Path,
    page_number: int,
    box: Dict[str, float],
    key: str,
    page_width: float,
    page_height: float,
    output_dir: Path = GENERATED_DIR,
    public_prefix: str = "/generated/aez_pdf/",
) -> Optional[str]:
    try:
        from PIL import Image
    except Exception:
        return None
    output_dir.mkdir(parents=True, exist_ok=True)
    out = output_dir.joinpath(f"{key}.png")
    if out.exists() and out.stat().st_size > 0:
        return public_prefix + out.name
    prefix = output_dir.joinpath(f"page_{key}")
    subprocess.run(
        [pdftoppm_command(), "-f", str(page_number), "-l", str(page_number), "-png", "-r", "160", str(pdf_path), str(prefix)],
        check=True,
        capture_output=True,
        timeout=30,
    )
    page_image = output_dir.joinpath(f"page_{key}-{page_number}.png")
    if not page_image.exists():
        padded_page_image = output_dir.joinpath(f"page_{key}-{page_number:02d}.png")
        if padded_page_image.exists():
            page_image = padded_page_image
    if not page_image.exists():
        return None
    image = Image.open(page_image)
    scale_x = image.width / page_width
    scale_y = image.height / page_height
    crop_box = (
        max(0, int(box["x0"] * scale_x)),
        max(0, int(box["top"] * scale_y)),
        min(image.width, int(box["x1"] * scale_x)),
        min(image.height, int(box["bottom"] * scale_y)),
    )
    crop = image.crop(crop_box)
    crop.save(out)
    return public_prefix + out.name


def offer_key(page_number: int, title: str, price_cents: int) -> str:
    source = f"{page_number}:{normalize_text(title)}:{price_cents}"
    return "aez_" + hashlib.sha1(source.encode("utf-8")).hexdigest()[:12]


def slim_match(result: Dict[str, Any]) -> Dict[str, Any]:
    keys = [
        "title",
        "price",
        "price_cents",
        "price_text",
        "currency",
        "unit_price",
        "unit_price_text",
        "package_size_text",
        "url",
        "image_url",
        "pdf_page",
        "pdf_match_quality",
        "pdf_extracted_title",
        "pdf_file_name",
        "provider_article_number",
    ]
    return {key: result.get(key) for key in keys if result.get(key) is not None}


def parse_pdf_context(pdf_url: str, pdf_path: Path, loaded_at: Optional[float] = None) -> Dict[str, Any]:
    try:
        import pdfplumber
    except Exception as exc:
        raise RuntimeError("pdfplumber fehlt. Installiere es mit: pip install pdfplumber pillow") from exc

    with pdfplumber.open(str(pdf_path)) as pdf:
        pages = []
        for page_index, page in enumerate(pdf.pages, start=1):
            words = page.extract_words(x_tolerance=2, y_tolerance=3, keep_blank_chars=False)
            pages.append(
                {
                    "page_index": page_index,
                    "width": float(page.width),
                    "height": float(page.height),
                    "words": words,
                    "prices": price_words(words),
                }
            )
    context = {
        "loaded_at": loaded_at or time.time(),
        "pdf_url": pdf_url,
        "pdf_path": pdf_path,
        "pdf_file_name": Path(urllib.parse.urlparse(pdf_url).path).name,
        "pages": pages,
    }
    return context


def current_pdf_context() -> Dict[str, Any]:
    pdf_url = canonical_pdf_url(current_pdf_url())
    current_key = f"context:{pdf_url}"
    for key in list(PDF_CONTEXT_CACHE):
        if key != current_key:
            PDF_CONTEXT_CACHE.pop(key, None)
    current_pdf_path = cached_pdf_path(pdf_url)
    if PDF_CACHE_DIR.exists():
        for pdf_path in PDF_CACHE_DIR.glob("*.pdf"):
            if pdf_path != current_pdf_path:
                try:
                    pdf_path.unlink()
                except OSError:
                    pass
    return pdf_context_for_url(pdf_url)


def pdf_context_for_url(pdf_url: str) -> Dict[str, Any]:
    now = time.time()
    pdf_url = canonical_pdf_url(pdf_url)
    cache_key = f"context:{pdf_url}"
    cached = PDF_CONTEXT_CACHE.get(cache_key)
    if cached and now - float(cached.get("loaded_at") or 0) < PDF_CONTEXT_TTL_SECONDS:
        return cached

    pdf_path = download_pdf(pdf_url)
    context = parse_pdf_context(pdf_url, pdf_path, now)
    PDF_CONTEXT_CACHE[cache_key] = context
    return context


def read_pdf_product_from_context(
    product: Dict[str, str],
    context: Dict[str, Any],
    output_dir: Path = GENERATED_DIR,
    public_prefix: str = "/generated/aez_pdf/",
    market_id: str = "weekly",
    available_service: str = "PDF",
) -> Dict[str, Any]:
    term = (
        str(product.get("search_term") or "").strip()
        or search_term_from_url(str(product.get("product_url") or ""))
        or str(product.get("name") or "").strip()
        or str(product.get("article_number") or "").strip()
    )
    if not term:
        raise RuntimeError("Kein Suchwort für PDF-Prospekt angegeben.")

    pdf_url = str(context["pdf_url"])
    best: Optional[Dict[str, Any]] = None

    for allow_fuzzy in (False, True):
        candidates = []
        for page_data in context["pages"]:
            page_index = int(page_data["page_index"])
            words = page_data["words"]
            matches = find_phrase(words, term, allow_fuzzy=allow_fuzzy)
            if not matches:
                continue
            prices = page_data["prices"]
            for match in matches:
                price = choose_price(match, prices)
                if not price:
                    continue
                region_box = {
                    "x0": max(0, min(float(match["x0"]), float(price["x0"])) - 70),
                    "x1": min(float(page_data["width"]), max(float(match["x1"]), float(price["x1"])) + 95),
                    "top": max(0, min(float(match["top"]), float(price["top"])) - 35),
                    "bottom": min(float(page_data["height"]), max(float(match["bottom"]), float(price["bottom"])) + 95),
                }
                region_words = words_in_region(words, region_box)
                title = title_from_region(region_words, float(price["top"]))
                details = details_from_region(region_words)
                result_title = title
                if title in {"AEZ Prospekt-Treffer", "PDF Prospekt-Treffer"}:
                    result_title = str(product.get("name") or term).strip() or title
                if allow_fuzzy or float(match.get("score") or 0) > 0:
                    result_title = str(product.get("name") or term).strip() or title
                if allow_fuzzy:
                    details["package_size_text"] = None
                key = offer_key(page_index, result_title, int(price["price_cents"]))
                image_url = render_crop(
                    Path(page_data.get("pdf_path") or context["pdf_path"]),
                    page_index,
                    region_box,
                    key,
                    float(page_data["width"]),
                    float(page_data["height"]),
                    output_dir,
                    public_prefix,
                )
                best = {
                    "id": product.get("id"),
                    "name": product.get("name") or title,
                    "title": result_title,
                    "article_number": product.get("article_number"),
                    "provider_article_number": key,
                    "price": int(price["price_cents"]) / 100,
                    "price_cents": int(price["price_cents"]),
                    "price_text": price["price_text"],
                    "currency": "EUR",
                    "unit_price": details.get("unit_price_text"),
                    "unit_price_text": details.get("unit_price_text"),
                    "package_size_text": details.get("package_size_text"),
                    "available_service": available_service,
                    "market_id": market_id,
                    "url": str(page_data.get("pdf_url") or pdf_url),
                    "image_url": image_url,
                    "pdf_page": page_index,
                    "pdf_search_term": term,
                    "pdf_match_quality": "fuzzy" if allow_fuzzy else "exact",
                    "pdf_extracted_title": title,
                    "pdf_file_name": page_data.get("pdf_file_name") or context.get("pdf_file_name"),
                    "pdf_loaded_at": context.get("loaded_at"),
                }
                candidates.append((float(match.get("score") or 0), float(price.get("_match_score") or 0), page_index, best))
        if candidates:
            candidates.sort(key=lambda item: (item[0], item[1], item[2]))
            unique_matches = []
            seen_matches = set()
            for _score, _price_score, _page_index, candidate in candidates:
                identity = (
                    candidate.get("pdf_page"),
                    candidate.get("price_cents"),
                    normalize_text(candidate.get("title")),
                    candidate.get("provider_article_number"),
                )
                if identity in seen_matches:
                    continue
                seen_matches.add(identity)
                unique_matches.append(slim_match(candidate))
            best = candidates[0][3]
            best["matches"] = unique_matches
            best["match_count"] = len(unique_matches)
            break

    if not best:
        raise RuntimeError(f"Kein Treffer im PDF-Prospekt für '{term}'.")

    normalized = normalize_price_details(
        best.get("package_size_text"),
        best.get("unit_price_text") or best.get("unit_price"),
    )
    best.update({key: value for key, value in normalized.items() if value})
    return best


def read_aez_pdf_product(product: Dict[str, str], market: Dict[str, Any], postal_code: str) -> Dict[str, Any]:
    context = current_pdf_context()
    return read_pdf_product_from_context(product, context)
