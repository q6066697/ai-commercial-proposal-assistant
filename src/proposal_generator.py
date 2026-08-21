"""
Generates a commercial proposal (.docx) from extracted request data + a price
breakdown. This is the "human-in-the-loop" boundary of the pipeline: the
output here is a DRAFT for a human to review, edit, and approve — not
something auto-sent to a customer. See README.md for the full flow.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

from docx import Document
from docx.shared import Pt, Cm
from docx.enum.text import WD_ALIGN_PARAGRAPH

from extractor import ExtractedRequest
from calculator import PriceBreakdown

COMPANY_NAME = "DemoAlp Services LLC"  # synthetic demo company, see data/knowledge/company_profile.md


def generate_proposal_docx(
    extracted: ExtractedRequest,
    breakdown: PriceBreakdown,
    output_path: Path,
    proposal_number: str = "DEMO-0001",
) -> Path:
    doc = Document()

    style = doc.styles["Normal"]
    style.font.name = "Calibri"
    style.font.size = Pt(11)

    title = doc.add_paragraph()
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = title.add_run("КОММЕРЧЕСКОЕ ПРЕДЛОЖЕНИЕ")
    run.bold = True
    run.font.size = Pt(16)

    meta = doc.add_paragraph()
    meta.alignment = WD_ALIGN_PARAGRAPH.CENTER
    meta.add_run(f"№ {proposal_number} от {date.today().strftime('%d.%m.%Y')}")

    doc.add_paragraph()
    doc.add_paragraph(f"Исполнитель: {COMPANY_NAME}")
    if extracted.location:
        doc.add_paragraph(f"Объект: г. {extracted.location}")

    doc.add_heading("Предмет предложения", level=2)
    doc.add_paragraph(
        f"{breakdown.service_name}, объём {breakdown.quantity:g} {breakdown.unit}"
        + (f", высота объекта до {extracted.height_m:g} м." if extracted.height_m else ".")
    )

    doc.add_heading("Расчёт стоимости", level=2)
    table = doc.add_table(rows=1, cols=2)
    table.style = "Light Grid Accent 1"
    table.columns[0].width = Cm(9)
    table.columns[1].width = Cm(5)
    hdr = table.rows[0].cells
    hdr[0].text = "Параметр"
    hdr[1].text = "Значение"

    rows_data = [
        ("Услуга", breakdown.service_name),
        ("Объём работ", f"{breakdown.quantity:g} {breakdown.unit}"),
        ("Базовая цена за единицу", f"{breakdown.base_price:.2f} {breakdown.currency}"),
        ("Коэффициент высоты", f"×{breakdown.height_coefficient:g}"),
        ("Коэффициент срочности", f"×{breakdown.urgency_multiplier:g}"),
        ("Минимальная сумма заказа применена", "да" if breakdown.minimum_applied else "нет"),
    ]
    for label, value in rows_data:
        row = table.add_row().cells
        row[0].text = label
        row[1].text = str(value)

    doc.add_paragraph()
    total_p = doc.add_paragraph()
    total_run = total_p.add_run(f"ИТОГО: {breakdown.total:.2f} {breakdown.currency}")
    total_run.bold = True
    total_run.font.size = Pt(13)

    if breakdown.warnings:
        doc.add_heading("⚠ Требует проверки перед отправкой клиенту", level=2)
        for w in breakdown.warnings:
            doc.add_paragraph(w, style="List Bullet")

    doc.add_paragraph()
    footer = doc.add_paragraph(
        "Данный документ сформирован автоматически на основе синтетических демо-данных "
        "и предназначен для демонстрации возможностей системы. Все цены и реквизиты вымышлены."
    )
    footer.runs[0].italic = True
    footer.runs[0].font.size = Pt(8)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(output_path)
    return output_path
