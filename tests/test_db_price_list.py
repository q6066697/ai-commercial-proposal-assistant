"""
Price list import, and the main guarantee of the API service: a quote priced
from the database is identical to one priced from the JSON file, line by line.
"""
import asyncio
import copy
import itertools
import json
import sys
from pathlib import Path

import pytest
from sqlalchemy import func, select

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from calculator import calculate_price
from db.models import PriceListItem, PriceListVersion
from db.price_lists import (NoActivePriceList, PriceListError, content_hash, get_active_version, import_price_list,
                            read_price_list_file, to_price_list)
from extractor import extract_rule_based
from price_list import LEGACY_PRICE_LIST_PATH, load_price_list
from schema import ExtractedRequest

pytestmark = pytest.mark.asyncio


@pytest.fixture
def raw():
    return read_price_list_file()


async def do_import(session_factory, raw):
    async with session_factory.begin() as s:
        return await import_price_list(s, raw)


async def active_price_list(session_factory):
    async with session_factory() as s:
        return to_price_list(await get_active_version(s))


async def versions(session_factory):
    async with session_factory() as s:
        rows = await s.execute(select(PriceListVersion.version, PriceListVersion.is_active)
                               .order_by(PriceListVersion.id))
        return [tuple(r) for r in rows]


def changed(raw, version=None, rate=99.0):
    new = copy.deepcopy(raw)
    new["services"][0]["rate"] = rate
    if version:
        new["meta"]["version"] = version
    return new


def sample_requests() -> list[ExtractedRequest]:
    """Demo and eval texts through the extractor, plus a sweep over every
    service × height × urgency × volume (× dismantling / cargo weight)."""
    texts = [p.read_text(encoding="utf-8") for p in sorted((ROOT / "data" / "demo_requests").glob("*.txt"))]
    for name in ("cases.json", "hard_cases.json"):
        cases = json.loads((ROOT / "data" / "eval_cases" / name).read_text(encoding="utf-8"))
        texts += [c["text"] for c in cases]
    texts += ["Монтаж световых коробов на фасаде ТЦ, г. Минск.",
              "Очистка кровли от снега 300 м2 после снегопада, г. Минск.",
              "Мойка фасада 900 м2 над проезжей частью, 7 этаж, г. Минск, срочно."]
    requests = [extract_rule_based(t) for t in texts]

    catalogue = load_price_list()["services"]
    heights = [{}, {"height_m": 15}, {"height_m": 15.5}, {"floor": 5}, {"floor": 9}]
    for service in [*catalogue, "facade_cleaning", "seam_sealing", "metal_structures_painting"]:
        # Only where they matter: cargo weight, dismantling of mounted things
        weights = [None, 50, 120.5, 501] if service == "cargo_lift" else [None]
        dismantle = [False, True] if catalogue.get(service, {}).get("category") == "mounting" else [False]
        for height, urgency, is_dismantle, weight, quantity in itertools.product(
                heights, ["standard", "urgent_within_week"], dismantle, weights, [None, 1, 333.33]):
            requests.append(ExtractedRequest(service=service, quantity=quantity, urgency=urgency, location="Минск",
                                             is_dismantle=is_dismantle, weight_kg=weight, raw_text="тест", **height))
    return requests


async def test_price_list_survives_the_round_trip(session_factory, raw):
    await do_import(session_factory, raw)
    assert await active_price_list(session_factory) == load_price_list()


async def test_database_and_json_prices_give_identical_quotes(session_factory, raw):
    await do_import(session_factory, raw)
    from_db, from_json = await active_price_list(session_factory), load_price_list()
    statuses = set()
    for request in sample_requests():
        expected = calculate_price(request, from_json).to_dict()
        assert calculate_price(request, from_db).to_dict() == expected, request
        statuses.add(expected["status"])
    assert statuses == {"ok", "manual_estimate", "needs_clarification"}


async def test_known_total_from_the_database(session_factory, raw):
    await do_import(session_factory, raw)
    text = (ROOT / "data" / "demo_requests" / "facade_cleaning.txt").read_text(encoding="utf-8")
    assert calculate_price(extract_rule_based(text), await active_price_list(session_factory)).total == 7560.0


