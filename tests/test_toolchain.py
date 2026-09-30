"""Keep the formatter used locally, in pre-commit, and in CI on one version."""

import re
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.unit
def test_ruff_pin_matches_pre_commit_hook() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    dev_deps = pyproject["project"]["optional-dependencies"]["dev"]
    ruff_pins = [dep for dep in dev_deps if re.match(r"ruff\b", dep)]
    assert len(ruff_pins) == 1
    assert ruff_pins[0].startswith("ruff=="), "Pin ruff exactly in pyproject.toml."
    pinned = ruff_pins[0].removeprefix("ruff==")

    config = (ROOT / ".pre-commit-config.yaml").read_text(encoding="utf-8")
    hook = re.search(r"ruff-pre-commit\s*\n\s*rev:\s*v?(\S+)", config)
    assert hook is not None, "ruff-pre-commit hook not found."
    assert hook.group(1) == pinned
