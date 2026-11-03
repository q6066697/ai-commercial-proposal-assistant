"""
Loads a price list file into the one normalized shape the calculator uses.

Two on-disk formats are supported:

- v0.3+ (data/price_list.json): `services` is a list of {"id", "rate", ...},
  every pricing rule lives in one `pricing_rules` block and carries a
  `source` tag (client / market / assumption). This is the default.
- legacy demo (data/pricing/demo_price_list.json): `services` is a dict with
  per-service height tiers. Kept so the original v0.1 demo numbers stay
  reproducible (see tests/test_pipeline.py).

Prices and rules live only in these JSON files; nothing here hardcodes a
number. Normalizing up front means the calculator has a single code path
and every value it uses can be traced back to a path in the source file.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PRICE_LIST_PATH = ROOT / "data" / "price_list.json"
LEGACY_PRICE_LIST_PATH = ROOT / "data" / "pricing" / "demo_price_list.json"

# Service ids the extractor and eval set still use (v0.1 schema), mapped to
# their v0.3 counterparts. None = no counterpart in v0.3 -> manual estimate.
LEGACY_SERVICE_ALIASES = {
    "facade_cleaning": "facade_washing",
    "seam_sealing": "joint_sealing",
    "metal_structures_painting": None,
}


def load_price_list(path: Path = PRICE_LIST_PATH) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return normalize_price_list(json.load(f))


def normalize_price_list(raw: dict) -> dict:
    if raw.get("_normalized"):
        return raw
    if isinstance(raw["services"], dict):
        return _from_legacy(raw)
    return _from_v03(raw)


def _from_v03(raw: dict) -> dict:
    rules = raw.get("pricing_rules", {})
    height = rules.get("height_coefficients")
    services = {}
    for s in raw["services"]:
        services[s["id"]] = {**s, "rate_source": f"services.{s['id']}.rate", "source": s.get("source", "market")}

    manual_items = []
    for item in raw.get("manual_estimate_only", {}).get("items", []):
        if isinstance(item, str):
            item = {"name": item, "triggers": []}
        manual_items.append(item)

    return {
        "_normalized": True,
        "version": raw.get("meta", {}).get("version", "unknown"),
        "currency": raw.get("meta", {}).get("currency", "BYN"),
        "company": raw.get("meta", {}).get("company"),
        "services": services,
        "manual_items": manual_items,
        "rules": {
            "minimum_order": rules.get("minimum_order"),
            "height": None if height is None else {
                "tiers": height["tiers"],
                "floor_to_m": height.get("floor_to_m"),
                "exclude_services": height.get("exclude_services", []),
                "source": height.get("source", "assumption"),
                "path": "pricing_rules.height_coefficients",
            },
            "urgency": rules.get("urgency"),
            "dismantle_factor": rules.get("dismantle_factor"),
            "complex_access": rules.get("complex_access"),
            "winter_peak": rules.get("winter_peak"),
            "materials": rules.get("materials"),
        },
    }


def _from_legacy(raw: dict) -> dict:
    services = {}
    for service_id, s in raw["services"].items():
        services[service_id] = {
            "id": service_id,
            "name": s["name_ru"],
            "unit": s["unit"],
            "rate": s["base_price"],
            "rate_source": f"services.{service_id}.base_price",
            "source": "demo",
            "height": {
                "tiers": [{"max_m": t["max_height_m"], "k": t["coefficient"]} for t in s["height_coefficients"]],
                "floor_to_m": None,
                "exclude_services": [],
                "source": "demo",
                "path": f"services.{service_id}.height_coefficients",
            },
        }

    urgent_multiplier = raw.get("urgency_surcharge", {}).get("urgent_within_week", 1.0)
    return {
        "_normalized": True,
        "version": "0.1-demo",
        "currency": raw["currency"],
        "company": None,
        "services": services,
        "manual_items": [],
        "rules": {
            "minimum_order": {"amount": raw["minimum_order_byn"], "source": "demo", "path": "minimum_order_byn"},
            "height": None,
            "urgency": {"surcharge": round(urgent_multiplier - 1.0, 4), "source": "demo", "path": "urgency_surcharge"},
            "dismantle_factor": None,
            "complex_access": None,
            "winter_peak": None,
            "materials": None,
        },
    }
