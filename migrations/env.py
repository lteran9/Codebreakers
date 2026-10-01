"""Alembic environment for Codebreakers persistence migrations."""

import os
from logging.config import fileConfig
from typing import cast

from alembic import context
from sqlalchemy import Connection, pool

from codebreakers.infrastructure.persistence.postgres import (
    Base,
    create_postgres_engine,
)

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations without opening a database connection."""
    url = os.environ.get("CODEBREAKERS_DATABASE_URL") or config.get_main_option(
        "sqlalchemy.url"
    )
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def _run_with_connection(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations using a supplied or newly created connection."""
    supplied = config.attributes.get("connection")
    if supplied is not None:
        _run_with_connection(cast(Connection, supplied))
        return
    section = config.get_section(config.config_ini_section, {})
    section["sqlalchemy.url"] = os.environ.get("CODEBREAKERS_DATABASE_URL") or str(
        section["sqlalchemy.url"]
    )
    database_url = os.environ.get("CODEBREAKERS_DATABASE_URL") or str(
        section["sqlalchemy.url"]
    )
    connectable = create_postgres_engine(database_url, poolclass=pool.NullPool)
    with connectable.connect() as connection:
        _run_with_connection(connection)


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
