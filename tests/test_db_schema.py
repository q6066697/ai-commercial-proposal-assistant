"""
Migration matches the models, can be rolled back, and the constraints that
protect prices and quotes are enforced by PostgreSQL itself.
"""
import uuid
from decimal import Decimal

import pytest
from alembic import command
from conftest import alembic_config, check_test_database_name, migrate
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from db.models import PriceListVersion, Quote, Request, RequestItem

pytestmark = pytest.mark.asyncio(loop_scope="session")


def version(**kw):
    return PriceListVersion(**{"version": "v1", "content_hash": "a" * 64, "currency": "BYN", "rules": {},
                               "manual_items": [], "is_active": True, "items": []} | kw)


def quote(request_id, version_id, **kw):
    return Quote(**{"request_id": request_id, "price_list_version_id": version_id, "input_hash": "h" * 64,
                    "status": "ok", "total": Decimal("650.00"), "currency": "BYN", "breakdown": {},
                    "docx_path": "x.docx"} | kw)


async def add_request_and_version(session):
    r, v = Request(source_text="мойка фасада"), version()
    session.add_all([r, v])
    await session.flush()
    return r.id, v.id


async def test_migration_matches_models(engine):
    async with engine.connect() as c:
        await c.run_sync(lambda conn: command.check(alembic_config(conn)))


async def test_downgrade_removes_everything_and_upgrade_restores(engine):
    count = text("SELECT count(*) FROM pg_tables WHERE schemaname = 'public' AND tablename <> 'alembic_version'")
    async with engine.connect() as c:
        await c.run_sync(lambda conn: command.downgrade(alembic_config(conn), "base"))
        after_downgrade = await c.scalar(count)
        await c.run_sync(migrate)
        after_upgrade = await c.scalar(count)
        await c.commit()
    await engine.dispose()  # pooled connections remember statements on the dropped tables
    assert (after_downgrade, after_upgrade) == (0, 5)


async def test_request_defaults_and_cascade(session_factory):
    async with session_factory.begin() as s:
        r = Request(source_text="мойка фасада", items=[RequestItem(position=1, quantity=Decimal("4200.5"))])
        s.add(r)
        await s.flush()
        assert (r.status, r.urgency) == ("extracted", "standard")
        assert r.created_at.tzinfo is not None
        created = r.updated_at
        r.status = "quoted"
        await s.flush()
        assert r.updated_at >= created  # read back without implicit I/O
        await s.delete(r)
        await s.flush()
        assert await s.scalar(select(func.count()).select_from(RequestItem)) == 0


async def test_only_one_active_price_list(session_factory):
    async with session_factory() as s:
        s.add_all([version(), version(version="v2", content_hash="b" * 64)])
        with pytest.raises(IntegrityError, match="uq_price_list_versions_active"):
            await s.flush()


async def test_duplicate_quote_is_rejected(session_factory):
    async with session_factory() as s:
        ids = await add_request_and_version(s)
        s.add(quote(*ids))
        await s.flush()
        s.add(quote(*ids))
        with pytest.raises(IntegrityError, match="uq_quotes_request_id_price_list_version_id_input_hash"):
            await s.flush()


@pytest.mark.parametrize("kw, constraint", [
    ({"status": "ok", "total": None}, "total_only_if_ok"),
    ({"status": "manual_estimate", "total": Decimal("0")}, "total_only_if_ok"),
    ({"total": Decimal("-1")}, "total_not_negative"),
    ({"status": "paid", "total": None}, "ck_quotes_status"),
])
async def test_quote_total_rules(session_factory, kw, constraint):
    async with session_factory() as s:
        ids = await add_request_and_version(s)
        s.add(quote(*ids, **kw))
        with pytest.raises(IntegrityError, match=constraint):
            await s.flush()


async def test_quoted_request_and_price_list_cannot_be_deleted(session_factory):
    async with session_factory.begin() as s:
        request_id, version_id = await add_request_and_version(s)
        s.add(quote(request_id, version_id))
    for statement, key in (("DELETE FROM requests WHERE id = :id", request_id),
                           ("DELETE FROM price_list_versions WHERE id = :id", version_id)):
        async with session_factory() as s:
            with pytest.raises(IntegrityError, match="fk_quotes"):
                await s.execute(text(statement), {"id": key})


async def test_unknown_request_status_is_rejected(session_factory):
    async with session_factory() as s:
        s.add(Request(id=uuid.uuid4(), source_text="x", status="sent"))
        with pytest.raises(IntegrityError, match="ck_requests_status"):
            await s.flush()


@pytest.mark.parametrize("name", ["proposal", "postgres", "test_proposal", ""])
async def test_fixtures_refuse_a_working_database(name):
    with pytest.raises(RuntimeError, match="_test"):
        check_test_database_name(name)
