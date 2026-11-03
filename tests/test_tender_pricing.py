"""
Tender pricing against the v0.3 price list: repeats, one minimum order for
the whole tender, unit prices for framework contracts (on_request), and
lines that are listed instead of priced.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from tender.models import TenderRequirements
from tender.pricing import CURRENCY_NOTE, price_tender
from tender_fixtures import ev, sample


def line(service_id="window_washing", volume=1000.0, repeats=1, **extra):
    return {"service_id": service_id, "description": extra.pop("description", "Мойка остекления"),
            "surface": None, "side": "outer", "building": None, "volume": volume,
            "unit": extra.pop("unit", "m2"), "repeats": repeats, "period": extra.pop("period", None),
            "evidence": ev("Общая площадь фасада здания составляет 10740,4 кв.м."), **extra}


def tender(scope, height_m=10.0, mode="fixed_dates", country="RU"):
    data = sample(scope=scope, height={"max_height_m": height_m, "floors": None})
    data["schedule"]["mode"] = mode
    data["tender"]["country"] = country
    return TenderRequirements.model_validate(data)


def test_fixed_dates_tender_without_height():
    # 10740.4 × 1.50 = 16110.60; ×1.2 (height unknown) -> +3222.12 = 19332.72
    q = price_tender(tender([line("facade_washing", 10740.4)], height_m=None))
    assert q.mode == "total"
    assert q.works_total == 19332.72 and q.total == 19332.72
    height = next(l for l in q.lines if l.label == "Коэффициент высоты")
    assert height.amount == 3222.12 and "высота не указана" in height.formula
    assert any("Высота в документации не указана" in w for w in q.warnings)
    assert CURRENCY_NOTE in q.notes


def test_no_currency_note_for_belarus():
    q = price_tender(tender([line()], country="BY"))
    assert CURRENCY_NOTE not in q.notes


def test_repeats_multiply_the_line():
    # 1.50 × 1000 × 2 = 3000, height 10 m -> ×1.0
    q = price_tender(tender([line(repeats=2)]))
    assert q.lines[0].amount == 3000.0
    assert "× 2 раза" in q.lines[0].formula
    assert q.total == 3000.0


def test_seasons_are_separate_lines():
    # spring 16313 + autumn 10689 = 27002 m² × 1.50 × 1.2 (60 m)
    q = price_tender(tender([line(volume=16313, period="весна"), line(volume=10689, period="осень")], height_m=60))
    assert [l.label for l in q.lines[:2]] == ["Мойка окон и остекления (снаружи, весна)",
                                             "Мойка окон и остекления (снаружи, осень)"]
    assert q.total == round(27002 * 1.5 * 1.2, 2)


def test_minimum_order_applies_once_to_the_tender_total():
    # 100 × 1.50 + 100 × 1.50 = 300 < 650 -> one minimum line
    q = price_tender(tender([line(volume=100), line(volume=100, period="осень")]))
    assert q.subtotal == 300.0 and q.total == 650.0
    assert [l.group for l in q.lines].count("minimum") == 1


def test_materials_are_separate_and_lines_sum_to_total():
    # joint sealing 300 lm: works 2400 + materials 600
    q = price_tender(tender([line("joint_sealing", 300, unit="lm", description="Герметизация швов")]))
    assert q.works_total == 2400.0 and q.materials_total == 600.0 and q.total == 3000.0
    assert round(sum(l.amount for l in q.lines), 2) == q.total


def test_lines_that_cannot_be_priced_are_listed_not_guessed():
    unverified = line(volume=500)
    unverified["evidence"]["verified"] = False
    unverified["evidence"]["match"] = "not_found"
    scope = [
        line(volume=1000),
        unverified,
        line(None, 50, description="Мойка водосточных воронок"),
        line("window_washing", None, description="Мойка внутреннего остекления"),
        line("facade_washing", 20, description="Монтаж световых коробов", unit="pcs"),
    ]
    q = price_tender(tender(scope))
    assert q.total == 1500.0
    assert len(q.not_priced) == 4
    assert any("не подтверждена" in r for r in q.not_priced)
    assert any("нет в прайсе" in r for r in q.not_priced)
    assert any("объём не указан" in r for r in q.not_priced)
    assert any("договорная работа" in r for r in q.not_priced)
    assert any("Итог не включает" in w for w in q.warnings)


def test_on_request_gives_unit_prices_not_a_total():
    # window washing: 1.50 × 1.2 (height unknown) = 1.80 per m²
    q = price_tender(tender([line(volume=1171.2), line(volume=6650.0, building="Строение 2")],
                            height_m=None, mode="on_request"))
    assert q.mode == "unit_prices" and q.total is None and q.lines == []
    [unit] = q.unit_prices
    assert unit.price_per_unit == 1.8
    assert unit.reference_volume == 7821.2
    assert any("Минимальная сумма одной заявки — 650,00 BYN" in n for n in q.notes)


def test_on_request_unit_price_includes_materials():
    # facade painting: 4.00 × 1.0 (10 m) + materials 2.50 = 6.50 per m²
    q = price_tender(tender([line("facade_painting", 500)], mode="on_request"))
    assert q.unit_prices[0].price_per_unit == 6.5
    assert "материалы 2,50" in q.unit_prices[0].formula
