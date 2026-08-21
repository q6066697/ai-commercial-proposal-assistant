"""
Deterministic pricing calculator.

Design choice: pricing is NEVER computed by the LLM. The LLM's only job is
extraction (turning free text into structured parameters); once we have
structured data, price = plain Python business rules read from
data/pricing/demo_price_list.json. This keeps quotes reproducible, auditable,
and easy for a human to review before anything goes to a customer.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from extractor import ExtractedRequest

PRICE_LIST_PATH = Path(__file__).resolve().parent.parent / "data" / "pricing" / "demo_price_list.json"


@dataclass
class PriceBreakdown:
    service_name: str
    quantity: float
    unit: str
    base_price: float
    height_coefficient: float
    urgency_multiplier: float
    subtotal: float
    minimum_applied: bool
    total: float
    currency: str
    warnings: list[str]

    def to_dict(self) -> dict:
        return {
            "service_name": self.service_name,
            "quantity": self.quantity,
            "unit": self.unit,
            "base_price": self.base_price,
            "height_coefficient": self.height_coefficient,
            "urgency_multiplier": self.urgency_multiplier,
            "subtotal": round(self.subtotal, 2),
            "minimum_applied": self.minimum_applied,
            "total": round(self.total, 2),
            "currency": self.currency,
            "warnings": self.warnings,
        }


def _load_price_list(path: Path = PRICE_LIST_PATH) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _height_coefficient(height_m: Optional[float], tiers: list[dict]) -> float:
    if height_m is None:
        # Conservative default: assume the tallest tier if height is unknown,
        # and flag it as a warning so a human reviews before quoting.
        return tiers[-1]["coefficient"]
    for tier in tiers:
        if tier["max_height_m"] is None or height_m <= tier["max_height_m"]:
            return tier["coefficient"]
    return tiers[-1]["coefficient"]


def calculate_price(extracted: ExtractedRequest, price_list: Optional[dict] = None) -> PriceBreakdown:
    price_list = price_list or _load_price_list()
    warnings: list[str] = []

    if extracted.service is None or extracted.service not in price_list["services"]:
        raise ValueError(f"Unknown or undetected service: {extracted.service!r}")

    service_cfg = price_list["services"][extracted.service]

    quantity = extracted.quantity
    if quantity is None:
        warnings.append("Quantity not found in request — cannot produce a firm price. Needs human input.")
        quantity = 0.0

    height_coeff = _height_coefficient(extracted.height_m, service_cfg["height_coefficients"])
    if extracted.height_m is None:
        warnings.append("Height not found in request — worst-case coefficient applied, review before sending.")

    urgency_multiplier = price_list["urgency_surcharge"].get(extracted.urgency, 1.0)

    subtotal = service_cfg["base_price"] * quantity * height_coeff * urgency_multiplier

    minimum = price_list["minimum_order_byn"]
    minimum_applied = subtotal < minimum
    total = max(subtotal, minimum) if quantity > 0 else 0.0

    if extracted.location is None:
        warnings.append("Location not found in request — confirm before quoting (may affect mobilization cost).")

    return PriceBreakdown(
        service_name=service_cfg["name_ru"],
        quantity=quantity,
        unit=service_cfg["unit"],
        base_price=service_cfg["base_price"],
        height_coefficient=height_coeff,
        urgency_multiplier=urgency_multiplier,
        subtotal=subtotal,
        minimum_applied=minimum_applied,
        total=total,
        currency=price_list["currency"],
        warnings=warnings,
    )
