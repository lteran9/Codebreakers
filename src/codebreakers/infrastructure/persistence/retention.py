"""Command-line retention cleanup for scheduled operations."""

import os

from sqlalchemy.orm import sessionmaker

from codebreakers.infrastructure.persistence.postgres import (
    SqlAlchemyAnalysisRepository,
    create_postgres_engine,
)


def main() -> None:
    """Delete expired analysis jobs without displaying database credentials."""
    database_url = os.environ.get("CODEBREAKERS_DATABASE_URL")
    if database_url is None:
        msg = "CODEBREAKERS_DATABASE_URL must be configured."
        raise SystemExit(msg)
    retention_days = int(os.environ.get("CODEBREAKERS_ANALYSIS_RETENTION_DAYS", "7"))
    engine = create_postgres_engine(database_url, pool_pre_ping=True)
    try:
        repository = SqlAlchemyAnalysisRepository(
            sessionmaker(engine), retention_days=retention_days
        )
        deleted = repository.purge_expired()
    finally:
        engine.dispose()
    print(f"Deleted {deleted} expired analysis job(s).")


if __name__ == "__main__":
    main()
