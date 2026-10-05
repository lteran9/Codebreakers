"""Keep tool versions and dependency lock files consistent across environments."""

import re
import tomllib
from pathlib import Path

import pytest
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
from packaging.version import Version

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


def _locked_versions(lock_file: Path) -> dict[str, Version]:
    text = lock_file.read_text(encoding="utf-8")
    pins = re.findall(r"^([A-Za-z0-9_.-]+)==(\S+) [\\]$", text, flags=re.MULTILINE)
    return {canonicalize_name(name): Version(version) for name, version in pins}


@pytest.mark.unit
def test_runtime_lock_satisfies_every_declared_dependency() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    locked = _locked_versions(ROOT / "requirements" / "runtime.txt")

    for declared in map(Requirement, pyproject["project"]["dependencies"]):
        name = canonicalize_name(declared.name)
        assert name in locked, f"{name} is missing from requirements/runtime.txt"
        assert locked[name] in declared.specifier, (
            f"{name}=={locked[name]} does not satisfy {declared}; run `make lock`."
        )


@pytest.mark.unit
@pytest.mark.parametrize("lock_name", ["runtime.txt", "build.txt"])
def test_lock_files_pin_hashes_for_every_package(lock_name: str) -> None:
    text = (ROOT / "requirements" / lock_name).read_text(encoding="utf-8")
    blocks = re.split(r"^(?=[A-Za-z0-9_.-]+==)", text, flags=re.MULTILINE)[1:]
    assert blocks
    for block in blocks:
        assert "--hash=sha256:" in block, f"unhashed pin: {block.splitlines()[0]}"
