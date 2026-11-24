"""
Fixtures for tests that need PostgreSQL (docker compose up -d --wait).

They use a separate database, <POSTGRES_DB>_test, created and migrated here,
never the working one. If the server is not running, these tests are skipped
with the reason shown; all other tests don't touch the database at all.
Anything else (wrong password, broken migration) is a failure, not a skip.
"""
import sys
from pathlib import Path

import pytest
import pytest_asyncio

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))


def check_test_database_name(name: str) -> None:
    """The fixtures drop the whole schema: refuse anything but a test database."""
    if not name.endswith("_test"):
        raise RuntimeError(f"refusing to run tests on database {name!r}: the name must end with _test")


def test_settings():
    from settings import Settings

    base = Settings()
    return base.model_copy(update={"postgres_db": f"{base.postgres_db}_test"})


test_settings.__test__ = False  # a helper, not a test


def alembic_config(connection):
    from alembic.config import Config

    config = Config(str(ROOT / "alembic.ini"))
    config.attributes["connection"] = connection
    return config


def migrate(connection, revision="head"):
    from alembic import command

    command.upgrade(alembic_config(connection), revision)


async def _prepare(settings) -> None:
    from sqlalchemy import text

    from db.session import make_engine

    name = settings.postgres_db
    check_test_database_name(name)

    admin = make_engine(settings.model_copy(update={"postgres_db": "postgres"}),
                        isolation_level="AUTOCOMMIT", connect_args={"timeout": 5})
    try:
        async with admin.connect() as c:
            if not await c.scalar(text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": name}):
                await c.execute(text(f'CREATE DATABASE "{name}"'))
    finally:
        await admin.dispose()

    engine = make_engine(settings)
    try:
        async with engine.begin() as c:
            await c.execute(text("DROP SCHEMA public CASCADE"))
            await c.execute(text("CREATE SCHEMA public"))
            await c.run_sync(migrate)
    finally:
        await engine.dispose()


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def database():
    """Settings of a freshly migrated test database."""
    from pydantic import ValidationError

    try:
        settings = test_settings()
    except ValidationError:
        pytest.skip("POSTGRES_PASSWORD is not set (see .env.example)")
    try:
        await _prepare(settings)
    except (ConnectionError, TimeoutError) as e:
        pytest.skip(f"PostgreSQL is not running on {settings.postgres_host}:{settings.postgres_port}: "
                    f"docker compose up -d --wait ({type(e).__name__})")
    return settings


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def shared_engine(database):
    """One pool and one event loop for the whole run: a connection to Docker on
    Windows costs ~0.3 s. Tests using it need asyncio(loop_scope="session")."""
    from db.session import make_engine

    # Faster commits; a test database doesn't need to survive a power cut
    engine = make_engine(database, connect_args={"server_settings": {"synchronous_commit": "off"}})
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture(loop_scope="session")
async def engine(shared_engine):
    """Empty tables for every test."""
    from db.models import Base

    async with shared_engine.begin() as c:
        for table in reversed(Base.metadata.sorted_tables):
            await c.execute(table.delete())
    return shared_engine


@pytest.fixture
def session_factory(engine):
    from db.session import make_session_factory

    return make_session_factory(engine)
