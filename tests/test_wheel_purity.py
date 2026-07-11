"""Wheel-purity gate: prove the extraction left no `utils.*` coupling behind.

Two independent checks:

1. **Source scan** — walk every module under ``src/medecon_verify`` and assert no
   ``utils`` reference (import statement, dotted access, or stray mention)
   survives. The plan's headline claim is that the built wheel imports every
   module with zero ``utils.*`` references (Part 1.1, C-6).

2. **Import scan** — import every ``medecon_verify`` module and assert that doing
   so never pulled a top-level ``utils`` package (or any ``utils.*`` submodule)
   into ``sys.modules``. A surviving lazy ``from utils import ...`` would only
   surface at call time; importing the module proves the top-level import graph
   is clean, and the source scan covers the lazy paths.
"""
from __future__ import annotations

import importlib
import re
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_PKG_ROOT = _REPO_ROOT / "src" / "medecon_verify"

# Any standalone `utils` token: `from utils`, `import utils`, `utils.dateparse`,
# etc. Word-boundaried so it does not match `importlib.util` (no trailing "s").
_UTILS_TOKEN = re.compile(r"\butils\b")


def _module_files() -> list[Path]:
    return sorted(_PKG_ROOT.rglob("*.py"))


def _module_names() -> list[str]:
    names = []
    for path in _module_files():
        rel = path.relative_to(_PKG_ROOT.parent).with_suffix("")
        parts = list(rel.parts)
        if parts[-1] == "__init__":
            parts = parts[:-1]
        names.append(".".join(parts))
    return names


def test_modules_present() -> None:
    files = _module_files()
    assert files, "no medecon_verify modules found under src/"


@pytest.mark.parametrize("path", _module_files(), ids=lambda p: p.name)
def test_no_utils_reference_survives_in_source(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    offenders = [
        f"{path.name}:{i}: {line.strip()}"
        for i, line in enumerate(text.splitlines(), 1)
        if _UTILS_TOKEN.search(line)
    ]
    assert not offenders, (
        "surviving `utils` reference(s) in extracted module:\n" + "\n".join(offenders)
    )


def test_importing_every_module_pulls_no_utils_into_sys_modules() -> None:
    # Import every module in the package (this executes their top-level import
    # graph). `medecon_verify.cli` imports click, which is available in the test
    # environment; if it were absent the module still imports (click degrades).
    #
    # `riskadj.engine` loads its HCC reference CSVs at import; those are bundled by
    # task 0.2, so tolerate a missing-data error here without masking a `utils`
    # leak — the source-scan test above is the authoritative no-utils gate and it
    # covers every module unconditionally.
    for name in _module_names():
        try:
            importlib.import_module(name)
        except FileNotFoundError:
            pass

    tainted = [m for m in sys.modules if m == "utils" or m.startswith("utils.")]
    assert not tainted, f"`utils` leaked into sys.modules after import: {tainted}"
