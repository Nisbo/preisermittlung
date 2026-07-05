from __future__ import annotations

from typing import Any, Dict, List

from readers import (
    aez_pdf_reader,
    aldi_sued_reader,
    generic_reader,
    hit_reader,
    manual_pdf_reader,
    mediamarkt_reader,
    mueller_reader,
    rewe_reader,
    rossmann_reader,
)


DEFAULT_PROVIDER = "rewe"


PROVIDERS = {
    "rewe": {
        "label": "REWE",
        "markets": True,
        "kind": "shop",
    },
    "mueller": {
        "label": "Müller",
        "markets": False,
        "kind": "shop",
    },
    "mediamarkt": {
        "label": "MediaMarkt",
        "markets": False,
        "kind": "shop",
    },
    "aldi_sued": {
        "label": "ALDI Süd",
        "markets": False,
        "kind": "shop",
    },
    "rossmann": {
        "label": "Rossmann",
        "markets": False,
        "kind": "shop",
    },
    "hit": {
        "label": "HIT",
        "markets": True,
        "kind": "shop",
    },
    "generic": {
        "label": "Generic",
        "markets": False,
        "kind": "shop",
    },
    "aez_pdf": {
        "label": "AEZ Prospekt",
        "markets": False,
        "kind": "prospect",
    },
    "manual_pdf": {
        "label": "Manuelle PDFs",
        "markets": False,
        "kind": "prospect",
    }
}


def provider_label(provider: str) -> str:
    return PROVIDERS.get(provider, {}).get("label", provider.upper())


def provider_choices() -> List[Dict[str, Any]]:
    return [
        {
            "id": provider_id,
            "label": data["label"],
            "markets": bool(data.get("markets")),
            "kind": data.get("kind") or "shop",
        }
        for provider_id, data in PROVIDERS.items()
    ]


def provider_kind(provider: str) -> str:
    return str(PROVIDERS[normalize_provider(provider)].get("kind") or "shop")


def normalize_provider(provider: str | None) -> str:
    provider_id = (provider or DEFAULT_PROVIDER).lower()
    if provider_id not in PROVIDERS:
        raise RuntimeError(f"Provider nicht unterstuetzt: {provider_id}")
    return provider_id


def configure_user_agent(user_agent: str | None) -> None:
    value = (user_agent or "").strip()
    rewe_reader.set_user_agent(value)
    mueller_reader.set_user_agent(value)
    mediamarkt_reader.set_user_agent(value)
    aldi_sued_reader.set_user_agent(value)
    rossmann_reader.set_user_agent(value)
    hit_reader.set_user_agent(value)
    generic_reader.set_user_agent(value)
    aez_pdf_reader.set_user_agent(value)
    manual_pdf_reader.set_user_agent(value)


def default_user_agent() -> str:
    return mediamarkt_reader.default_user_agent()


def market_provider(market: Dict[str, Any]) -> str:
    return normalize_provider(market.get("provider"))


def product_provider(config: Dict[str, Any], product: Dict[str, Any]) -> str:
    if product.get("provider"):
        return normalize_provider(product.get("provider"))
    market_id = product.get("market_id")
    for market in rewe_reader.markets_from_config(config):
        if market.get("market_id") == market_id:
            return market_provider(market)
    return normalize_provider(DEFAULT_PROVIDER)


def provider_uses_markets(provider: str) -> bool:
    return bool(PROVIDERS[normalize_provider(provider)].get("markets"))


def virtual_markets(provider: str) -> List[Dict[str, str]]:
    provider_id = normalize_provider(provider)
    if provider_id == "mueller":
        return [
            {
                "provider": "mueller",
                "market_id": "online",
                "postal_code": "",
                "service": "online",
                "market_name": "Müller Online",
                "market_company": "Müller",
                "market_street": "",
                "market_city": "Online",
            }
        ]
    if provider_id == "mediamarkt":
        return [
            {
                "provider": "mediamarkt",
                "market_id": "online",
                "postal_code": "",
                "service": "online",
                "market_name": "MediaMarkt Online",
                "market_company": "MediaMarkt",
                "market_street": "",
                "market_city": "Online",
            }
        ]
    if provider_id == "aldi_sued":
        return [
            {
                "provider": "aldi_sued",
                "market_id": "online",
                "postal_code": "",
                "service": "online",
                "market_name": "ALDI Süd Online",
                "market_company": "ALDI Süd",
                "market_street": "",
                "market_city": "Online",
            }
        ]
    if provider_id == "rossmann":
        return [
            {
                "provider": "rossmann",
                "market_id": "online",
                "postal_code": "",
                "service": "online",
                "market_name": "Rossmann Online",
                "market_company": "Rossmann",
                "market_street": "",
                "market_city": "Online",
            }
        ]
    if provider_id == "generic":
        return [
            {
                "provider": "generic",
                "market_id": "online",
                "postal_code": "",
                "service": "online",
                "market_name": "Generic Online",
                "market_company": "Generic",
                "market_street": "",
                "market_city": "Online",
            }
        ]
    if provider_id == "aez_pdf":
        return [
            {
                "provider": "aez_pdf",
                "market_id": "weekly",
                "postal_code": "",
                "service": "pdf",
                "market_name": "AEZ Wochenblatt",
                "market_company": "AEZ",
                "market_street": "",
                "market_city": "Prospekt",
            }
        ]
    if provider_id == "manual_pdf":
        return [
            {
                "provider": "manual_pdf",
                "market_id": "manual",
                "postal_code": "",
                "service": "pdf",
                "market_name": "Manuelle PDFs",
                "market_company": "PDF Upload",
                "market_street": "",
                "market_city": "Prospekt",
            }
        ]
    return []


