from __future__ import annotations

import re
from typing import Dict, Optional


UNIT_LABELS = {
    "kilogramm": "kg",
    "kilogram": "kg",
    "kg": "kg",
    "gramm": "g",
    "gram": "g",
    "g": "g",
    "liter": "l",
    "litre": "l",
    "l": "l",
    "milliliter": "ml",
    "millilitre": "ml",
    "ml": "ml",
    "stück": "Stück",
    "stueck": "Stück",
    "stk": "Stück",
    "stk.": "Stück",
}


def normalize_space(value: str) -> str:
    return re.sub(r"\s+", " ", value.replace("\xa0", " ")).strip()


def normalize_unit(unit: str) -> str:
    cleaned = unit.strip().lower().rstrip(".")
    return UNIT_LABELS.get(cleaned, unit.strip())


def normalize_amount(value: str) -> str:
    cleaned = value.strip().replace(".", ",")
    if "," in cleaned:
        cleaned = cleaned.rstrip("0").rstrip(",")
    return cleaned


def normalize_package_size(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    cleaned = normalize_space(value)
    cleaned = re.sub(
        r"(?i)\b(\d+(?:[.,]\d+)?)\s*(kg|g|l|ml|stück|stueck|stk\.?)\b",
        lambda match: f"{normalize_amount(match.group(1))} {normalize_unit(match.group(2))}",
        cleaned,
    )
    return cleaned or None


def normalize_price(value: str) -> str:
    cleaned = value.strip().replace(".", ",")
    if "," not in cleaned:
        cleaned += ",00"
    whole, cents = cleaned.split(",", 1)
    cents = (cents + "00")[:2]
    return f"{whole},{cents}"


def unit_price_text(price: str, amount: str, unit: str) -> str:
    return f"{normalize_price(price)} € / {normalize_amount(amount)} {normalize_unit(unit)}"


def normalize_price_details(package_size: Optional[str] = None, unit_price: Optional[str] = None) -> Dict[str, Optional[str]]:
    raw_unit = normalize_space(unit_price or "")
    package_text = normalize_package_size(package_size)
    unit_text = None

    if raw_unit:
        package_match = re.match(r"^(.+?)\s*\((.+)\)\s*(.*)$", raw_unit)
        if package_match:
            package_text = package_text or normalize_package_size(package_match.group(1))
            raw_unit = package_match.group(2)

        equals_match = re.search(
            r"(?i)(\d+(?:[.,]\d+)?)\s*(kg|g|l|ml|stück|stueck|stk\.?)\s*=\s*(\d+(?:[.,]\d{2})?)\s*€",
            raw_unit,
        )
        if equals_match:
            unit_text = unit_price_text(equals_match.group(3), equals_match.group(1), equals_match.group(2))

        slash_match = re.search(
            r"(?i)(\d+(?:[.,]\d{2})?)\s*€\s*/\s*(\d+(?:[.,]\d+)?)\s*(kg|g|l|ml|stück|stueck|stk\.?)",
            raw_unit,
        )
        if slash_match:
            unit_text = unit_price_text(slash_match.group(1), slash_match.group(2), slash_match.group(3))

    return {
        "package_size_text": package_text,
        "unit_price_text": unit_text,
    }
