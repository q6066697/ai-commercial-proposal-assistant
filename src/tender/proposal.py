"""
Draft proposal (.docx) for a tender: the price (line by line, or unit prices
for a framework contract), the contractor's requirements checklist with the
source of each item, flags, and questions to the customer. Like the
single-request proposal, it is a draft for human review, never auto-sent.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Optional

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Pt

from proposal_generator import COMPANY_NAME, _money, add_cost_table, add_table
from tender.grounding import GroundingReport
from tender.models import TenderRequirements
from tender.pricing import TenderQuote

CATEGORY_LABELS = {
    "license": "лицензия", "experience": "опыт", "personnel_admission": "допуски персонала",
    "medical": "медосмотр", "equipment": "оборудование", "ppe": "СИЗ", "insurance": "страхование",
    "site_visit": "осмотр объекта", "safety": "безопасность", "materials": "материалы",
    "schedule": "сроки", "documents": "документы", "confidentiality": "конфиденциальность", "other": "прочее",
}
COST_LABELS = {"none": "нет", "low": "низкое", "high": "высокое", None: "—"}
MODE_LABELS = {"fixed_dates": "в фиксированные даты", "on_request": "по заявкам заказчика (рамочный договор)",
               "periodic": "периодически по графику"}


def _source(evidence) -> str:
    mark = {True: "✓", False: "⚠ не подтверждено"}.get(evidence.verified, "")
    return f"{evidence.doc} {mark}".strip()


def generate_tender_proposal(req: TenderRequirements, quote: TenderQuote, report: Optional[GroundingReport],
                             output_path: Path, company: Optional[str] = None,
                             proposal_number: str = "TENDER-DRAFT") -> Path:
    doc = Document()
    doc.styles["Normal"].font.name = "Calibri"
    doc.styles["Normal"].font.size = Pt(11)

    title = doc.add_paragraph()
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = title.add_run("КОММЕРЧЕСКОЕ ПРЕДЛОЖЕНИЕ")
    run.bold = True
    run.font.size = Pt(16)
    meta = doc.add_paragraph(f"№ {proposal_number} от {date.today().strftime('%d.%m.%Y')}")
    meta.alignment = WD_ALIGN_PARAGRAPH.CENTER

    doc.add_paragraph(f"Исполнитель: {company or COMPANY_NAME}")
    if req.tender.customer.value:
        doc.add_paragraph(f"Заказчик: {req.tender.customer.value}")
    if req.tender.location and req.tender.location.value:
        doc.add_paragraph(f"Объект: {req.tender.location.value}")

    doc.add_heading("Предмет предложения", level=2)
    doc.add_paragraph(req.tender.subject.value or "—")
    schedule = req.schedule
    terms = [f"Порядок выполнения: {MODE_LABELS[schedule.mode]}"]
    if schedule.start or schedule.end:
        terms.append(f"Сроки: {schedule.start or '?'} — {schedule.end or '?'}")
    if schedule.response_time_days:
        terms.append(f"Срок исполнения заявки: {schedule.response_time_days} дн.")
    if schedule.contract_term:
        terms.append(f"Срок договора: {schedule.contract_term}")
    for t in terms:
        doc.add_paragraph(t, style="List Bullet")

    doc.add_heading("Расчёт стоимости", level=2)
    if quote.mode == "unit_prices":
        add_table(doc, [("Услуга", 5), ("Расчёт", 6.5), (f"Цена, {quote.currency}", 2.5), ("Объём по документации", 3)],
                  [(u.service_name, u.formula, f"{_money(u.price_per_unit)} / {u.unit}",
                    f"{u.reference_volume:g} {u.unit} (справочно)") for u in quote.unit_prices])
    elif quote.total is not None:
        add_cost_table(doc, quote.lines, quote.currency, quote.works_total, evidence_label="в документации")
        total = doc.add_paragraph().add_run(f"ИТОГО: {_money(quote.total)} {quote.currency}")
        total.bold = True
        total.font.size = Pt(13)
        if quote.minimum_applied:
            doc.add_paragraph(f"Расчётная стоимость ({_money(quote.subtotal)} {quote.currency}) ниже минимальной "
                              f"суммы заказа, к оплате принимается {_money(quote.total)} {quote.currency}.")
    else:
        doc.add_paragraph("Стоимость не рассчитана автоматически — требуется ручной расчёт.").runs[0].bold = True
    for note in quote.notes:
        doc.add_paragraph(note, style="List Bullet")
    if quote.not_priced:
        doc.add_paragraph("Не рассчитано автоматически:").runs[0].bold = True
        for reason in quote.not_priced:
            doc.add_paragraph(reason, style="List Bullet")

    # A flag is shown only with a confirmed quote: an unfounded "full
    # liability" or "mandatory site visit" would mislead the reviewer.
    flags = [f for f in req.flags if f.evidence.verified is True]
    if flags:
        doc.add_heading("Важно для исполнителя", level=2)
        for flag in flags:
            doc.add_paragraph(f"{flag.text} ({flag.evidence.doc}: «{flag.evidence.quote}»)", style="List Bullet")

    doc.add_heading("Требования к исполнителю", level=2)
    add_table(doc, [("Требование", 7), ("Категория", 3), ("Обязат.", 1.5), ("Влияние на цену", 2), ("Источник", 3.5)],
              [(r.text, CATEGORY_LABELS[r.category], "да" if r.mandatory else "нет",
                COST_LABELS[r.cost_impact], _source(r.evidence)) for r in req.requirements])

    if req.gaps:
        doc.add_heading("Вопросы заказчику", level=2)
        for gap in req.gaps:
            doc.add_paragraph(gap.question_to_customer, style="List Number")

    review = list(quote.warnings)
    if report and report.issues:
        review.append(f"Цитаты: подтверждено {report.verified} из {report.total}. "
                      f"Неподтверждённые факты помечены «⚠» и не участвуют в расчёте.")
    if review:
        doc.add_heading("⚠ Требует проверки перед отправкой", level=2)
        for w in review:
            doc.add_paragraph(w, style="List Bullet")

    footer = doc.add_paragraph("Черновик сформирован автоматически по тендерной документации и требует проверки. "
                               "Цены — по демонстрационному прайсу.")
    footer.runs[0].italic = True
    footer.runs[0].font.size = Pt(8)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(output_path)
    return output_path
