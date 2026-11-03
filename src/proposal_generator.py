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
from calculator import FIELD_LABELS, UNIT_LABELS, PriceBreakdown

# Fallback when the price list has no meta.company (the legacy demo list).
COMPANY_NAME = "DemoAlp Services LLC"  # synthetic demo company, see data/knowledge/company_profile.md
SOURCE_LABELS = {"client": "подтверждено клиентом", "market": "рынок", "assumption": "допущение", "demo": "демо-прайс"}


def _money(x: float) -> str:
    return f"{x:,.2f}".replace(",", " ").replace(".", ",")


def _source_label(source: str, source_type: str) -> str:
    kinds = ", ".join(SOURCE_LABELS.get(t, t) for t in dict.fromkeys(source_type.split("/")))
    return f"{source} ({kinds})"


def _add_cost_table(doc: Document, breakdown: PriceBreakdown) -> None:
    table = doc.add_table(rows=1, cols=4)
    table.style = "Light Grid Accent 1"
    for cell, (text, width) in zip(table.rows[0].cells, [("Статья", 4), ("Расчёт", 6), (f"Сумма, {breakdown.currency}", 2.5), ("Основание", 4.5)]):
        cell.text = text
        cell.width = Cm(width)

    def add_row(label: str, formula: str, amount: str, source: str, bold: bool = False) -> None:
        cells = table.add_row().cells
        for cell, text in zip(cells, (label, formula, amount, source)):
            cell.text = text
            for run in cell.paragraphs[0].runs:
                run.font.size = Pt(9)
                run.bold = bold

    def add_lines(group: str) -> None:
        for line in breakdown.lines:
            if line.group != group:
                continue
            formula = line.formula + (f"; в заявке: «{line.evidence}»" if line.evidence else "")
            add_row(line.label, formula, _money(line.amount), _source_label(line.source, line.source_type))

    add_lines("works")
    add_row("Итого работы", "", _money(breakdown.works_total), "", bold=True)
    add_lines("materials")
    add_lines("minimum")


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
    doc.add_paragraph(f"Исполнитель: {breakdown.company or COMPANY_NAME}")
    if extracted.location:
        doc.add_paragraph(f"Объект: г. {extracted.location}")

    doc.add_heading("Предмет предложения", level=2)
    subject = breakdown.service_name
    if breakdown.quantity:
        subject += f", объём {breakdown.quantity:g} {UNIT_LABELS.get(breakdown.unit, breakdown.unit)}"
    if extracted.height_m:
        subject += f", высота объекта до {extracted.height_m:g} м"
    elif extracted.floor:
        subject += f", {extracted.floor} этаж"
    doc.add_paragraph(subject + ".")

    doc.add_heading("Расчёт стоимости", level=2)
    if breakdown.status == "manual_estimate":
        total_p = doc.add_paragraph()
        total_run = total_p.add_run("Стоимость: требуется осмотр объекта / ручной расчёт.")
        doc.add_paragraph(f"Причина: {breakdown.manual_reason}.")
    elif breakdown.status == "needs_clarification":
        missing = ", ".join(FIELD_LABELS.get(f, f) for f in breakdown.clarify_fields)
        total_p = doc.add_paragraph()
        total_run = total_p.add_run(f"Стоимость будет рассчитана после уточнения данных: {missing}.")
    else:
        _add_cost_table(doc, breakdown)
        total_p = doc.add_paragraph()
        total_run = total_p.add_run(f"ИТОГО: {_money(breakdown.total)} {breakdown.currency}")
        if breakdown.minimum_applied:
            doc.add_paragraph(
                f"Расчётная стоимость работ и материалов ({_money(breakdown.subtotal)} {breakdown.currency}) "
                f"ниже минимальной суммы заказа, поэтому к оплате принимается минимальная сумма "
                f"{_money(breakdown.total)} {breakdown.currency}."
            )
    total_run.bold = True
    total_run.font.size = Pt(13)

    if breakdown.clarify_fields and breakdown.status == "ok":
        doc.add_heading("Уточнить у клиента", level=2)
        for f in breakdown.clarify_fields:
            doc.add_paragraph(FIELD_LABELS.get(f, f), style="List Bullet")

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
