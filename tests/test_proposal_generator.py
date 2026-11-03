"""
The proposal must show how the total was built (one table row per price
line, signed with its rule) and must never print a number for work that
needs a site inspection.
"""
import sys
from pathlib import Path

from docx import Document

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from calculator import calculate_price
from extractor import extract_rule_based
from proposal_generator import generate_proposal_docx


def render(text: str, tmp_path: Path) -> str:
    extracted = extract_rule_based(text)
    path = generate_proposal_docx(extracted, calculate_price(extracted), tmp_path / "kp.docx")
    doc = Document(path)
    parts = [p.text for p in doc.paragraphs]
    for table in doc.tables:
        for row in table.rows:
            parts.append(" | ".join(c.text for c in row.cells))
    return "\n".join(parts)


def test_urgent_quote_shows_every_line_with_its_source(tmp_path):
    text = render("Покраска фасада 500 м2, высота 12 м, г. Минск. Просим выполнить срочно.", tmp_path)
    assert "pricing_rules.urgency (подтверждено клиентом)" in text
    assert "services.facade_painting.material_rate (рынок)" in text
    assert "Итого работы |  | 2 400,00" in text
    assert "ИТОГО: 3 650,00 BYN" in text
    assert "ООО «Высотник-Демо»" in text


def test_minimum_order_is_explained(tmp_path):
    text = render("Мойка фасада 200 м2, высота 10 м, г. Минск.", tmp_path)
    assert "ИТОГО: 650,00 BYN" in text
    assert "ниже минимальной суммы заказа" in text


def test_negotiated_work_has_no_total(tmp_path):
    text = render("Просим КП на монтаж световых коробов на фасаде ТЦ, г. Минск.", tmp_path)
    assert "требуется осмотр объекта / ручной расчёт" in text
    assert "ИТОГО" not in text
