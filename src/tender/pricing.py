"""
Prices the scope of a tender with the same price list and rules as the
single-request calculator (src/calculator.py).

- Each confirmed scope line: rate × volume × repeats, one row per line.
- Height: one coefficient for the tender (height block, floors -> meters,
  unknown -> top tier), applied to the works subtotal of the lines it
  concerns; complex access likewise for lines whose description triggers it.
- Materials: separate rows.
- Minimum order: once, on the tender total -- not per line.
- schedule.mode == "on_request" (framework contract, work by request): no
  total. A price per unit for each service instead, the volume only for
  reference, and a note that the minimum order applies to each request.

Lines that cannot be priced are listed, never guessed: scope line whose
quote failed the grounding check, service not in the price list, work from
manual_estimate_only, missing volume, unit that doesn't match the service.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from calculator import (UNIT_LABELS, PriceLine, _coef, _find_trigger, _money, _num,
                        height_coefficient, height_rule_for)
from price_list import LEGACY_SERVICE_ALIASES, load_price_list, normalize_price_list
from tender.models import ScopeItem, TenderRequirements

SIDE_LABELS = {"outer": "снаружи", "inner": "изнутри"}
CURRENCY_NOTE = "Цена ориентировочная: прайс для РБ (BYN), тендер в другой стране."


@dataclass
class UnitPrice:
    service_id: str
    service_name: str
    unit: str
    formula: str
    price_per_unit: float
    reference_volume: float  # sum of volume × repeats in the tender, not guaranteed

    def to_dict(self) -> dict:
        return {"service_id": self.service_id, "service_name": self.service_name, "unit": self.unit,
                "formula": self.formula, "price_per_unit": self.price_per_unit,
                "reference_volume": self.reference_volume}


@dataclass
class TenderQuote:
    mode: str  # "total" | "unit_prices"
    currency: str
    price_list_version: str
    lines: list[PriceLine] = field(default_factory=list)
    unit_prices: list[UnitPrice] = field(default_factory=list)
    works_total: Optional[float] = None
    materials_total: Optional[float] = None
    subtotal: Optional[float] = None
    minimum_applied: bool = False
    total: Optional[float] = None
    not_priced: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "mode": self.mode, "currency": self.currency, "price_list_version": self.price_list_version,
            "lines": [l.to_dict() for l in self.lines],
            "unit_prices": [u.to_dict() for u in self.unit_prices],
            "works_total": self.works_total, "materials_total": self.materials_total,
            "subtotal": self.subtotal, "minimum_applied": self.minimum_applied, "total": self.total,
            "not_priced": self.not_priced, "notes": self.notes, "warnings": self.warnings,
        }


def _label(service: dict, item: ScopeItem) -> str:
    details = [item.building, item.surface, SIDE_LABELS.get(item.side or ""), item.period]
    details = [d for d in details if d]
    return service["name"] + (f" ({', '.join(details)})" if details else "")


def _priceable(pl: dict, item: ScopeItem) -> tuple[Optional[dict], Optional[str]]:
    """(service, None) or (None, reason it is not priced)."""
    if item.evidence.verified is False:
        return None, f"«{item.description}»: цитата не подтверждена в документах ({item.evidence.match})"
    for manual in pl["manual_items"]:
        trigger = _find_trigger(item.description, manual.get("triggers", []))
        if trigger:
            return None, f"«{item.description}»: договорная работа «{manual['name']}» — ручной расчёт"
    service_id = item.service_id
    if service_id not in pl["services"]:
        service_id = LEGACY_SERVICE_ALIASES.get(service_id)
    if service_id is None or service_id not in pl["services"]:
        return None, f"«{item.description}»: услуги нет в прайсе — ручной расчёт"
    service = pl["services"][service_id]
    if service["rate"] is None:
        return None, f"«{item.description}»: у услуги «{service['name']}» нет ставки — ручной расчёт"
    if item.volume is None:
        return None, f"«{item.description}»: объём не указан — уточнить у заказчика"
    if item.unit and UNIT_LABELS.get(item.unit) != UNIT_LABELS.get(service["unit"]):
        return None, (f"«{item.description}»: единица {item.unit} не совпадает с прайсом "
                      f"({service['unit']}) — ручной расчёт")
    return service, None


def price_tender(req: TenderRequirements, price_list: Optional[dict] = None) -> TenderQuote:
    pl = normalize_price_list(price_list) if price_list is not None else load_price_list()
    rules = pl["rules"]
    quote = TenderQuote(
        mode="unit_prices" if req.schedule.mode == "on_request" else "total",
        currency=pl["currency"], price_list_version=pl["version"],
    )
    if req.tender.country != "BY":
        quote.notes.append(CURRENCY_NOTE)

    priced: list[tuple[ScopeItem, dict]] = []
    for item in req.scope:
        service, reason = _priceable(pl, item)
        if service:
            priced.append((item, service))
        else:
            quote.not_priced.append(reason)

    height_m = req.height.max_height_m if req.height else None
    floors = req.height.floors if req.height else None
    access = rules["complex_access"]

    def height_for(service: dict) -> tuple[float, Optional[str], Optional[dict]]:
        rule = height_rule_for(pl, service)
        if not rule:
            return 1.0, None, None
        k, formula, known = height_coefficient(rule, height_m, floors)
        if not known:
            warning = f"Высота в документации не указана — принят коэффициент {_num(k)}, уточнить у заказчика."
            if warning not in quote.warnings:
                quote.warnings.append(warning)
        return k, formula, rule

    def access_trigger(item: ScopeItem) -> Optional[str]:
        return _find_trigger(item.description, access["triggers"]) if access else None

    if quote.mode == "unit_prices":
        _unit_prices(quote, priced, height_for, access_trigger, access, rules)
        return quote

    # --- total ---------------------------------------------------------------
    height_base: dict[tuple, list] = {}  # (k, formula, rule path) -> [works amounts]
    access_base: list[float] = []
    materials: list[PriceLine] = []
    for item, service in priced:
        unit_label = UNIT_LABELS.get(service["unit"], service["unit"])
        repeats = f" × {item.repeats} раза" if item.repeats > 1 else ""
        amount = round(service["rate"] * item.volume * item.repeats, 2)
        quote.lines.append(PriceLine(
            "works", _label(service, item), f"{_money(service['rate'])} × {_num(item.volume)} {unit_label}{repeats}",
            amount, service["rate_source"], service["source"], evidence=item.evidence.quote,
        ))
        k, formula, rule = height_for(service)
        if rule:
            height_base.setdefault((k, formula, rule["path"], rule["source"]), []).append(amount)
        if access_trigger(item):
            access_base.append(round(amount * k, 2))
        if service.get("material_rate"):
            materials.append(PriceLine(
                "materials", f"Материалы: {_label(service, item)}",
                f"{_money(service['material_rate'])} × {_num(item.volume)} {unit_label}{repeats}",
                round(service["material_rate"] * item.volume * item.repeats, 2),
                f"services.{service['id']}.material_rate", service["source"],
            ))

    for (k, formula, path, source), amounts in height_base.items():
        if k != 1.0:
            base = round(sum(amounts), 2)
            quote.lines.append(PriceLine("works", "Коэффициент высоты",
                                         f"{formula} к работам на {_money(base)}",
                                         round(base * (k - 1.0), 2), path, source))
    if access_base:
        base = round(sum(access_base), 2)
        quote.lines.append(PriceLine("works", "Сложный доступ", f"×{_coef(access['k'])} к работам на {_money(base)}",
                                     round(base * (access["k"] - 1.0), 2), "pricing_rules.complex_access",
                                     access["source"]))
    quote.works_total = round(sum(l.amount for l in quote.lines), 2)

    if materials:
        quote.lines.extend(materials)
    elif rules["materials"] and priced:
        quote.lines.append(PriceLine("materials", "Материалы", "не требуются для этих видов работ", 0.0,
                                     "pricing_rules.materials", rules["materials"]["source"]))
    quote.materials_total = round(sum(l.amount for l in materials), 2)

    if not priced:
        quote.warnings.append("Ни одна строка объёмов не может быть рассчитана автоматически.")
        return quote

    quote.subtotal = round(quote.works_total + quote.materials_total, 2)
    minimum = rules["minimum_order"]
    if quote.subtotal < minimum["amount"]:
        quote.minimum_applied = True
        quote.lines.append(PriceLine(
            "minimum", "Минимальная сумма заказа",
            f"расчёт {_money(quote.subtotal)} меньше минимальной суммы заказа {_money(minimum['amount'])} "
            f"— к оплате {_money(minimum['amount'])}",
            round(minimum["amount"] - quote.subtotal, 2), minimum.get("path", "pricing_rules.minimum_order"),
            minimum["source"],
        ))
    quote.total = round(sum(l.amount for l in quote.lines), 2)
    if quote.not_priced:
        quote.warnings.append("Итог не включает строки из списка «не рассчитано» — их нужно добавить вручную.")
    return quote


def _unit_prices(quote: TenderQuote, priced, height_for, access_trigger, access, rules) -> None:
    by_service: dict[str, list[ScopeItem]] = {}
    services: dict[str, dict] = {}
    for item, service in priced:
        by_service.setdefault(service["id"], []).append(item)
        services[service["id"]] = service

    for service_id, items in by_service.items():
        service = services[service_id]
        unit_label = UNIT_LABELS.get(service["unit"], service["unit"])
        k, height_formula, _ = height_for(service)
        price = service["rate"] * k
        formula = f"{_money(service['rate'])}" + (f" {height_formula}" if k != 1.0 else "")
        if access and any(access_trigger(i) for i in items):
            price *= access["k"]
            formula += f" ×{_coef(access['k'])} (сложный доступ)"
        if service.get("material_rate"):
            price += service["material_rate"]
            formula += f" + материалы {_money(service['material_rate'])}"
        quote.unit_prices.append(UnitPrice(
            service_id, service["name"], unit_label, formula, round(price, 2),
            round(sum(i.volume * i.repeats for i in items), 2),
        ))

    minimum = rules["minimum_order"]["amount"]
    quote.notes.append("Рамочный договор: работы по заявкам заказчика, объём не гарантирован. "
                       "Указана цена за единицу; объём из документации — справочно.")
    quote.notes.append(f"Минимальная сумма одной заявки — {_money(minimum)} {quote.currency}.")
