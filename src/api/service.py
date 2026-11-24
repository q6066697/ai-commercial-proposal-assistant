"""
What the endpoints do, without HTTP. Extraction, pricing and the document come
from the same modules the CLI and Streamlit use (extractor, calculator,
proposal_generator); this file only stores their inputs and results.

No LLM here: extraction is rule-based, the price is deterministic code.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from calculator import FIELD_LABELS, PriceBreakdown, PriceLine, calculate_price
from db.models import PriceListVersion, Quote, Request, RequestItem
from db.numbers import to_decimal, to_float, to_money
from db.price_lists import get_active_version, to_price_list
from extractor import extract_rule_based
from proposal_generator import generate_proposal_docx
from schema import ExtractedRequest

# Part of input_hash. Increase when the calculation itself changes (code, not
# prices), otherwise requests quoted earlier keep returning the old result.
CALCULATION_VERSION = 1

DOCX_NAME = re.compile(r"[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}\.docx")
REQUEST_STATUS = {"ok": "quoted", "manual_estimate": "manual_estimate", "needs_clarification": "needs_clarification"}


class NotFound(LookupError):
    pass


class ExtractionFailed(ValueError):
    """The text contains a value the extractor can't accept."""


class CannotQuote(RuntimeError):
    """The request can't be priced by this service at all."""


class ServiceNotDetected(RuntimeError):
    """No document is made, same as in the CLI."""

    def __init__(self, clarify_fields: list[str], warnings: list[str]):
        super().__init__("Не удалось определить вид работ: уточните заявку.")
        self.clarify_fields = clarify_fields
        self.warnings = warnings


# --- Requests ---------------------------------------------------------------

def extract(text: str) -> ExtractedRequest:
    try:
        extracted = extract_rule_based(text)
    except ValidationError as e:
        fields = [str(err["loc"][0]) for err in e.errors() if err["loc"]]
        labels = ", ".join(FIELD_LABELS.get(f, f) for f in fields)
        raise ExtractionFailed(f"Недопустимое значение в заявке: {labels}.") from None
    for field in ("quantity", "height_m", "weight_kg"):
        value = getattr(extracted, field)
        if value is not None and not math.isfinite(value):
            raise ExtractionFailed(f"Недопустимое значение в заявке: {FIELD_LABELS[field]}.")
    return extracted


async def create_request(session: AsyncSession, text: str, client_name: Optional[str]) -> Request:
    extracted = await asyncio.to_thread(extract, text)
    request = Request(
        client_name=client_name,
        source_text=text,
        location=extracted.location,
        urgency=extracted.urgency,
        urgency_evidence=extracted.urgency_evidence,
        # The extractor finds one service per request for now
        items=[RequestItem(
            position=1,
            service_code=extracted.service,
            quantity=to_decimal(extracted.quantity),
            unit=extracted.unit,
            height_m=to_decimal(extracted.height_m),
            floor=extracted.floor,
            weight_kg=to_decimal(extracted.weight_kg),
            is_dismantle=extracted.is_dismantle,
        )],
    )
    session.add(request)
    await session.commit()
    return request


async def get_request(session: AsyncSession, request_id: uuid.UUID, lock: bool = False) -> Request:
    query = select(Request).where(Request.id == request_id).options(selectinload(Request.items))
    request = await session.scalar(query.with_for_update() if lock else query)
    if request is None:
        raise NotFound("Заявка не найдена.")
    return request


def to_extracted(request: Request) -> ExtractedRequest:
    """The stored request as the calculator's input."""
    if len(request.items) != 1:
        # Never price one position and silently drop the others
        raise CannotQuote(f"Расчёт поддерживает одну позицию, в заявке их {len(request.items)}.")
    item = request.items[0]
    return ExtractedRequest(
        service=item.service_code,
        quantity=to_float(item.quantity),
        unit=item.unit,
        height_m=to_float(item.height_m),
        location=request.location,
        urgency=request.urgency,
        raw_text=request.source_text,
        floor=item.floor,
        weight_kg=to_float(item.weight_kg),
        is_dismantle=item.is_dismantle,
        urgency_evidence=request.urgency_evidence,
    )


# --- Quotes -----------------------------------------------------------------

def input_hash(extracted: ExtractedRequest) -> str:
    """Everything the calculation and the document depend on, except prices
    (those are identified by the price list version)."""
    payload = {"calculation": CALCULATION_VERSION, "request": extracted.to_dict()}
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


