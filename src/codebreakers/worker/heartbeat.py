"""File heartbeat for liveness probes of processes without an HTTP port.

The worker and relay touch the file once per loop iteration. A container
health check runs ``python -m codebreakers.worker.heartbeat PATH --max-age N``
and fails when the file is missing or older than ``N`` seconds. Set ``N``
above the analysis time budget so a long job is not mistaken for a hang.
"""

import argparse
import logging
import sys
import time
from collections.abc import Callable, Sequence
from pathlib import Path

logger = logging.getLogger("codebreakers.worker")


class Heartbeat:
    """Touch a file to record that a processing loop is still advancing."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._failing = False

    def beat(self) -> None:
        """Update the file's modification time, logging only the first failure."""
        try:
            self._path.touch()
        except OSError as err:
            if not self._failing:
                logger.warning("heartbeat_error error=%s", type(err).__name__)
            self._failing = True
        else:
            self._failing = False


def is_fresh(
    path: Path, max_age: float, clock: Callable[[], float] = time.time
) -> bool:
    """Return whether ``path`` exists and was touched within ``max_age`` seconds."""
    try:
        modified = path.stat().st_mtime
    except OSError:
        return False
    return clock() - modified <= max_age


def main(argv: Sequence[str] | None = None) -> int:
    """Exit 0 when the heartbeat is fresh and 1 otherwise."""
    parser = argparse.ArgumentParser(
        prog="python -m codebreakers.worker.heartbeat",
        description="Check that a worker or relay heartbeat file is fresh.",
    )
    parser.add_argument("path", type=Path, help="Heartbeat file to check.")
    parser.add_argument(
        "--max-age",
        type=float,
        default=120.0,
        help="Maximum age in seconds (default: 120).",
    )
    args = parser.parse_args(argv)
    if is_fresh(args.path, args.max_age):
        return 0
    print(f"Heartbeat {args.path} is missing or stale.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
