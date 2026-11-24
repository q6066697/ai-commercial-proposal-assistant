"""
API service end to end: HTTP -> extractor -> calculator -> PostgreSQL -> DOCX.
Numbers are the same as in the CLI tests; what is checked here is storage,
idempotency and error answers.
"""
import asyncio
import copy
import io
import json
import sys
import uuid
from datetime import date, datetime, timezone
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from docx import Document
from sqlalchemy import func, select, text, update
from sqlalchemy.exc import DBAPIError

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from api import service
from api.main import create_app
from calculator import calculate_price
from db.models import Quote, Request, RequestItem
from db.price_lists import import_price_list, read_price_list_file
from extractor import extract_rule_based
from price_list import load_price_list
from proposal_generator import generate_proposal_docx

pytestmark = pytest.mark.asyncio(loop_scope="session")

FACADE = (ROOT / "data" / "demo_requests" / "facade_cleaning.txt").read_text(encoding="utf-8")
MANUAL = "Просим КП на монтаж световых коробов на фасаде ТЦ, г. Минск, высота 12 м."
NO_VOLUME = "Нужна мойка фасада, высота 20 м, г. Минск."
UNKNOWN = "Просим прислать КП на озеленение территории."


def client_for(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


@pytest.fixture
def quotes_dir(tmp_path):
    return tmp_path / "quotes"


@pytest.fixture
def app(database, engine, quotes_dir):
    return create_app(database.model_copy(update={"quotes_dir": quotes_dir}), engine)


@pytest_asyncio.fixture(loop_scope="session")
async def empty_api(app):
    """No price list imported."""
    async with client_for(app) as c:
        yield c


@pytest_asyncio.fixture(loop_scope="session")
async def api(empty_api, session_factory):
    async with session_factory.begin() as s:
        await import_price_list(s, read_price_list_file())
    return empty_api


async def make_request(api, text=FACADE, **body) -> dict:
    r = await api.post("/requests", json={"text": text, **body})
    assert r.status_code == 201, r.text
    return r.json()


async def make_quote(api, request_id) -> httpx.Response:
    return await api.post(f"/requests/{request_id}/quote")


async def quote_count(session_factory) -> int:
    async with session_factory() as s:
        return await s.scalar(select(func.count()).select_from(Quote))


def docx_text(content: bytes) -> str:
    doc = Document(io.BytesIO(content))
    parts = [p.text for p in doc.paragraphs]
    for table in doc.tables:
        parts += [" | ".join(c.text for c in row.cells) for row in table.rows]
    return "\n".join(parts)


# --- /requests --------------------------------------------------------------

async def test_create_and_read_request(api):
    created = await make_request(api, client_name="Отдел закупок")
    assert created["status"] == "extracted"
    assert (created["client_name"], created["location"]) == ("Отдел закупок", "Минск")
    assert created["source_text"] == FACADE
    [item] = created["items"]
    assert (item["position"], item["service_code"], item["quantity"], item["height_m"]) == (
        1, "facade_cleaning", 4200.0, 18.0)

    r = await api.get(f"/requests/{created['id']}")
    assert r.status_code == 200 and r.json() == created


async def test_request_without_a_known_service_is_still_stored(api):
    [item] = (await make_request(api, UNKNOWN))["items"]
    assert item["service_code"] is None


@pytest.mark.parametrize("body", [
    {}, {"text": ""}, {"text": "  \n "}, {"text": "x" * 20_001}, {"text": 5}, {"text": "мойка", "client_name": "x" * 201}])
async def test_invalid_body(api, body):
    assert (await api.post("/requests", json=body)).status_code == 422


@pytest.mark.parametrize("content", [
    b'{"text": "a\\u0000b"}', b'{"text": "a\\ud800"}', b'{"text": NaN}', b'{"text": "a", "client_name": "\\u0000"}', b"{bad"])
async def test_text_the_database_cannot_hold_is_422_not_500(api, content):
    r = await api.post("/requests", content=content, headers={"content-type": "application/json"})
    assert r.status_code == 422, r.text


@pytest.mark.parametrize("source, field", [
    ("Мойка фасада 100 м2, высота 900 метров, г. Минск", "высота"),
    ("Мойка фасада 100 м2, 300 этаж, г. Минск", "floor"),
    ("Подъём груза " + "9" * 400 + " кг на 9 этаж", "вес груза")])
async def test_impossible_value_is_422_and_nothing_is_stored(api, session_factory, source, field):
    r = await api.post("/requests", json={"text": source})
    assert r.status_code == 422 and field in r.json()["detail"]
    async with session_factory() as s:
        assert await s.scalar(select(func.count()).select_from(Request)) == 0


async def test_unknown_and_malformed_ids(api):
    missing = uuid.uuid4()
    assert (await api.get(f"/requests/{missing}")).status_code == 404
    assert (await make_quote(api, missing)).status_code == 404
    assert (await api.get(f"/quotes/{missing}/download")).status_code == 404
    assert (await api.get("/requests/abc")).status_code == 422
    assert (await api.get("/quotes/..%2F..%2F.env/download")).status_code in (404, 422)


# --- /quote -----------------------------------------------------------------

async def test_quote_matches_the_cli_calculation(api):
    request = await make_request(api)
    r = await make_quote(api, request["id"])
    assert r.status_code == 201
    body = r.json()
    expected = calculate_price(extract_rule_based(FACADE), load_price_list())
    assert body["total"] == "7560.00" and body["currency"] == "BYN"
    assert body["breakdown"] == expected.to_dict()
    assert body["price_list_version"] == expected.price_list_version
    assert (await api.get(f"/requests/{request['id']}")).json()["status"] == "quoted"


@pytest.mark.parametrize("source, total", [
    ("Мойка фасада офиса. Площадь 200 м2, высота 10 м. г. Минск.", "650.00"),                     # minimum order
    ("Мойка фасада жилого дома, 1000 м2, работы до 7 этажа. г. Минск.", "1800.00"),               # floor -> height
    ("Мойка фасада. Площадь: 500 м2. г. Минск.", "900.00"),                                       # unknown height
    ("Покраска фасада 500 м2, высота 12 м, г. Минск. Просим выполнить срочно.", "3650.00"),       # urgency, materials
    ("Покраска фасада 500 м2, высота 12 м, г. Минск. Не срочно.", "3250.00"),
    ("Мойка фасада 1000 м2 над проезжей частью, высота 10 м, г. Минск.", "1875.00"),              # complex access
    ("Демонтаж баннера 200 м2, высота 20 м, г. Минск.", "720.00"),
    ("Подъём груза: 4 шт. по 250 кг на 9 этаж, г. Гродно.", "1600.00"),
    ("Нужен подъём груза 650 кг на 9 этаж, г. Минск.", None),                                     # too heavy
    ("Очистка кровли от снега 300 м2 после снегопада, г. Минск.", None),                          # winter peak
])
async def test_pricing_rules_through_the_api(api, source, total):
    """Totals are written out by hand, not taken from the calculator."""
    body = (await make_quote(api, (await make_request(api, source))["id"])).json()
    assert body["total"] == total
    assert body["status"] == ("ok" if total else "manual_estimate")
    if total:
        assert f"{sum(line['amount'] for line in body['breakdown']['lines']):.2f}" == total


async def test_stored_request_gives_back_the_same_calculator_input(api, session_factory):
    texts = [FACADE, MANUAL, NO_VOLUME, UNKNOWN, "Подъём груза: 4 шт. по 250,5 кг на 9 этаж, г. Гродно.",
             "Демонтаж баннера 200 м2, высота 20 м, г. Минск.",
             "Покраска фасада 500 м2, высота 12 м, г. Минск. Просим выполнить срочно.\r\n"]
    for name in ("cases.json", "hard_cases.json"):
        texts += [c["text"] for c in json.loads((ROOT / "data" / "eval_cases" / name).read_text(encoding="utf-8"))]
    for source in texts:
        created = await make_request(api, source)
        async with session_factory() as s:
            stored = service.to_extracted(await service.get_request(s, uuid.UUID(created["id"])))
        assert stored == extract_rule_based(source)


async def test_download_is_the_same_document_as_from_the_cli(api, tmp_path):
    request = await make_request(api)
    quote = (await make_quote(api, request["id"])).json()
    r = await api.get(quote["download_url"])
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/vnd.openxmlformats")
    assert ".docx" in r.headers["content-disposition"]

    extracted = extract_rule_based(FACADE)
    path = generate_proposal_docx(extracted, calculate_price(extracted), tmp_path / "cli.docx",
                                  proposal_number=f"API-{quote['id'][:8].upper()}")
    assert docx_text(r.content) == docx_text(path.read_bytes())


async def test_manual_estimate_has_no_total(api):
    request = await make_request(api, MANUAL)
    r = await make_quote(api, request["id"])
    assert r.status_code == 201
    assert (r.json()["status"], r.json()["total"]) == ("manual_estimate", None)
    assert (await api.get(f"/requests/{request['id']}")).json()["status"] == "manual_estimate"
    document = docx_text((await api.get(r.json()["download_url"])).content)
    assert "ручной расчёт" in document and "ИТОГО" not in document


async def test_missing_volume_needs_clarification(api):
    request = await make_request(api, NO_VOLUME)
    r = await make_quote(api, request["id"])
    assert r.status_code == 201
    assert (r.json()["status"], r.json()["total"]) == ("needs_clarification", None)
    assert r.json()["breakdown"]["clarify_fields"] == ["quantity"]


async def test_unknown_service_is_422_without_a_quote(api, session_factory, quotes_dir):
    request = await make_request(api, UNKNOWN)
    r = await make_quote(api, request["id"])
    assert r.status_code == 422 and r.json()["clarify_fields"] == ["service"]
    assert await quote_count(session_factory) == 0 and not quotes_dir.exists()
    assert (await api.get(f"/requests/{request['id']}")).json()["status"] == "needs_clarification"


async def test_no_price_list_is_503(empty_api, session_factory):
    request = await make_request(empty_api)
    r = await make_quote(empty_api, request["id"])
    assert r.status_code == 503 and "import_price_list" in r.json()["detail"]
    assert await quote_count(session_factory) == 0


async def test_service_missing_from_the_price_list_is_409(api, session_factory):
    raw = copy.deepcopy(read_price_list_file())
    raw["meta"]["version"] = "no-facade"
    raw["services"] = [s for s in raw["services"] if s["id"] != "facade_washing"]
    async with session_factory.begin() as s:
        await import_price_list(s, raw)
    request = await make_request(api)
    assert (await make_quote(api, request["id"])).status_code == 409


async def test_several_positions_are_refused_not_dropped(api, session_factory):
    request = await make_request(api)
    async with session_factory.begin() as s:
        s.add(RequestItem(request_id=uuid.UUID(request["id"]), position=2, service_code="window_washing"))
    assert (await make_quote(api, request["id"])).status_code == 409
    assert await quote_count(session_factory) == 0


# --- Idempotency ------------------------------------------------------------

async def test_second_quote_is_the_same_quote(api, session_factory, quotes_dir):
    request = await make_request(api)
    first, second = await make_quote(api, request["id"]), await make_quote(api, request["id"])
    assert (first.status_code, second.status_code) == (201, 200)
    assert first.json() == second.json()
    assert await quote_count(session_factory) == 1 and len(list(quotes_dir.iterdir())) == 1


async def test_parallel_calls_create_one_quote(api, session_factory, quotes_dir):
    request = await make_request(api)
    answers = await asyncio.gather(*(make_quote(api, request["id"]) for _ in range(8)))
    assert sorted(r.status_code for r in answers) == [200] * 7 + [201]
    assert len({r.json()["id"] for r in answers}) == 1
    assert await quote_count(session_factory) == 1 and len(list(quotes_dir.iterdir())) == 1


async def test_many_requests_at_once(api, session_factory):
    """More parallel work than pooled connections: everyone waits, nobody fails."""
    async def one(volume):
        request = await make_request(api, f"Мойка фасада {volume} м2, высота 10 м, г. Минск.")
        r = await make_quote(api, request["id"])
        assert r.status_code == 201, r.text
        return r.json()["total"]

    totals = await asyncio.gather(*(one(1000 + i * 100) for i in range(20)))
    assert totals == [f"{1500 + i * 150}.00" for i in range(20)]
    assert await quote_count(session_factory) == 20


async def test_equal_requests_have_their_own_quotes(api, session_factory):
    ids = [(await make_request(api))["id"] for _ in range(2)]
    quotes = [(await make_quote(api, i)).json()["id"] for i in ids]
    assert quotes[0] != quotes[1]


async def test_new_price_list_makes_a_new_quote_and_keeps_the_old_one(api, session_factory):
    request = await make_request(api)
    old = (await make_quote(api, request["id"])).json()
    old_document = docx_text((await api.get(old["download_url"])).content)

    raw = copy.deepcopy(read_price_list_file())
    raw["meta"]["version"] = "next"
    raw["services"][0]["rate"] = 2.0
    async with session_factory.begin() as s:
        await import_price_list(s, raw)

    r = await make_quote(api, request["id"])
    new = r.json()
    assert r.status_code == 201 and new["id"] != old["id"]
    assert (new["total"], new["price_list_version"]) == ("10080.00", "next")
    assert await quote_count(session_factory) == 2
    assert docx_text((await api.get(old["download_url"])).content) == old_document

    # Back to the old prices: the old quote is found, not a third one
    async with session_factory.begin() as s:
        await import_price_list(s, read_price_list_file())
    r = await make_quote(api, request["id"])
    assert (r.status_code, r.json()["id"]) == (200, old["id"])


async def test_changed_request_makes_a_new_quote(api, session_factory):
    request = await make_request(api)
    await make_quote(api, request["id"])
    async with session_factory.begin() as s:
        await s.execute(update(RequestItem).values(quantity=1000))
    r = await make_quote(api, request["id"])
    assert (r.status_code, r.json()["total"]) == (201, "1800.00")


async def test_new_calculation_version_makes_a_new_quote(api, monkeypatch):
    request = await make_request(api)
    await make_quote(api, request["id"])
    monkeypatch.setattr(service, "CALCULATION_VERSION", service.CALCULATION_VERSION + 1)
    assert (await make_quote(api, request["id"])).status_code == 201


# --- Document on disk -------------------------------------------------------

@pytest.mark.parametrize("how", ["download", "quote"])
async def test_lost_file_comes_back_with_the_same_numbers_and_date(api, session_factory, quotes_dir, how):
    request = await make_request(api)
    quote = (await make_quote(api, request["id"])).json()
    async with session_factory.begin() as s:
        await s.execute(update(Quote).values(created_at=datetime(2026, 1, 15, 12, tzinfo=timezone.utc)))
    for f in quotes_dir.iterdir():
        f.unlink()

    if how == "quote":
        assert (await make_quote(api, request["id"])).status_code == 200
        assert len(list(quotes_dir.iterdir())) == 1
    document = docx_text((await api.get(quote["download_url"])).content)
    assert "от 15.01.2026" in document and "ИТОГО: 7 560,00 BYN" in document


async def test_parallel_downloads_of_a_lost_file(api, quotes_dir):
    request = await make_request(api)
    quote = (await make_quote(api, request["id"])).json()
    for f in quotes_dir.iterdir():
        f.unlink()
    answers = await asyncio.gather(*(api.get(quote["download_url"]) for _ in range(6)))
    assert {r.status_code for r in answers} == {200}
    assert all("ИТОГО: 7 560,00 BYN" in docx_text(r.content) for r in answers)
    assert [f.suffix for f in quotes_dir.iterdir()] == [".docx"]


async def test_path_check_does_not_depend_on_the_folder_being_created(tmp_path):
    """Path.resolve() failed here once in a while: 404 for the very first
    parallel quotes on a machine without outputs/quotes (Windows)."""
    quote = Quote(docx_path=f"{uuid.uuid4()}.docx")
    for i in range(300):
        folder = tmp_path / str(i) / "quotes"
        creating = asyncio.create_task(asyncio.to_thread(folder.mkdir, parents=True))
        await asyncio.sleep(0)
        assert service.docx_file(quote, folder) == folder / quote.docx_path
        await creating


async def test_failed_document_leaves_no_quote(api, session_factory, quotes_dir, monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("disk full")

    request = await make_request(api)
    monkeypatch.setattr(service, "generate_proposal_docx", fail)
    with pytest.raises(RuntimeError, match="disk full"):
        await make_quote(api, request["id"])
    assert await quote_count(session_factory) == 0
    assert (await api.get(f"/requests/{request['id']}")).json()["status"] == "extracted"
    monkeypatch.undo()
    assert (await make_quote(api, request["id"])).status_code == 201


async def test_only_generated_file_names_are_served(api, session_factory):
    request = await make_request(api)
    quote = (await make_quote(api, request["id"])).json()
    own = f"{quote['id']}.docx"
    for name in ("../../.env", "..", "", "C:\\x.docx", "C:x.docx", "/etc/passwd", "a.docx",
                 f"../{own}", f"{own}:stream", f"{own}\n"):
        async with session_factory.begin() as s:
            await s.execute(update(Quote).values(docx_path=name))
        assert (await api.get(quote["download_url"])).status_code == 404, name


async def test_default_proposal_date_is_still_today(tmp_path):
    extracted = extract_rule_based(FACADE)
    path = generate_proposal_docx(extracted, calculate_price(extracted), tmp_path / "kp.docx")
    assert f"от {date.today():%d.%m.%Y}" in docx_text(path.read_bytes())


# --- Health -----------------------------------------------------------------

async def test_health(empty_api):
    r = await empty_api.get("/health")
    assert (r.status_code, r.json()) == (200, {"status": "ok", "database": "ok"})


async def test_openapi_lists_exactly_the_agreed_endpoints(app):
    operations = {(m.upper(), path) for path, item in app.openapi()["paths"].items() for m in item}
    assert operations == {("GET", "/health"), ("POST", "/requests"), ("GET", "/requests/{request_id}"),
                          ("POST", "/requests/{request_id}/quote"), ("GET", "/quotes/{quote_id}/download")}


async def test_database_error_does_not_leak_customer_text(engine):
    async with engine.connect() as c:
        with pytest.raises(DBAPIError) as e:
            await c.execute(text("SELECT CAST(:t AS integer) / 0, CAST(:secret AS text)"),
                            {"t": 1, "secret": "телефон клиента"})
    assert "телефон клиента" not in str(e.value)


async def test_database_down_is_503():
    from settings import Settings

    settings = Settings(_env_file=None, postgres_host="127.0.0.1", postgres_port=1, postgres_password="unused")
    async with client_for(create_app(settings)) as c:
        health = await c.get("/health")
        assert (health.status_code, health.json()["database"]) == (503, "unavailable")
        assert (await c.post("/requests", json={"text": "мойка фасада"})).status_code == 503
        assert (await c.get(f"/requests/{uuid.uuid4()}")).status_code == 503
