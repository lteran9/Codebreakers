"""Command-line retention cleanup and on-request deletion for operations.

    python -m codebreakers.infrastructure.persistence.retention
    python -m codebreakers.infrastructure.persistence.retention --delete-job ID

The first form deletes jobs older than the retention window (the scheduled
job). The second deletes specific jobs immediately, for data-deletion requests
(SECURITY.md). Neither prints database credentials or job content.
"""

import argparse
import os
from collections.abc import Sequence
from uuid import UUID

from sqlalchemy.orm import sessionmaker

from codebreakers.infrastructure.persistence.postgres import (
    SqlAlchemyAnalysisRepository,
    create_postgres_engine,
)


def main(argv: Sequence[str] | None = None) -> None:
    """Delete expired analysis jobs, or the jobs named with ``--delete-job``."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--delete-job",
        action="append",
        type=UUID,
        default=[],
        metavar="JOB_ID",
        help="Delete this analysis job now instead of purging expired jobs.",
    )
    args = parser.parse_args(argv)
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
        if args.delete_job:
            deleted = repository.delete(args.delete_job)
            print(f"Deleted {deleted} of {len(args.delete_job)} requested job(s).")
        else:
            deleted = repository.purge_expired()
            print(f"Deleted {deleted} expired analysis job(s).")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
