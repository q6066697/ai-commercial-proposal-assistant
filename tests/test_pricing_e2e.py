"""
End-to-end: free-text request -> rule-based extractor -> calculator on the
default v0.3 price list. These are the scenarios the client cares about most
(below minimum, height, urgency, negotiated work, heavy cargo).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from calculator import calculate_price
from extractor import extract_rule_based

ROOT = Path(__file__).resolve().parent.parent


def quote(text: str):
    return calculate_price(extract_rule_based(text))


def test_request_below_minimum():
    b = quote("Здравствуйте! Нужна мойка фасада офиса. Площадь 200 м2, высота 10 м. г. Минск.")
    assert b.status == "ok"
    assert b.subtotal == 300.0 and b.minimum_applied and b.total == 650.0


def test_request_at_height_given_in_floors():
    b = quote("Мойка фасада жилого дома, 1000 м2, работы до 7 этажа. г. Минск.")
    assert b.height_coefficient == 1.2
    assert b.total == 1800.0
    assert "7 этаж × 3 м = 21 м" in b.lines[1].formula


def test_urgent_request():
    b = quote("Покраска фасада 500 м2, высота 12 м, г. Минск. Просим выполнить срочно.")
    assert b.urgency_multiplier == 1.2
    assert b.works_total == 2400.0 and b.materials_total == 1250.0
    assert b.total == 3650.0
    assert next(l for l in b.lines if l.label.startswith("Срочность")).evidence.lower() == "срочно"


def test_negated_urgency_is_not_surcharged():
    b = quote("Покраска фасада 500 м2, высота 12 м, г. Минск. Не срочно, работы по итогам тендера.")
    assert b.urgency_multiplier == 1.0
    assert b.total == 3250.0


def test_negotiated_work_request():
    b = quote("Просим КП на монтаж световых коробов на фасаде ТЦ, г. Минск, высота 12 м.")
    assert b.status == "manual_estimate"
    assert b.total is None
    assert "Монтаж объёмных букв, световых коробов, вывесок" in b.manual_reason


def test_cargo_heavier_than_500kg_request():
    b = quote("Нужен подъём груза 650 кг на 9 этаж, г. Минск.")
    assert b.status == "manual_estimate"
    assert b.total is None


def test_cargo_lift_request_within_tiers():
    b = quote("Подъём груза: 4 шт. по 250 кг на 9 этаж, г. Гродно.")
    assert b.status == "ok" and b.total == 1600.0


def test_dismantle_request():
    b = quote("Демонтаж баннера 200 м2, высота 20 м, г. Минск.")
    assert b.service_name.startswith("Демонтаж") and b.total == 720.0


def test_request_without_volume_needs_clarification():
    b = quote("Нужна мойка фасада, высота 20 м, г. Минск.")
    assert b.status == "needs_clarification"
    assert b.total is None
    assert b.clarify_fields == ["quantity"]


def test_demo_request_on_v03_prices():
    # 4200 × 1.50 × 1.2 (18 m); "Срочность: в течение месяца" is not urgency
    text = (ROOT / "data" / "demo_requests" / "facade_cleaning.txt").read_text(encoding="utf-8")
    b = quote(text)
    assert b.urgency_multiplier == 1.0
    assert b.total == 7560.0