async def test_import_twice_changes_nothing(session_factory, raw):
    first, second = await do_import(session_factory, raw), await do_import(session_factory, raw)
    assert (first.outcome, second.outcome) == ("created", "unchanged")
    async with session_factory() as s:
        assert await s.scalar(select(func.count()).select_from(PriceListItem)) == len(raw["services"])


async def test_changed_price_under_the_same_version_is_rejected(session_factory, raw):
    await do_import(session_factory, raw)
    with pytest.raises(PriceListError, match="meta.version"):
        await do_import(session_factory, changed(raw))
    assert (await active_price_list(session_factory))["services"]["facade_washing"]["rate"] == 1.5


async def test_changed_rule_under_the_same_version_is_rejected(session_factory, raw):
    await do_import(session_factory, raw)
    new = copy.deepcopy(raw)
    new["pricing_rules"]["minimum_order"]["amount"] = 700
    with pytest.raises(PriceListError, match="meta.version"):
        await do_import(session_factory, new)


async def test_new_version_becomes_active_and_the_old_one_stays(session_factory, raw):
    await do_import(session_factory, raw)
    await do_import(session_factory, changed(raw, version="next"))
    assert await versions(session_factory) == [(raw["meta"]["version"], False), ("next", True)]
    assert (await active_price_list(session_factory))["services"]["facade_washing"]["rate"] == 99.0


async def test_old_prices_can_be_activated_again(session_factory, raw):
    await do_import(session_factory, raw)
    await do_import(session_factory, changed(raw, version="next"))
    assert (await do_import(session_factory, raw)).outcome == "activated"
    assert await versions(session_factory) == [(raw["meta"]["version"], True), ("next", False)]


async def test_old_prices_can_return_under_a_new_version(session_factory, raw):
    await do_import(session_factory, raw)
    same = copy.deepcopy(raw)
    same["meta"]["version"] = "next"
    assert (await do_import(session_factory, same)).outcome == "created"


async def test_concurrent_imports_create_one_version(session_factory, raw):
    results = await asyncio.gather(*(do_import(session_factory, raw) for _ in range(5)))
    assert sorted(r.outcome for r in results) == ["created"] + ["unchanged"] * 4


async def test_failed_import_leaves_the_active_version(session_factory, raw):
    await do_import(session_factory, raw)
    with pytest.raises(Exception, match="rate_not_negative"):
        await do_import(session_factory, changed(raw, version="next", rate=-1.0))
    assert await versions(session_factory) == [(raw["meta"]["version"], True)]


async def test_no_active_price_list_is_a_clear_error(session_factory):
    with pytest.raises(NoActivePriceList, match="import_price_list.py"):
        await active_price_list(session_factory)


async def test_rate_is_stored_exactly(session_factory, raw):
    await do_import(session_factory, changed(raw, rate=1.123456789))
    assert (await active_price_list(session_factory))["services"]["facade_washing"]["rate"] == 1.123456789


async def test_hash_ignores_notes_that_do_not_affect_prices(raw):
    new = copy.deepcopy(raw)
    new["meta"]["note"] = "другая заметка"
    new["client_baseline"] = {}
    assert content_hash(new) == content_hash(raw)
    assert content_hash(changed(raw)) != content_hash(raw)


def _drop_version(raw):
    del raw["meta"]["version"]


def _duplicate_id(raw):
    raw["services"].append(copy.deepcopy(raw["services"][0]))


def _drop_rate(raw):
    del raw["services"][0]["rate"]


@pytest.mark.parametrize("break_it, message", [
    (_drop_version, "meta.version"), (_duplicate_id, "facade_washing"), (_drop_rate, "rate")])
async def test_broken_file_is_rejected(session_factory, raw, break_it, message):
    break_it(raw)
    with pytest.raises(PriceListError, match=message):
        await do_import(session_factory, raw)


async def test_legacy_demo_price_list_is_rejected(session_factory):
    with pytest.raises(PriceListError, match="v0.3"):
        await do_import(session_factory, read_price_list_file(LEGACY_PRICE_LIST_PATH))
