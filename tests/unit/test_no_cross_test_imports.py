"""Tests import from shared helper modules, never from other test modules.

Importing a test module runs its module-level code and drags its fixtures and
collection side effects into the importer, and it couples the two files: a
change to the imported test can break the importer for reasons that have
nothing to do with the behaviour under test. Shared setup belongs in a
`_`-prefixed helper module, which pytest does not collect.
"""

import ast
from pathlib import Path

TESTS_ROOT = Path(__file__).resolve().parents[1]


def _is_test_module(dotted: str) -> bool:
    last = dotted.split(".")[-1]
    return last.startswith("test_") or last.endswith("_test")


def _cross_test_imports() -> list[str]:
    offenders: list[str] = []
    for path in sorted(TESTS_ROOT.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        tree = ast.parse(path.read_text())
        rel = path.relative_to(TESTS_ROOT.parent)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and _is_test_module(node.module):
                names = ", ".join(a.name for a in node.names)
                offenders.append(f"{rel}:{node.lineno}: from {node.module} import {names}")
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if _is_test_module(alias.name):
                        offenders.append(f"{rel}:{node.lineno}: import {alias.name}")
    return offenders


def test_no_test_module_imports_another_test_module():
    offenders = _cross_test_imports()
    assert offenders == [], "tests must import shared helpers, not other tests:\n" + "\n".join(offenders)
