"""
Request and response bodies of the API service.

Money (total) is Decimal and goes out as a JSON string ("7560.00"), so a
client never gets it as an inexact float.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any, Literal, Optional

from pydantic import AfterValidator, BaseModel, ConfigDict, Field


def _storable(value: str) -> str:
    """JSON allows characters PostgreSQL text can't hold."""
    if "\x00" in value:
        raise ValueError("символ NUL недопустим")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        raise ValueError("некорректный Unicode") from None
    return value


def _not_blank(value: str) -> str:
    if not value.strip():
        raise ValueError("текст заявки пуст")
    return value


class RequestCreate(BaseModel):
    # Stored as received, not stripped: pricing triggers look at this text
    text: Annotated[str, Field(min_length=1, max_length=20_000), AfterValidator(_storable), AfterValidator(_not_blank)]
    client_name: Annotated[Optional[str], Field(max_length=200), AfterValidator(lambda v: v and _storable(v))] = None


class RequestItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    position: int
    service_code: Optional[str]
    quantity: Optional[float]
    unit: Optional[str]
    height_m: Optional[float]
    floor: Optional[int]
    weight_kg: Optional[float]
    is_dismantle: bool


class RequestOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    client_name: Optional[str]
    source_text: str
    status: Literal["extracted", "quoted", "manual_estimate", "needs_clarification"]
    location: Optional[str]
    urgency: Literal["standard", "urgent_within_week"]
    urgency_evidence: Optional[str]
    items: list[RequestItemOut]
    created_at: datetime
    updated_at: datetime


class QuoteOut(BaseModel):
    id: uuid.UUID
    request_id: uuid.UUID
    status: Literal["ok", "manual_estimate", "needs_clarification"]
    total: Optional[Decimal]  # null: manual estimate or clarification needed
    currency: str
    price_list_version: str
    breakdown: dict[str, Any]
    download_url: str
    created_at: datetime


class HealthOut(BaseModel):
    status: Literal["ok", "error"]
    database: Literal["ok", "unavailable"]