def market_for_product(config: Dict[str, Any], product: Dict[str, Any]) -> Dict[str, str]:
    provider_id = product_provider(config, product)
    if not provider_uses_markets(provider_id):
        return virtual_markets(provider_id)[0]
    return rewe_reader.market_for_product(config, product)


def market_for_selection(provider: str, market_id: str, markets: List[Dict[str, Any]]) -> Dict[str, str] | None:
    provider_id = normalize_provider(provider)
    for market in markets:
        if str(market.get("market_id")) == str(market_id) and market_provider(market) == provider_id:
            return dict(market)
    for market in virtual_markets(provider_id):
        if str(market.get("market_id")) == str(market_id):
            return market
    return None


def find_markets(provider: str, postal_code: str) -> List[Dict[str, str]]:
    provider_id = normalize_provider(provider)
    if provider_id == "rewe":
        return rewe_reader.find_pickup_markets_by_postal_code(postal_code)
    if provider_id == "mueller":
        return virtual_markets(provider_id)
    if provider_id == "mediamarkt":
        return virtual_markets(provider_id)
    if provider_id == "aldi_sued":
        return virtual_markets(provider_id)
    if provider_id == "rossmann":
        return virtual_markets(provider_id)
    if provider_id == "hit":
        return hit_reader.find_hit_markets_by_postal_code(postal_code)
    if provider_id == "generic":
        return virtual_markets(provider_id)
    if provider_id == "aez_pdf":
        return virtual_markets(provider_id)
    if provider_id == "manual_pdf":
        return virtual_markets(provider_id)
    raise RuntimeError(f"Marktsuche fuer {provider_id} ist noch nicht implementiert.")


def resolve_market(provider: str, market_config: Dict[str, str]) -> Dict[str, Any]:
    provider_id = normalize_provider(provider)
    if provider_id == "rewe":
        return rewe_reader.find_pickup_market(market_config)
    if provider_id == "mueller":
        return dict(market_config or virtual_markets(provider_id)[0])
    if provider_id == "mediamarkt":
        return dict(market_config or virtual_markets(provider_id)[0])
    if provider_id == "aldi_sued":
        return dict(market_config or virtual_markets(provider_id)[0])
    if provider_id == "rossmann":
        return dict(market_config or virtual_markets(provider_id)[0])
    if provider_id == "hit":
        return dict(market_config)
    if provider_id == "generic":
        return dict(market_config or virtual_markets(provider_id)[0])
    if provider_id == "aez_pdf":
        return dict(market_config or virtual_markets(provider_id)[0])
    if provider_id == "manual_pdf":
        return dict(market_config or virtual_markets(provider_id)[0])
    raise RuntimeError(f"Marktauflösung fuer {provider_id} ist noch nicht implementiert.")


def read_product(provider: str, product: Dict[str, str], market: Dict[str, Any], postal_code: str) -> Dict[str, Any]:
    provider_id = normalize_provider(provider)
    if provider_id == "rewe":
        return rewe_reader.read_rewe_product(product, market, postal_code)
    if provider_id == "mueller":
        return mueller_reader.read_mueller_product(product, market, postal_code)
    if provider_id == "mediamarkt":
        return mediamarkt_reader.read_mediamarkt_product(product, market, postal_code)
    if provider_id == "aldi_sued":
        return aldi_sued_reader.read_aldi_sued_product(product, market, postal_code)
    if provider_id == "rossmann":
        return rossmann_reader.read_rossmann_product(product, market, postal_code)
    if provider_id == "hit":
        return hit_reader.read_hit_product(product, market, postal_code)
    if provider_id == "generic":
        return generic_reader.read_generic_product(product, market, postal_code)
    if provider_id == "aez_pdf":
        return aez_pdf_reader.read_aez_pdf_product(product, market, postal_code)
    if provider_id == "manual_pdf":
        return manual_pdf_reader.read_manual_pdf_product(product, market, postal_code)
    raise RuntimeError(f"Produktabfrage fuer {provider_id} ist noch nicht implementiert.")


