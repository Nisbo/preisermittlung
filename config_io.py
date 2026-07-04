from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional


CONFIG_PATH = Path(__file__).with_name("config.yaml")


class ConfigError(RuntimeError):
    pass


def strip_quotes(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
        return value[1:-1]
    return value


def strip_yaml_comment(line: str) -> str:
    quote = ""
    escaped = False
    for index, char in enumerate(line):
        if escaped:
            escaped = False
            continue
        if char == "\\" and quote == '"':
            escaped = True
            continue
        if char in ('"', "'"):
            if quote == char:
                quote = ""
            elif not quote:
                quote = char
            continue
        if char == "#" and not quote:
            return line[:index]
    return line


def parse_simple_yaml(path: Path) -> Dict[str, Any]:
    """Parser fuer genau die kleine config.yaml-Struktur dieses Tools."""
    if not path.exists():
        raise ConfigError(f"Config nicht gefunden: {path}")

    config: Dict[str, Any] = {}
    section: Optional[str] = None
    current_item: Optional[Dict[str, str]] = None

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = strip_yaml_comment(raw_line).rstrip()
        if not line.strip():
            continue

        if not line.startswith(" "):
            key = line.rstrip(":").strip()
            if key not in {"settings", "store", "markets", "products", "categories"}:
                raise ConfigError(f"Unbekannter Config-Bereich: {key}")
            section = key
            config.setdefault(section, [] if section in {"markets", "products", "categories"} else {})
            current_item = None
            continue

        if section in {"settings", "store"}:
            if ":" not in line:
                raise ConfigError(f"Ungueltige {section}-Zeile: {raw_line}")
            key, value = line.strip().split(":", 1)
            config[section][key.strip()] = strip_quotes(value)
            continue

        if section in {"markets", "products", "categories"}:
            stripped = line.strip()
            if stripped.startswith("- "):
                current_item = {}
                config[section].append(current_item)
                stripped = stripped[2:].strip()
                if stripped:
                    if ":" not in stripped:
                        raise ConfigError(f"Ungueltige {section}-Zeile: {raw_line}")
                    key, value = stripped.split(":", 1)
                    current_item[key.strip()] = strip_quotes(value)
                continue

            if current_item is None:
                raise ConfigError(f"Wert ohne Listeneintrag in {section}: {raw_line}")
            if ":" not in stripped:
                raise ConfigError(f"Ungueltige {section}-Zeile: {raw_line}")
            key, value = stripped.split(":", 1)
            current_item[key.strip()] = strip_quotes(value)
            continue

        raise ConfigError(f"Zeile ausserhalb eines Bereichs: {raw_line}")

    validate_config(config)
    return config


def validate_config(config: Dict[str, Any]) -> None:
    store = config.get("store")
    products = config.get("products")
    if not isinstance(store, dict):
        raise ConfigError("Config braucht einen store-Bereich.")
    if not isinstance(products, list):
        raise ConfigError("Config braucht einen products-Bereich.")
    config.setdefault("categories", [{"id": "allgemein", "name": "Allgemein"}])
    if products:
        for key in ("postal_code", "service"):
            if not store.get(key):
                raise ConfigError(f"store.{key} fehlt.")
        if not store.get("market_id") and not store.get("market_match"):
            raise ConfigError("store.market_id oder store.market_match fehlt.")
        if store["service"].lower() != "pickup":
            raise ConfigError("Aktuell wird nur store.service: pickup unterstuetzt.")
    for product in products:
        if not product.get("id") or not product.get("article_number"):
            raise ConfigError("Jedes Produkt braucht id und article_number.")
    for market in config.get("markets") or []:
        if not market.get("market_id") or not market.get("postal_code"):
            raise ConfigError("Jeder Markt braucht market_id und postal_code.")


def quote_yaml(value: Any) -> str:
    text = str(value or "")
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def write_simple_yaml(config: Dict[str, Any], path: Path = CONFIG_PATH) -> None:
    lines = []
    settings = config.get("settings") or {}
    if settings:
        lines.append("settings:")
        for key, value in settings.items():
            lines.append(f"  {key}: {quote_yaml(value)}")
        lines.append("")

    lines.append("store:")
    for key, value in (config.get("store") or {}).items():
        lines.append(f"  {key}: {quote_yaml(value)}")

    markets = config.get("markets") or []
    if markets:
        lines.append("")
        lines.append("markets:")
        for market in markets:
            lines.append(f"  - provider: {quote_yaml(market.get('provider') or 'rewe')}")
            lines.append(f"    market_id: {quote_yaml(market.get('market_id'))}")
            for key in (
                "postal_code",
                "service",
                "market_name",
                "market_company",
                "market_street",
                "market_city",
                "market_match",
                "market_match_2",
            ):
                lines.append(f"    {key}: {quote_yaml(market.get(key))}")

    categories = config.get("categories") or []
    if categories:
        lines.append("")
        lines.append("categories:")
        for category in categories:
            lines.append(f"  - id: {quote_yaml(category.get('id'))}")
            lines.append(f"    name: {quote_yaml(category.get('name'))}")
            if category.get("color"):
                lines.append(f"    color: {quote_yaml(category.get('color'))}")
            if str(category.get("quick_cat", "false")).strip().lower() in {"1", "true", "yes", "on", "ja"}:
                lines.append('    quick_cat: "true"')

    lines.append("")
    lines.append("products:")
    for product in config.get("products") or []:
        lines.append(f"  - id: {quote_yaml(product.get('id'))}")
        lines.append(f"    article_number: {quote_yaml(product.get('article_number'))}")
        lines.append(f"    name: {quote_yaml(product.get('name'))}")
        if product.get("category_id"):
            lines.append(f"    category_id: {quote_yaml(product.get('category_id'))}")
        if product.get("target_price_cents") not in (None, ""):
            lines.append(f"    target_price_cents: {quote_yaml(product.get('target_price_cents'))}")
        if str(product.get("enabled", "true")).strip().lower() in {"0", "false", "no", "off", "nein"}:
            lines.append('    enabled: "false"')
        if product.get("provider"):
            lines.append(f"    provider: {quote_yaml(product.get('provider'))}")
        if product.get("market_id"):
            lines.append(f"    market_id: {quote_yaml(product.get('market_id'))}")
        if product.get("product_url"):
            lines.append(f"    product_url: {quote_yaml(product.get('product_url'))}")
        for key in ("generic_candidate_index", "generic_source", "generic_initial_context"):
            if product.get(key):
                lines.append(f"    {key}: {quote_yaml(product.get(key))}")

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