async def create_quote(session: AsyncSession, request_id: uuid.UUID,
                       quotes_dir: Path) -> tuple[Quote, PriceListVersion, bool]:
    """(quote, its price list version, created?). The same request on the same
    prices returns the existing quote. The request row is locked, so parallel
    calls wait here and then find the quote made by the first one."""
    request = await get_request(session, request_id, lock=True)
    extracted = to_extracted(request)
    version = await get_active_version(session)
    digest = input_hash(extracted)

    existing = await session.scalar(select(Quote).where(
        Quote.request_id == request.id, Quote.price_list_version_id == version.id, Quote.input_hash == digest))
    if existing is not None:
        request.status = REQUEST_STATUS[existing.status]  # may differ after a switch of price lists
        await ensure_docx(existing, extracted, version, quotes_dir)
        await session.commit()
        return existing, version, False

    try:
        breakdown = calculate_price(extracted, to_price_list(version))
    except ValueError:
        raise CannotQuote(f"Услуги «{extracted.service}» нет в прайсе «{version.version}».") from None

    request.status = REQUEST_STATUS[breakdown.status]
    if breakdown.status == "needs_clarification" and "service" in breakdown.clarify_fields:
        await session.commit()
        raise ServiceNotDetected(breakdown.clarify_fields, breakdown.warnings)

    quote_id = uuid.uuid4()
    quote = Quote(
        id=quote_id,
        request_id=request.id,
        price_list_version_id=version.id,
        input_hash=digest,
        status=breakdown.status,
        total=to_money(breakdown.total),
        currency=breakdown.currency,
        breakdown=breakdown.to_dict(),
        docx_path=f"{quote_id}.docx",
        created_at=datetime.now(timezone.utc),
    )
    session.add(quote)
    await session.flush()
    path = await ensure_docx(quote, extracted, version, quotes_dir)
    try:
        await session.commit()
    except BaseException:
        path.unlink(missing_ok=True)  # no document without its quote
        raise
    return quote, version, True


async def get_quote_docx(session: AsyncSession, quote_id: uuid.UUID, quotes_dir: Path) -> Path:
    quote = await session.get(Quote, quote_id)
    if quote is None:
        raise NotFound("Расчёт не найден.")
    request = await get_request(session, quote.request_id)
    version = await session.get(PriceListVersion, quote.price_list_version_id)
    return await ensure_docx(quote, to_extracted(request), version, quotes_dir)


# --- Document ---------------------------------------------------------------

def breakdown_from_dict(data: dict, company: Optional[str]) -> PriceBreakdown:
    """Inverse of PriceBreakdown.to_dict() (which leaves the company out)."""
    fields = {**data, "lines": [PriceLine(**line) for line in data["lines"]]}
    return PriceBreakdown(**fields, company=company)


def docx_file(quote: Quote, quotes_dir: Path) -> Path:
    """Only the names this service generates, so the path can't leave the
    folder. Not Path.resolve(): on Windows it gives different prefixes for the
    folder and the file while another thread is creating the folder."""
    if not DOCX_NAME.fullmatch(quote.docx_path):
        raise NotFound("Файл КП не найден.")
    return quotes_dir / quote.docx_path


async def ensure_docx(quote: Quote, extracted: ExtractedRequest, version: PriceListVersion, quotes_dir: Path) -> Path:
    """Renders the stored calculation if the file is not on disk. Nothing is
    recalculated: a lost file comes back with the same numbers and date."""
    path = docx_file(quote, quotes_dir)
    if not path.exists():
        await asyncio.to_thread(_render, quote, extracted, version.company, path)
    return path


def _render(quote: Quote, extracted: ExtractedRequest, company: Optional[str], path: Path) -> None:
    temporary = path.with_name(f"{path.stem}.{uuid.uuid4().hex}.tmp")
    try:
        generate_proposal_docx(
            extracted, breakdown_from_dict(quote.breakdown, company), temporary,
            proposal_number=f"API-{str(quote.id)[:8].upper()}",
            proposal_date=quote.created_at.astimezone().date(),
        )
        try:
            os.replace(temporary, path)  # a reader never sees a half-written file
        except PermissionError:
            # Windows: another call has just created the file and it is being read
            if not path.exists():
                raise
    finally:
        temporary.unlink(missing_ok=True)
