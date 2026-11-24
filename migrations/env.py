"""
Alembic environment: async engine, URL from src/settings.py (so the password
is never in alembic.ini). Tests pass a ready connection through
config.attributes["connection"].
"""
from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context

from db.models import Base
from db.session import make_engine
from settings import get_settings

config = context.config


def run_migrations(connection) -> None:
    context.configure(connection=connection, target_metadata=Base.metadata, compare_server_default=True)
    with context.begin_transaction():
        context.run_migrations()


async def run_online() -> None:
    engine = make_engine(get_settings())
    try:
        async with engine.connect() as connection:
            await connection.run_sync(run_migrations)
    finally:
        await engine.dispose()


def run_offline() -> None:
    """alembic upgrade head --sql: prints SQL, no connection needed."""
    context.configure(dialect_name="postgresql", target_metadata=Base.metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


if (connection := config.attributes.get("connection")) is not None:
    run_migrations(connection)
else:
    if config.config_file_name is not None:
        fileConfig(config.config_file_name, disable_existing_loggers=False)
    if context.is_offline_mode():
        run_offline()
    else:
        asyncio.run(run_online())
