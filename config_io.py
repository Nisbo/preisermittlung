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
            if key not in {"settings", "store", "markets", "products", "categories", "category_groups"}:
                raise ConfigError(f"Unbekannter Config-Bereich: {key}")
            section = key
            config.setdefault(section, [] if section in {"markets", "products", "categories", "category_groups"} else {})
            current_item = None
            continue

        if section in {"settings", "store"}:
            if ":" not in line:
                raise ConfigError(f"Ungueltige {section}-Zeile: {raw_line}")
            key, value = line.strip().split(":", 1)
            config[section][key.strip()] = strip_quotes(value)
            continue

        if section in {"markets", "products", "categories", "category_groups"}:
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


def migrate_legacy_store(config: Dict[str, Any]) -> None:
    store = config.get("store") if isinstance(config.get("store"), dict) else {}
    if not store:
        return

    store_provider = store.get("provider") or "rewe"
    store_market_id = store.get("market_id") or ""
    markets = config.setdefault("markets", [])
    if store_market_id and not any(
        market.get("market_id") == store_market_id and (market.get("provider") or "rewe") == store_provider
        for market in markets
    ):
        migrated_market = dict(store)
        migrated_market["provider"] = store_provider
        markets.insert(0, migrated_market)

    if store_market_id:
        for product in config.get("products") or []:
            product.setdefault("provider", store_provider)
            product.setdefault("market_id", store_market_id)


def validate_config(config: Dict[str, Any]) -> None:
    migrate_legacy_store(config)
    products = config.get("products")
    markets = config.get("markets")
    if markets is None:
        config["markets"] = []
    elif not isinstance(markets, list):
        raise ConfigError("Config-Bereich markets muss eine Liste sein.")
    if not isinstance(products, list):
        raise ConfigError("Config braucht einen products-Bereich.")
    config.setdefault("categories", [{"id": "allgemein", "name": "Allgemein"}])
    config.setdefault("category_groups", [])
    for product in products:
        if not product.get("id") or not product.get("article_number"):
            raise ConfigError("Jedes Produkt braucht id und article_number.")
        provider = product.get("provider") or ""
        if provider == "rewe" and not product.get("market_id"):
            raise ConfigError(f"REWE-Produkt {product.get('id')} braucht market_id.")
    for market in config.get("markets") or []:
        if not market.get("market_id") or not market.get("postal_code"):
            raise ConfigError("Jeder Markt braucht market_id und postal_code.")
    if not isinstance(config.get("category_groups"), list):
        raise ConfigError("Config-Bereich category_groups muss eine Liste sein.")


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

    markets = config.get("markets") or []
    if markets:
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
            for key in (
                "hit_market_url",
                "hit_search_postal_code",
                "hit_use_app_price",
                "distance_km",
            ):
                if key in market:
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
            if str(category.get("show_in_grouped", "true")).strip().lower() in {"0", "false", "no", "off", "nein"}:
                lines.append('    show_in_grouped: "false"')
            if str(category.get("searchable", "true")).strip().lower() in {"0", "false", "no", "off", "nein"}:
                lines.append('    searchable: "false"')
            if str(category.get("group_expanded", "true")).strip().lower() in {"0", "false", "no", "off", "nein"}:
                lines.append('    group_expanded: "false"')

    category_groups = config.get("category_groups") or []
    if category_groups:
        lines.append("")
        lines.append("category_groups:")
        for group in category_groups:
            lines.append(f"  - id: {quote_yaml(group.get('id'))}")
            lines.append(f"    name: {quote_yaml(group.get('name'))}")
            if group.get("color"):
                lines.append(f"    color: {quote_yaml(group.get('color'))}")
            if str(group.get("quick_group", "false")).strip().lower() in {"1", "true", "yes", "on", "ja"}:
                lines.append('    quick_group: "true"')
            category_ids = group.get("category_ids") or ""
            if isinstance(category_ids, (list, tuple)):
                category_ids = ", ".join(str(category_id) for category_id in category_ids if category_id)
            lines.append(f"    category_ids: {quote_yaml(category_ids)}")

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
        if str(product.get("mqtt_updates_enabled", "true")).strip().lower() in {"0", "false", "no", "off", "nein"}:
            lines.append('    mqtt_updates_enabled: "false"')
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
