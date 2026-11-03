"""
Minimal tests demonstrating the QA/eval mindset behind this project: every
extraction + calculation path used in the demo is checked against expected
output, not just "does it run".
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from extractor import extract_rule_based
from calculator import calculate_price
from price_list import LEGACY_PRICE_LIST_PATH, load_price_list


def test_facade_cleaning_extraction():
    text = (Path(__file__).resolve().parent.parent / "data" / "demo_requests" / "facade_cleaning.txt").read_text(encoding="utf-8")
    result = extract_rule_based(text)
    assert result.service == "facade_cleaning"
    assert result.quantity == 4200
    assert result.height_m == 18
    assert result.location == "Минск"


def test_facade_cleaning_price():
    text = (Path(__file__).resolve().parent.parent / "data" / "demo_requests" / "facade_cleaning.txt").read_text(encoding="utf-8")
    extracted = extract_rule_based(text)
    # Pinned to the v0.1 demo price list: this number is tied to its rates.
    breakdown = calculate_price(extracted, price_list=load_price_list(LEGACY_PRICE_LIST_PATH))
    # 4200 * 3.50 * 1.2 (height 10-20m tier) * 1.0 (standard urgency)
    assert round(breakdown.total, 2) == 17640.0
    assert breakdown.warnings == []


def test_missing_height_flags_warning():
    text = "Услуга: мойка фасада. Площадь: 500 м2. г. Минск."
    extracted = extract_rule_based(text)
    breakdown = calculate_price(extracted)
    assert any("Высота не указана" in w for w in breakdown.warnings)


def test_unknown_service_returns_none():
    text = "Просим прислать КП на озеленение территории."
    extracted = extract_rule_based(text)
    assert extracted.service is None


if __name__ == "__main__":
    import subprocess
    subprocess.run(["python", "-m", "pytest", str(Path(__file__)), "-v"])