def market_summary(provider: str, market: Dict[str, Any]) -> Dict[str, Any]:
    provider_id = normalize_provider(provider)
    if provider_id == "rewe":
        return rewe_reader.store_from_market(market)
    if provider_id == "mueller":
        return {
            "provider": "mueller",
            "market_id": "online",
            "name": "Müller Online",
            "company": "Müller",
            "service": "Online",
        }
    if provider_id == "mediamarkt":
        return {
            "provider": "mediamarkt",
            "market_id": "online",
            "name": "MediaMarkt Online",
            "company": "MediaMarkt",
            "service": "Online",
        }
    if provider_id == "aldi_sued":
        return {
            "provider": "aldi_sued",
            "market_id": "online",
            "name": "ALDI Süd Online",
            "company": "ALDI Süd",
            "service": "Online",
        }
    if provider_id == "rossmann":
        return {
            "provider": "rossmann",
            "market_id": "online",
            "name": "Rossmann Online",
            "company": "Rossmann",
            "service": "Online",
        }
    if provider_id == "hit":
        return {
            "provider": "hit",
            "market_id": market.get("market_id"),
            "name": market.get("market_name") or "HIT Markt",
            "company": "HIT",
            "street": market.get("market_street"),
            "zip": market.get("postal_code"),
            "city": market.get("market_city"),
            "service": "Markt",
        }
    if provider_id == "generic":
        return {
            "provider": "generic",
            "market_id": "online",
            "name": "Generic Online",
            "company": "Generic",
            "service": "Online",
        }
    if provider_id == "aez_pdf":
        return {
            "provider": "aez_pdf",
            "market_id": "weekly",
            "name": "AEZ Wochenblatt",
            "company": "AEZ",
            "service": "PDF",
        }
    if provider_id == "manual_pdf":
        return {
            "provider": "manual_pdf",
            "market_id": "manual",
            "name": "Manuelle PDFs",
            "company": "PDF Upload",
            "service": "PDF",
        }
    return dict(market)


def read_prices(config: Dict[str, Any]) -> Dict[str, Any]:
    configure_user_agent((config.get("settings") or {}).get("user_agent"))
    products = []
    first_market = None
    first_provider = None
    for product in config["products"]:
        provider_id = product_provider(config, product)
        market_config = market_for_product(config, product)
        market = resolve_market(provider_id, market_config)
        first_market = first_market or market
        first_provider = first_provider or provider_id
        products.append(read_product(provider_id, product, market, market_config.get("postal_code", "")))

    return {
        "ok": True,
        "store": market_summary(first_provider or DEFAULT_PROVIDER, first_market or {}),
        "products": products,
    }


def normalize_product_url(provider: str, url: str, article_number: str) -> str:
    provider_id = normalize_provider(provider)
    if provider_id == "rewe":
        cleaned = url.strip()
        if cleaned.startswith("https://www.rewe.de/p/"):
            return cleaned.replace("https://www.rewe.de/p/", "https://www.rewe.de/shop/p/", 1)
        if cleaned.startswith("https://www.rewe.de/shop/p/"):
            return cleaned
        return f"https://www.rewe.de/shop/productList?search={article_number}"
    if provider_id == "mueller":
        return mueller_reader.normalize_mueller_url(url)
    if provider_id == "mediamarkt":
        return mediamarkt_reader.normalize_mediamarkt_url(url)
    if provider_id == "aldi_sued":
        return aldi_sued_reader.normalize_aldi_sued_url(url)
    if provider_id == "rossmann":
        return rossmann_reader.normalize_rossmann_url(url)
    if provider_id == "hit":
        return hit_reader.normalize_hit_url(url)
    if provider_id == "generic":
        return generic_reader.normalize_generic_url(url)
    if provider_id == "aez_pdf":
        return aez_pdf_reader.normalize_aez_pdf_url(url)
    if provider_id == "manual_pdf":
        return manual_pdf_reader.normalize_manual_pdf_url(url)
    return url.strip()


def provider_article_number_from_url(provider: str, url: str) -> str:
    provider_id = normalize_provider(provider)
    if provider_id == "mueller":
        return mueller_reader.article_number_from_url(url)
    if provider_id == "mediamarkt":
        return mediamarkt_reader.article_number_from_url(url)
    if provider_id == "aldi_sued":
        return aldi_sued_reader.article_number_from_url(url)
    if provider_id == "rossmann":
        return rossmann_reader.article_number_from_url(url)
    if provider_id == "hit":
        return hit_reader.article_number_from_url(url)
    if provider_id == "generic":
        return generic_reader.article_number_from_url(url)
    if provider_id == "aez_pdf":
        return aez_pdf_reader.article_number_from_url(url)
    if provider_id == "manual_pdf":
        return manual_pdf_reader.article_number_from_url(url)
    return ""


def browser_cache_infos() -> List[Dict[str, Any]]:
    return [aldi_sued_reader.cache_info(), rossmann_reader.cache_info(), generic_reader.cache_info()]


def clear_browser_cache(provider: str) -> None:
    provider_id = normalize_provider(provider)
    if provider_id == "aldi_sued":
        aldi_sued_reader.clear_cache()
        return
    if provider_id == "rossmann":
        rossmann_reader.clear_cache()
        return
    if provider_id == "generic":
        generic_reader.clear_cache()
        return
    raise RuntimeError(f"Browser-Cache fuer {provider_id} ist nicht implementiert.")
