"""
Tender folder -> requirements JSON (with grounding) -> priced draft proposal.

    load_tender -> extract_tender (LLM)  -> ground -> price_tender -> .docx
                   or a ready JSON (e.g. gold markup, no LLM)

Outputs in out_dir: requirements.json (valid against
schemas/tender_requirements.schema.json, evidence.verified/match filled by
the grounding check), grounding.json, quote.json, proposal.docx.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from price_list import load_price_list
from tender.grounding import GroundingReport, ground
from tender.llm_extract import LLMCall, drop_resolved_gaps, extract_tender
from tender.loader import load_tender
from tender.models import TenderRequirements
from tender.pricing import TenderQuote, price_tender
from tender.proposal import generate_tender_proposal


@dataclass
class TenderResult:
    requirements: TenderRequirements
    grounding: GroundingReport
    quote: TenderQuote
    out_dir: Path


def load_requirements_json(path: Path) -> TenderRequirements:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    data.pop("_meta", None)
    return TenderRequirements.model_validate(data)


def run_tender(folder: Path, out_dir: Path, llm_call: Optional[LLMCall] = None,
               requirements_path: Optional[Path] = None) -> TenderResult:
    docs = load_tender(folder)
    if requirements_path:
        req = load_requirements_json(requirements_path)
        req.tender.source_docs = req.tender.source_docs or [d.name for d in docs]
        drop_resolved_gaps(req)
    else:
        req = extract_tender(docs, llm_call=llm_call)

    report = ground(req, docs)
    price_list = load_price_list()
    quote = price_tender(req, price_list)

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "requirements.json").write_text(
        json.dumps(req.to_json_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "grounding.json").write_text(
        json.dumps(report.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "quote.json").write_text(
        json.dumps(quote.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    generate_tender_proposal(req, quote, report, out_dir / "proposal.docx",
                             company=price_list["company"], proposal_number=f"TENDER-{Path(folder).name}")
    return TenderResult(req, report, quote, out_dir)


def summary(result: TenderResult) -> str:
    req, report, quote = result.requirements, result.grounding, result.quote
    unverified_reqs = sum(1 for r in req.requirements if r.evidence.verified is False)
    lines = [
        f"Требований: {len(req.requirements)} (не подтверждено цитатой: {unverified_reqs})",
        f"Строк объёмов: {len(req.scope)}, режим: {req.schedule.mode}",
        f"Цитаты: подтверждено {report.verified} из {report.total}",
    ]
    for issue in report.issues:
        lines.append(f"  {issue.status:<15} {issue.path}: «{issue.quote[:80]}» [{issue.doc}]")
    if quote.mode == "unit_prices":
        lines += [f"Цена за единицу: {u.service_name} — {u.price_per_unit:.2f} {quote.currency}/{u.unit}"
                  for u in quote.unit_prices]
    else:
        lines.append(f"Итого: {quote.total} {quote.currency}" if quote.total is not None else "Итого: ручной расчёт")
    lines += [f"Не рассчитано: {r}" for r in quote.not_priced]
    lines.append(f"Gaps ({len(req.gaps)}):")
    lines += [f"  [{g.issue}] {g.field}: {g.question_to_customer}" for g in req.gaps]
    lines.append(f"Flags ({len(req.flags)}, в КП только подтверждённые):")
    lines += [f"  {'✓' if f.evidence.verified else '✗ ' + str(f.evidence.match)} {f.text}" for f in req.flags]
    lines.append(f"-> {result.out_dir}")
    return "\n".join(lines)
