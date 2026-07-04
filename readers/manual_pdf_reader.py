from __future__ import annotations

import hashlib
import re
import time
import urllib.parse
from pathlib import Path
from typing import Any, Dict, List

from readers import aez_pdf_reader


APP_ROOT = Path(__file__).resolve().parent.parent
UPLOAD_DIR = APP_ROOT.joinpath("manual_pdfs")
GENERATED_DIR = APP_ROOT.joinpath("generated", "manual_pdf")
PUBLIC_PREFIX = "/generated/manual_pdf/"
CONTEXT_CACHE: Dict[str, Any] = {}
FILE_CONTEXT_CACHE: Dict[str, Any] = {}
CONTEXT_TTL_SECONDS = 900


def set_user_agent(user_agent: str) -> None:
    aez_pdf_reader.set_user_agent(user_agent)


def search_term_from_url(value: str) -> str:
    value = (value or "").strip()
    if value.startswith("manualpdf://"):
        return urllib.parse.unquote(value.removeprefix("manualpdf://")).strip()
    return value


def normalize_manual_pdf_url(value: str) -> str:
    term = search_term_from_url(value)
    return "manualpdf://" + urllib.parse.quote(term)


def article_number_from_url(value: str) -> str:
    term = search_term_from_url(value)
    if not term:
        return ""
    return hashlib.sha1(aez_pdf_reader.normalize_text(term).encode("utf-8")).hexdigest()[:12]


def safe_pdf_name(filename: str) -> str:
    base = Path(filename or "prospekt.pdf").name
    stem = re.sub(r"[^A-Za-z0-9._ -]+", "_", base).strip(" ._")
    if not stem.lower().endswith(".pdf"):
        stem += ".pdf"
    return stem or f"prospekt_{int(time.time())}.pdf"


def pdf_files() -> List[Path]:
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    return sorted(path for path in UPLOAD_DIR.glob("*.pdf") if path.is_file())


def pdf_infos() -> List[Dict[str, Any]]:
    items = []
    for path in pdf_files():
        stat = path.stat()
        items.append(
            {
                "name": path.name,
                "size": stat.st_size,
                "size_text": f"{stat.st_size / 1024 / 1024:.1f} MB",
                "mtime": stat.st_mtime,
            }
        )
    return items


def save_upload(file_storage: Any) -> str:
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    name = safe_pdf_name(getattr(file_storage, "filename", "") or "")
    target = UPLOAD_DIR.joinpath(name)
    if target.exists():
        stem = target.stem
        suffix = target.suffix
        counter = 2
        while target.exists():
            target = UPLOAD_DIR.joinpath(f"{stem}_{counter}{suffix}")
            counter += 1
    file_storage.save(str(target))
    CONTEXT_CACHE.clear()
    return target.name


def delete_pdf(name: str) -> None:
    target = UPLOAD_DIR.joinpath(Path(name).name)
    if target.exists() and target.is_file() and target.suffix.lower() == ".pdf":
        target.unlink()
    FILE_CONTEXT_CACHE.pop(target.name, None)
    CONTEXT_CACHE.clear()


def parsed_file_context(path: Path, loaded_at: float) -> Dict[str, Any]:
    stat = path.stat()
    signature = f"{path.name}:{stat.st_size}:{stat.st_mtime_ns}"
    cached = FILE_CONTEXT_CACHE.get(path.name)
    if cached and cached.get("signature") == signature:
        return cached
    public_url = "/manual-pdfs/file/" + urllib.parse.quote(path.name)
    context = aez_pdf_reader.parse_pdf_context(public_url, path, loaded_at)
    context["signature"] = signature
    FILE_CONTEXT_CACHE[path.name] = context
    return context


def current_pdf_context() -> Dict[str, Any]:
    files = pdf_files()
    if not files:
        raise RuntimeError("Keine manuellen PDFs hochgeladen.")
    signature = "|".join(f"{path.name}:{path.stat().st_size}:{path.stat().st_mtime_ns}" for path in files)
    now = time.time()
    cached = CONTEXT_CACHE.get("context")
    if cached and cached.get("signature") == signature and now - float(cached.get("loaded_at") or 0) < CONTEXT_TTL_SECONDS:
        return cached

    pages = []
    for path in files:
        public_url = "/manual-pdfs/file/" + urllib.parse.quote(path.name)
        context = parsed_file_context(path, now)
        for page in context["pages"]:
            pages.append(
                {
                    **page,
                    "pdf_path": path,
                    "pdf_url": public_url,
                    "pdf_file_name": path.name,
                }
            )
    combined = {
        "loaded_at": now,
        "signature": signature,
        "pdf_url": "manualpdf://uploaded",
        "pdf_path": files[0],
        "pdf_file_name": "Manuelle PDFs",
        "pages": pages,
    }
    CONTEXT_CACHE["context"] = combined
    return combined


def read_manual_pdf_product(product: Dict[str, str], market: Dict[str, Any], postal_code: str) -> Dict[str, Any]:
    return aez_pdf_reader.read_pdf_product_from_context(
        product,
        current_pdf_context(),
        output_dir=GENERATED_DIR,
        public_prefix=PUBLIC_PREFIX,
        market_id="manual",
        available_service="PDF Upload",
    )
