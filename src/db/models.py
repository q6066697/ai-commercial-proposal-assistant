"""
Tables of the API service.

- price_list_versions / price_list: versioned copy of data/price_list.json
  (the JSON stays the master, see db/price_lists.py). Imported versions are
  never edited: a quote always points at the prices it was made with.
- requests / request_items: what came in and what was extracted from it.
- quotes: calculation snapshot + path to the DOCX.

Money is NUMERIC. Statuses are text + CHECK rather than PostgreSQL enums, so
adding one is a simple migration.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import (CheckConstraint, DateTime, ForeignKey, Identity, Index, MetaData, Numeric, String, Text,
                        UniqueConstraint, false, func, text)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    # Explicit names: migrations can refer to every constraint
    metadata = MetaData(naming_convention={
        "ix": "ix_%(column_0_label)s",
        "uq": "uq_%(table_name)s_%(column_0_N_name)s",
        "ck": "ck_%(table_name)s_%(constraint_name)s",
        "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
        "pk": "pk_%(table_name)s",
    })
    type_annotation_map = {
        datetime: DateTime(timezone=True),
        str: Text,
        dict[str, Any]: JSONB,
        list[Any]: JSONB,
    }
    # Read server-side values (updated_at) back in the same statement instead
    # of an implicit SELECT later, which fails under asyncio
    __mapper_args__ = {"eager_defaults": True}


class PriceListVersion(Base):
    __tablename__ = "price_list_versions"
    __table_args__ = (
        # At most one active version
        Index("uq_price_list_versions_active", "is_active", unique=True, postgresql_where=text("is_active")),
    )

    id: Mapped[int] = mapped_column(Identity(), primary_key=True)
    version: Mapped[str] = mapped_column(unique=True)       # meta.version of the JSON
    content_hash: Mapped[str] = mapped_column(String(64), unique=True)
    currency: Mapped[str] = mapped_column(String(3))
    company: Mapped[Optional[str]]
    rules: Mapped[dict[str, Any]]                           # pricing_rules, as in the JSON
    manual_items: Mapped[list[Any]]                         # manual_estimate_only.items
    is_active: Mapped[bool] = mapped_column(server_default=false())
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    items: Mapped[list[PriceListItem]] = relationship(
        back_populates="price_list_version", order_by="PriceListItem.id", lazy="raise",
        cascade="all, delete-orphan")


class PriceListItem(Base):
    __tablename__ = "price_list"
    __table_args__ = (
        UniqueConstraint("version_id", "code"),
        CheckConstraint("rate >= 0", name="rate_not_negative"),
        CheckConstraint("material_rate >= 0", name="material_rate_not_negative"),
    )

    id: Mapped[int] = mapped_column(Identity(), primary_key=True)
    version_id: Mapped[int] = mapped_column(ForeignKey("price_list_versions.id", ondelete="CASCADE"))
    code: Mapped[str]
    name: Mapped[str]
    unit: Mapped[str]
    category: Mapped[Optional[str]]
    source: Mapped[Optional[str]]
    # No scale limit: PostgreSQL would silently round a rate that doesn't fit
    rate: Mapped[Optional[Decimal]] = mapped_column(Numeric)           # NULL = weight tiers or manual estimate
    material_rate: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    params: Mapped[dict[str, Any]]                                     # weight_tiers_kg, market_range, ...

    price_list_version: Mapped[PriceListVersion] = relationship(back_populates="items", lazy="raise")


class Request(Base):
    __tablename__ = "requests"
    __table_args__ = (
        CheckConstraint("status IN ('extracted', 'quoted', 'manual_estimate', 'needs_clarification')",
                        name="status"),
        CheckConstraint("urgency IN ('standard', 'urgent_within_week')", name="urgency"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    client_name: Mapped[Optional[str]]
    source_text: Mapped[str]
    status: Mapped[str] = mapped_column(server_default="extracted")
    location: Mapped[Optional[str]]
    urgency: Mapped[str] = mapped_column(server_default="standard")
    urgency_evidence: Mapped[Optional[str]]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())

    items: Mapped[list[RequestItem]] = relationship(
        back_populates="request", order_by="RequestItem.position", lazy="raise",
        cascade="all, delete-orphan")


class RequestItem(Base):
    __tablename__ = "request_items"
    __table_args__ = (
        UniqueConstraint("request_id", "position"),
        CheckConstraint("quantity >= 0", name="quantity_not_negative"),
        CheckConstraint("height_m >= 0", name="height_m_not_negative"),
        CheckConstraint("weight_kg >= 0", name="weight_kg_not_negative"),
    )

    id: Mapped[int] = mapped_column(Identity(), primary_key=True)
    request_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("requests.id", ondelete="CASCADE"))
    position: Mapped[int]
    # Extractor's id, possibly a v0.1 alias or NULL; not a FK: prices are versioned
    service_code: Mapped[Optional[str]]
    quantity: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    unit: Mapped[Optional[str]]
    height_m: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    floor: Mapped[Optional[int]]
    weight_kg: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    is_dismantle: Mapped[bool] = mapped_column(server_default=false())

    request: Mapped[Request] = relationship(back_populates="items", lazy="raise")


class Quote(Base):
    __tablename__ = "quotes"
    __table_args__ = (
        # Idempotency: the same request data on the same prices = the same quote
        UniqueConstraint("request_id", "price_list_version_id", "input_hash"),
        CheckConstraint("status IN ('ok', 'manual_estimate', 'needs_clarification')", name="status"),
        # A number only for a real calculation: "manual estimate" is never 0
        CheckConstraint("(status = 'ok') = (total IS NOT NULL)", name="total_only_if_ok"),
        CheckConstraint("total >= 0", name="total_not_negative"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    request_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("requests.id", ondelete="RESTRICT"))
    price_list_version_id: Mapped[int] = mapped_column(
        ForeignKey("price_list_versions.id", ondelete="RESTRICT"), index=True)
    input_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str]
    total: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2))
    currency: Mapped[str] = mapped_column(String(3))
    breakdown: Mapped[dict[str, Any]]                       # PriceBreakdown.to_dict()
    docx_path: Mapped[str]                                  # relative to settings.quotes_dir
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
