"""
Deterministic pricing calculator.

Design choice: pricing is NEVER computed by the LLM. The LLM's only job is
extraction (turning free text into structured parameters); once we have
structured data, price = plain Python business rules read from the price
list file (data/price_list.json by default, see price_list.py). This keeps
quotes reproducible, auditable, and easy for a human to review before
anything goes to a customer.

Transparency: the result is a list of PriceLine rows (rate × volume, each
coefficient, materials, minimum-order top-up), each signed with the path of
the rule in the price list and its source tag. The lines always sum to the
total, so the reviewer can see exactly how the number was built.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from extractor import ExtractedRequest
from price_list import LEGACY_SERVICE_ALIASES, PRICE_LIST_PATH, load_price_list, normalize_price_list

UNIT_LABELS = {"m2": "м²", "m": "м.п.", "lm": "м.п.", "pcs": "шт."}
FIELD_LABELS = {
    "service": "вид работ",
    "quantity": "объём работ",
    "weight_kg": "вес груза",
    "height_m": "высота объекта / этаж",
    "location": "город объекта",
}


def _money(x: float) -> str:
    return f"{x:,.2f}".replace(",", " ").replace(".", ",")


def _num(x: float) -> str:
    return f"{x:g}".replace(".", ",")


def _coef(k: float) -> str:
    """1.0 -> "1,0", 1.25 -> "1,25": a coefficient always reads as one."""
    s = _num(k)
    return s if "," in s else s + ",0"


@dataclass
class PriceLine:
    group: str          # "works" | "materials" | "minimum"
    label: str
    formula: str
    amount: float       # this line's contribution -- lines sum to the total
    source: str         # path of the value in the price list file
    source_type: str    # client / market / assumption / demo
    evidence: Optional[str] = None  # request fragment that triggered the line

    def to_dict(self) -> dict:
        return {
            "group": self.group,
            "label": self.label,
            "formula": self.formula,
            "amount": round(self.amount, 2),
            "source": self.source,
            "source_type": self.source_type,
            "evidence": self.evidence,
        }


@dataclass
class PriceBreakdown:
    service_name: str
    quantity: Optional[float]
    unit: str
    base_price: Optional[float]
    height_coefficient: float
    urgency_multiplier: float
    subtotal: Optional[float]
    minimum_applied: bool
    total: Optional[float]
    currency: str
    warnings: list[str]
    status: str = "ok"  # "ok" | "manual_estimate" | "needs_clarification"
    lines: list[PriceLine] = field(default_factory=list)
    works_total: Optional[float] = None
    materials_total: Optional[float] = None
    clarify_fields: list[str] = field(default_factory=list)
    manual_reason: Optional[str] = None
    service_id: Optional[str] = None
    company: Optional[str] = None
    price_list_version: str = ""

    def to_dict(self) -> dict:
        def r(x):
            return None if x is None else round(x, 2)

        return {
            "status": self.status,
            "service_id": self.service_id,
            "service_name": self.service_name,
            "quantity": self.quantity,
            "unit": self.unit,
            "base_price": self.base_price,
            "height_coefficient": self.height_coefficient,
            "urgency_multiplier": self.urgency_multiplier,
            "lines": [line.to_dict() for line in self.lines],
            "works_total": r(self.works_total),
            "materials_total": r(self.materials_total),
            "subtotal": r(self.subtotal),
            "minimum_applied": self.minimum_applied,
            "total": r(self.total),
            "currency": self.currency,
            "manual_reason": self.manual_reason,
            "clarify_fields": self.clarify_fields,
            "warnings": self.warnings,
            "price_list_version": self.price_list_version,
        }


def _load_price_list(path: Path = PRICE_LIST_PATH) -> dict:
    return load_price_list(path)


def _norm_text(text: str) -> str:
    return text.lower().replace("ё", "е")


def _find_trigger(text: str, triggers: list[str]) -> Optional[str]:
    """Case/ё-insensitive match anchored at a word start, so the trigger
    "монтаж вывес" does not fire on "демонтаж вывески"."""
    norm = _norm_text(text)
    for trigger in triggers:
        if re.search(r"(?<![а-яa-z0-9])" + re.escape(_norm_text(trigger)), norm):
            return trigger
    return None


def _height_tier(height_m: float, tiers: list[dict]) -> tuple[dict, str]:
    prev_max = None
    for tier in tiers:
        if tier["max_m"] is None or height_m <= tier["max_m"]:
            span = f"до {_num(tier['max_m'])} м" if tier["max_m"] is not None else f"выше {_num(prev_max)} м"
            return tier, span
        prev_max = tier["max_m"]
    return tiers[-1], f"выше {_num(prev_max)} м"


def height_rule_for(pl: dict, service: dict) -> Optional[dict]:
    """The height rule that applies to a service, or None if it is excluded
    (cargo lift, tree removal) or the price list has no height rule."""
    rule = service.get("height") or pl["rules"]["height"]
    if not rule or service["id"] in rule["exclude_services"]:
        return None
    return rule


def height_coefficient(rule: dict, height_m: Optional[float], floor: Optional[int]) -> tuple[float, str, bool]:
    """(k, formula for the price line, height known?). A floor is converted
    to meters via floor_to_m; unknown height takes the top tier."""
    if height_m is not None:
        condition = f"высота {_num(height_m)} м"
    elif floor is not None and rule["floor_to_m"]:
        height_m = floor * rule["floor_to_m"]
        condition = f"{floor} этаж × {_num(rule['floor_to_m'])} м = {_num(height_m)} м"
    if height_m is None:
        k = rule["tiers"][-1]["k"]
        return k, f"×{_coef(k)} (высота не указана — принят коэффициент {_coef(k)}, уточнить у клиента)", False
    tier, span = _height_tier(height_m, rule["tiers"])
    return tier["k"], f"×{_coef(tier['k'])} ({condition}, ступень «{span}»)", True


def _source_path(rule: dict, default: str) -> str:
    return rule.get("path", default)


def _stub(pl: dict, extracted: ExtractedRequest, service: Optional[dict], status: str,
          warnings: list[str], clarify: list[str] = (), reason: Optional[str] = None) -> PriceBreakdown:
    """Result without a number: the system never invents a price."""
    return PriceBreakdown(
        service_name=service["name"] if service else "Вид работ не определён",
        quantity=extracted.quantity,
        unit=service["unit"] if service else (extracted.unit or ""),
        base_price=None,
        height_coefficient=1.0,
        urgency_multiplier=1.0,
        subtotal=None,
        minimum_applied=False,
        total=None,
        currency=pl["currency"],
        warnings=warnings,
        status=status,
        clarify_fields=list(clarify),
        manual_reason=reason,
        service_id=service["id"] if service else None,
        company=pl["company"],
        price_list_version=pl["version"],
    )


def _manual(pl, extracted, service, reason: str) -> PriceBreakdown:
    return _stub(pl, extracted, service, "manual_estimate",
                 [f"Требуется осмотр объекта / ручной расчёт: {reason}"], reason=reason)


def calculate_price(extracted: ExtractedRequest, price_list: Optional[dict] = None) -> PriceBreakdown:
    """Order of rules follows pricing_rules.application_order:
    rate × volume × height × complex access -> + urgency (only if requested)
    -> + materials as a separate line -> minimum order."""
    pl = normalize_price_list(price_list) if price_list is not None else _load_price_list()
    rules = pl["rules"]
    text = extracted.raw_text
    warnings: list[str] = []
    lines: list[PriceLine] = []
    clarify: list[str] = []

    # 1. Negotiated work ("договорная"): no price at all, regardless of service.
    for item in pl["manual_items"]:
        trigger = _find_trigger(text, item.get("triggers", []))
        if trigger:
            return _manual(pl, extracted, None, f"«{item['name']}» (в заявке: «{trigger}»)")

    if extracted.service is None:
        return _stub(pl, extracted, None, "needs_clarification",
                     ["Не удалось определить вид работ — уточнить у клиента."], clarify=["service"])

    service_id = extracted.service
    if service_id not in pl["services"]:
        if service_id in LEGACY_SERVICE_ALIASES and LEGACY_SERVICE_ALIASES[service_id] is None:
            return _manual(pl, extracted, None, f"услуга «{service_id}» отсутствует в прайсе {pl['version']}")
        service_id = LEGACY_SERVICE_ALIASES.get(service_id)
    if service_id is None or service_id not in pl["services"]:
        raise ValueError(f"Unknown or undetected service: {extracted.service!r}")
    service = pl["services"][service_id]

    # 2. Winter peak: snow work during heavy snowfall is priced by hand only.
    winter = rules["winter_peak"]
    if winter and service_id in winter.get("services", []):
        trigger = _find_trigger(text, winter.get("triggers", []))
        if trigger:
            return _manual(pl, extracted, service, f"пик снегопадов (в заявке: «{trigger}»), цена кратно выше обычной")
        warnings.append(winter["warning"])

    weight_tiers = service.get("weight_tiers_kg")
    if service["rate"] is None and not weight_tiers:
        return _manual(pl, extracted, service, f"в прайсе нет ставки для «{service['name']}»")
    if weight_tiers and extracted.weight_kg is not None and extracted.weight_kg > weight_tiers[-1]["max_kg"]:
        return _manual(pl, extracted, service,
                       f"груз {_num(extracted.weight_kg)} кг тяжелее {_num(weight_tiers[-1]['max_kg'])} кг")

    # 3. Missing inputs: ask the client instead of quoting 0.
    if not extracted.quantity:
        clarify.append("quantity")
    if weight_tiers and extracted.weight_kg is None:
        clarify.append("weight_kg")
    if extracted.location is None:
        clarify.append("location")
    if "quantity" in clarify or "weight_kg" in clarify:
        missing = ", ".join(FIELD_LABELS[f] for f in clarify)
        return _stub(pl, extracted, service, "needs_clarification",
                     warnings + [f"Не хватает данных для расчёта: {missing}."], clarify=clarify)

    quantity = extracted.quantity
    unit_label = UNIT_LABELS.get(service["unit"], service["unit"])
    service_name = service["name"]

    # 4. Works: rate × volume (weight tier for cargo, × dismantle factor).
    dismantle = rules["dismantle_factor"]
    if weight_tiers:
        tier = next(t for t in weight_tiers if extracted.weight_kg <= t["max_kg"])
        rate = tier["price"]
        formula = f"{_money(rate)} × {_num(quantity)} {unit_label} (груз {_num(extracted.weight_kg)} кг, ступень до {_num(tier['max_kg'])} кг)"
        source, source_type = f"services.{service_id}.weight_tiers_kg", service["source"]
    elif (extracted.is_dismantle and dismantle and service.get("category") == "mounting"
          and service_name.startswith("Монтаж")):
        rate = round(service["rate"] * dismantle["value"], 2)
        service_name = "Демонтаж" + service_name[len("Монтаж"):]
        formula = f"{_money(service['rate'])} × {_num(dismantle['value'])} (демонтаж от ставки монтажа) × {_num(quantity)} {unit_label}"
        source = f"{service['rate_source']} × pricing_rules.dismantle_factor"
        source_type = "/".join(dict.fromkeys([service["source"], dismantle["source"]]))
    else:
        rate = service["rate"]
        formula = f"{_money(rate)} × {_num(quantity)} {unit_label}"
        source, source_type = service["rate_source"], service["source"]
    running = round(rate * quantity, 2)
    lines.append(PriceLine("works", f"{service_name}: ставка × объём", formula, running, source, source_type))

    # 5. × height (floor converted to meters when only the floor is given).
    height_coeff = 1.0
    height_rule = height_rule_for(pl, service)
    if height_rule:
        height_coeff, formula, known = height_coefficient(height_rule, extracted.height_m, extracted.floor)
        if not known:
            clarify.insert(0, "height_m")
            warnings.append(f"Высота не указана — принят коэффициент "
                            f"{_num(height_coeff)}, уточнить у клиента.")
        delta = round(running * (height_coeff - 1.0), 2)
        running += delta
        lines.append(PriceLine("works", "Коэффициент высоты", formula, delta,
                               height_rule["path"], height_rule["source"]))

    # 6. × complex access.
    access = rules["complex_access"]
    if access:
        trigger = _find_trigger(text, access["triggers"])
        if trigger:
            delta = round(running * (access["k"] - 1.0), 2)
            running += delta
            lines.append(PriceLine("works", "Сложный доступ", f"×{_coef(access['k'])}", delta,
                                   "pricing_rules.complex_access", access["source"], evidence=trigger))

    # 7. + urgency, only when the request explicitly asks to speed up.
    urgency_multiplier = 1.0
    if extracted.urgency == "urgent_within_week":
        urgency = rules["urgency"]
        urgency_multiplier = 1.0 + urgency["surcharge"]
        delta = round(running * urgency["surcharge"], 2)
        running += delta
        lines.append(PriceLine("works", "Срочность (запрошена в заявке)",
                               f"+{_num(round(urgency['surcharge'] * 100, 2))}% к стоимости работ", delta,
                               _source_path(urgency, "pricing_rules.urgency"), urgency["source"],
                               evidence=extracted.urgency_evidence))
    works_total = running

    # 8. Materials: always a separate line, no height/urgency on top.
    materials_total = 0.0
    materials = rules["materials"]
    if service.get("material_rate"):
        materials_total = round(service["material_rate"] * quantity, 2)
        lines.append(PriceLine("materials", "Материалы",
                               f"{_money(service['material_rate'])} × {_num(quantity)} {unit_label}",
                               materials_total, f"services.{service_id}.material_rate", service["source"]))
    elif materials:
        lines.append(PriceLine("materials", "Материалы", "не требуются для этого вида работ", 0.0,
                               "pricing_rules.materials", materials["source"]))

    # 9. Minimum order.
    subtotal = round(works_total + materials_total, 2)
    minimum_rule = rules["minimum_order"]
    minimum = minimum_rule["amount"]
    minimum_applied = subtotal < minimum
    if minimum_applied:
        lines.append(PriceLine(
            "minimum", "Минимальная сумма заказа",
            f"расчёт {_money(subtotal)} меньше минимальной суммы заказа {_money(minimum)} — к оплате {_money(minimum)}",
            round(minimum - subtotal, 2), _source_path(minimum_rule, "pricing_rules.minimum_order"),
            minimum_rule["source"],
        ))
    total = round(sum(line.amount for line in lines), 2)

    if extracted.location is None:
        warnings.append("Не указан город объекта — уточнить перед отправкой (может влиять на стоимость мобилизации).")

    return PriceBreakdown(
        service_name=service_name,
        quantity=quantity,
        unit=service["unit"],
        base_price=rate,
        height_coefficient=height_coeff,
        urgency_multiplier=urgency_multiplier,
        subtotal=subtotal,
        minimum_applied=minimum_applied,
        total=total,
        currency=pl["currency"],
        warnings=warnings,
        lines=lines,
        works_total=works_total,
        materials_total=materials_total,
        clarify_fields=clarify,
        service_id=service_id,
        company=pl["company"],
        price_list_version=pl["version"],
    )
