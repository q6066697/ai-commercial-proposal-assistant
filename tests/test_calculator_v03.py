"""
Calculator against the v0.3 client-calibrated price list (data/price_list.json).

Inputs are built directly as ExtractedRequest so these tests pin the pricing
rules alone, independent of how well the extractor reads free text (that is
covered end-to-end in test_pricing_e2e.py). Expected sums are worked out by
hand from the price list, see the comment on each test.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from calculator import calculate_price
from schema import ExtractedRequest


def req(text: str = "", location: str = "Минск", **fields) -> ExtractedRequest:
    return ExtractedRequest(raw_text=text, location=location, **fields)


def line(breakdown, label_start: str):
    return next(l for l in breakdown.lines if l.label.startswith(label_start))


# --- 1. minimum order ---------------------------------------------------

def test_below_minimum_is_raised_to_650_and_explained():
    # 200 m2 × 1.50 × 1.0 (10 m) = 300 < 650
    b = calculate_price(req(service="facade_washing", quantity=200, height_m=10))
    assert b.status == "ok"
    assert b.subtotal == 300.0
    assert b.minimum_applied
    assert b.total == 650.0
    minimum = line(b, "Минимальная сумма заказа")
    assert minimum.amount == 350.0
    assert "300,00" in minimum.formula and "650,00" in minimum.formula
    assert minimum.source == "pricing_rules.minimum_order" and minimum.source_type == "client"


def test_above_minimum_has_no_minimum_line():
    b = calculate_price(req(service="facade_washing", quantity=1000, height_m=10))
    assert not b.minimum_applied
    assert all(l.group != "minimum" for l in b.lines)


# --- 2. height ----------------------------------------------------------

def test_floor_is_converted_to_meters_and_priced_20_percent_higher():
    # 7 floor × 3 m = 21 m > 15 m -> ×1.2; 1000 × 1.50 × 1.2 = 1800
    b = calculate_price(req(service="facade_washing", quantity=1000, floor=7))
    assert b.height_coefficient == 1.2
    assert b.total == 1800.0
    height = line(b, "Коэффициент высоты")
    assert "7 этаж × 3 м = 21 м" in height.formula
    assert height.amount == 300.0
    assert height.source == "pricing_rules.height_coefficients" and height.source_type == "client"


@pytest.mark.parametrize("height_m, k", [(15, 1.0), (15.5, 1.2), (18, 1.2)])
def test_height_boundary_15m(height_m, k):
    b = calculate_price(req(service="facade_washing", quantity=1000, height_m=height_m))
    assert b.height_coefficient == k
    assert b.total == round(1000 * 1.5 * k, 2)


def test_missing_height_takes_1_2_and_asks_client():
    b = calculate_price(req(service="facade_washing", quantity=1000))
    assert b.height_coefficient == 1.2
    assert b.total == 1800.0
    assert "высота не указана — принят коэффициент 1,2" in line(b, "Коэффициент высоты").formula
    assert "height_m" in b.clarify_fields


def test_height_not_applied_to_cargo_lift_and_tree_removal():
    tree = calculate_price(req(service="tree_removal", quantity=10, height_m=30))
    assert tree.height_coefficient == 1.0
    assert all(not l.label.startswith("Коэффициент высоты") for l in tree.lines)
    assert tree.total == 750.0  # 10 × 75


def test_height_applied_to_ac_install():
    # 4 × 170 × 1.2 (24 m) = 816
    b = calculate_price(req(service="ac_install", quantity=4, height_m=24))
    assert b.height_coefficient == 1.2
    assert b.total == 816.0


# --- 3. urgency ---------------------------------------------------------

def test_urgent_request_adds_20_percent_to_works_not_materials():
    # works 500 × 4.00 = 2000, ×1.0 (12 m), +20% = 2400; materials 500 × 2.50 = 1250
    b = calculate_price(req(
        "Покраска фасада 500 м2, высота 12 м, просим выполнить срочно",
        service="facade_painting", quantity=500, height_m=12,
        urgency="urgent_within_week", urgency_evidence="срочно",
    ))
    assert b.works_total == 2400.0
    assert b.materials_total == 1250.0
    assert b.total == 3650.0
    urgency = line(b, "Срочность")
    assert urgency.amount == 400.0
    assert urgency.evidence == "срочно"
    assert urgency.source == "pricing_rules.urgency" and urgency.source_type == "client"


def test_no_urgency_surcharge_unless_requested():
    b = calculate_price(req(service="facade_painting", quantity=500, height_m=12))
    assert b.total == 3250.0
    assert all(not l.label.startswith("Срочность") for l in b.lines)


# --- 4. negotiated work / winter peak -----------------------------------

@pytest.mark.parametrize("text", [
    "Нужен монтаж световых коробов на фасаде ТЦ, высота 12 м",
    "Сварочные работы на высоте 20 м, металлоконструкции",
    "Покраска водонапорной башни",
])
def test_negotiated_work_has_no_price(text):
    b = calculate_price(req(text))
    assert b.status == "manual_estimate"
    assert b.total is None and b.lines == []
    assert "ручной расчёт" in b.warnings[0]


def test_dismantling_a_sign_is_not_confused_with_sign_installation():
    # "демонтаж вывески" must not hit the manual trigger "монтаж вывес"
    b = calculate_price(req("Демонтаж вывески, 1 шт., высота 9 м", service="sign_dismantle", quantity=1, height_m=9))
    assert b.status == "ok"
    assert b.total == 650.0  # 150 < 650


def test_legacy_service_without_v03_rate_is_manual():
    b = calculate_price(req(service="metal_structures_painting", quantity=900, height_m=25))
    assert b.status == "manual_estimate" and b.total is None


def test_snow_removal_in_snowfall_peak_is_manual():
    b = calculate_price(req("После снегопада завалило кровлю, нужна очистка 500 м2",
                            service="snow_removal", quantity=500, height_m=12))
    assert b.status == "manual_estimate" and b.total is None


def test_snow_removal_outside_peak_is_priced_with_warning():
    # 500 × 3.00 × 1.0 = 1500
    b = calculate_price(req("Очистка кровли от снега 500 м2", service="snow_removal", quantity=500, height_m=12))
    assert b.status == "ok" and b.total == 1500.0
    assert "В пик снегопадов цена кратно выше, уточняется отдельно." in b.warnings


# --- 5. cargo lift ------------------------------------------------------

def test_cargo_heavier_than_500kg_is_manual():
    b = calculate_price(req("Подъём груза 650 кг на 9 этаж", service="cargo_lift", quantity=1, weight_kg=650, floor=9))
    assert b.status == "manual_estimate" and b.total is None
    assert "650 кг" in b.manual_reason


def test_cargo_weight_tier_times_pieces():
    # 250 kg -> tier ≤300 kg = 400; 4 × 400 = 1600, no height coefficient
    b = calculate_price(req(service="cargo_lift", quantity=4, weight_kg=250, floor=9))
    assert b.total == 1600.0
    assert b.height_coefficient == 1.0
    works = b.lines[0]
    assert works.source == "services.cargo_lift.weight_tiers_kg"
    assert "до 300 кг" in works.formula


def test_light_cargo_hits_minimum():
    b = calculate_price(req(service="cargo_lift", quantity=1, weight_kg=40))
    assert b.subtotal == 180.0 and b.total == 650.0


def test_cargo_without_weight_needs_clarification():
    b = calculate_price(req(service="cargo_lift", quantity=1))
    assert b.status == "needs_clarification"
    assert "weight_kg" in b.clarify_fields


# --- 6. dismantle, materials, complex access ----------------------------

def test_dismantle_is_60_percent_of_mounting():
    # 5.00 × 0.6 = 3.00; 3.00 × 200 = 600; ×1.2 (20 m) = 720
    b = calculate_price(req(service="banner_install", quantity=200, height_m=20, is_dismantle=True))
    assert b.service_name.startswith("Демонтаж баннера")
    assert b.base_price == 3.0
    assert b.total == 720.0
    assert "pricing_rules.dismantle_factor" in b.lines[0].source


def test_materials_are_a_separate_line():
    # works 300 × 8.00 = 2400; materials 300 × 2.00 = 600
    b = calculate_price(req(service="joint_sealing", quantity=300, height_m=10))
    materials = line(b, "Материалы")
    assert materials.group == "materials" and materials.amount == 600.0
    assert b.works_total == 2400.0 and b.total == 3000.0


def test_service_without_materials_still_shows_materials_line():
    b = calculate_price(req(service="facade_washing", quantity=1000, height_m=10))
    assert line(b, "Материалы").amount == 0.0


def test_legacy_seam_sealing_id_maps_to_joint_sealing():
    b = calculate_price(req(service="seam_sealing", quantity=300, height_m=10))
    assert b.service_id == "joint_sealing" and b.total == 3000.0


def test_complex_access_trigger():
    # 1000 × 1.50 × 1.0 × 1.25 = 1875
    b = calculate_price(req("Мойка фасада 1000 м2 над проезжей частью", service="facade_washing",
                            quantity=1000, height_m=10))
    access = line(b, "Сложный доступ")
    assert access.evidence == "над проезжей частью"
    assert b.total == 1875.0


# --- 7. missing quantity ------------------------------------------------

def test_missing_quantity_needs_clarification_not_zero():
    b = calculate_price(req(service="facade_washing", height_m=10, location=None))
    assert b.status == "needs_clarification"
    assert b.total is None
    assert b.clarify_fields == ["quantity", "location"]


# --- 8. transparency ----------------------------------------------------

@pytest.mark.parametrize("fields", [
    dict(service="facade_washing", quantity=200, height_m=10),
    dict(service="facade_painting", quantity=500, height_m=12, urgency="urgent_within_week"),
    dict(service="facade_washing", quantity=1000, floor=7, text="над проезжей частью"),
    dict(service="banner_install", quantity=200, height_m=20, is_dismantle=True),
    dict(service="cargo_lift", quantity=4, weight_kg=250),
    dict(service="joint_sealing", quantity=300),
])
def test_lines_sum_to_total_and_every_line_is_signed(fields):
    b = calculate_price(req(**fields))
    assert b.status == "ok"
    assert round(sum(l.amount for l in b.lines), 2) == b.total
    for l in b.lines:
        assert l.source and l.source_type in {"client", "market", "assumption", "market/market"}
        assert l.formula
