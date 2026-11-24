"""
data/price_list.json -> database, and back into the shape the calculator uses.

The JSON file is the master (CLI and Streamlit read it directly); the database
holds an immutable copy of every imported version. Reading rebuilds the JSON
structure and passes it through the same normalize_price_list(), so there is
one pricing code path and nothing here calculates or adjusts a price.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from db.models import PriceListItem, PriceListVersion
from db.numbers import to_decimal, to_float
from price_list import PRICE_LIST_PATH, normalize_price_list

# Service fields with their own column; everything else goes to params
COLUMN_FIELDS = {"id", "name", "unit", "category", "source", "rate", "material_rate"}
REQUIRED_SERVICE_FIELDS = ("id", "name", "unit", "rate")
# Serializes imports ("one active version"); any constant will do
IMPORT_LOCK_KEY = 740_001


class PriceListError(ValueError):
    """The price list can't be imported as is."""


class NoActivePriceList(RuntimeError):
    """Nothing imported yet: run scripts/import_price_list.py."""


@dataclass
class ImportResult:
    outcome: Literal["created", "activated", "unchanged"]
    version: str
    services: int


def read_price_list_file(path: Path = PRICE_LIST_PATH) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _validate(raw: dict) -> None:
    if not isinstance(raw.get("services"), list):
        raise PriceListError("Ожидается прайс формата v0.3: services должен быть списком.")
    meta = raw.get("meta", {})
    for key in ("version", "currency"):
        if not meta.get(key):
            raise PriceListError(f"В прайсе не заполнено meta.{key}.")
    for s in raw["services"]:
        missing = [f for f in REQUIRED_SERVICE_FIELDS if f not in s]
        if missing:
            raise PriceListError(f"Услуга {s.get('id', '?')}: нет полей {', '.join(missing)}.")
    ids = [s["id"] for s in raw["services"]]
    duplicates = sorted({i for i in ids if ids.count(i) > 1})
    if duplicates:
        raise PriceListError(f"Повторяются id услуг: {', '.join(duplicates)}.")


def price_content(raw: dict) -> dict:
    """The part of the file that affects a quote. Market sources, client
    baseline and the like can change without a new version."""
    return {
        "version": raw["meta"]["version"],
        "currency": raw["meta"]["currency"],
        "company": raw["meta"].get("company"),
        "services": raw["services"],
        "manual_items": raw.get("manual_estimate_only", {}).get("items", []),
        "rules": raw.get("pricing_rules", {}),
    }


def content_hash(raw: dict) -> str:
    canonical = json.dumps(price_content(raw), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


async def import_price_list(session: AsyncSession, raw: dict) -> ImportResult:
    """Imports and activates a version. Importing the same file again changes
    nothing; importing an older known version makes it active again. Does not
    commit: the caller owns the transaction."""
    _validate(raw)
    content = price_content(raw)
    version, digest = content["version"], content_hash(raw)

    await session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": IMPORT_LOCK_KEY})
    existing = await session.scalar(select(PriceListVersion).where(PriceListVersion.version == version))
    if existing is not None:
        if existing.content_hash != digest:
            raise PriceListError(
                f"Прайс «{version}» уже импортирован с другим содержимым. "
                f"Измените meta.version в data/price_list.json и повторите импорт.")
        if existing.is_active:
            return ImportResult("unchanged", version, len(content["services"]))

    # Before the new version turns active: the unique index allows only one
    await session.execute(update(PriceListVersion).where(PriceListVersion.is_active).values(is_active=False))
    if existing is not None:
        existing.is_active = True
        await session.flush()
        return ImportResult("activated", version, len(content["services"]))

    session.add(PriceListVersion(
        version=version,
        content_hash=digest,
        currency=content["currency"],
        company=content["company"],
        rules=content["rules"],
        manual_items=content["manual_items"],
        is_active=True,
        items=[
            PriceListItem(
                code=s["id"], name=s["name"], unit=s["unit"],
                category=s.get("category"), source=s.get("source"),
                rate=to_decimal(s["rate"]), material_rate=to_decimal(s.get("material_rate")),
                params={k: v for k, v in s.items() if k not in COLUMN_FIELDS},
            )
            for s in content["services"]
        ],
    ))
    await session.flush()
    return ImportResult("created", version, len(content["services"]))


async def get_active_version(session: AsyncSession) -> PriceListVersion:
    version = await session.scalar(
        select(PriceListVersion).where(PriceListVersion.is_active).options(selectinload(PriceListVersion.items)))
    if version is None:
        raise NoActivePriceList("В базе нет активного прайса: запустите scripts/import_price_list.py.")
    return version


def to_price_list(version: PriceListVersion) -> dict:
    """Normalized price list for calculate_price(). Needs version.items loaded."""
    services: list[dict[str, Any]] = []
    for item in version.items:
        service = {"id": item.code, "name": item.name, "unit": item.unit, "rate": to_float(item.rate), **item.params}
        optional = {"category": item.category, "source": item.source, "material_rate": to_float(item.material_rate)}
        services.append(service | {k: v for k, v in optional.items() if v is not None})
    return normalize_price_list({
        "meta": {"version": version.version, "currency": version.currency, "company": version.company},
        "services": services,
        "manual_estimate_only": {"items": version.manual_items},
        "pricing_rules": version.rules,
    })
