"""Import boundaries for executable experiment code."""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
IGNORED_PARTS = frozenset(
    {
        ".claude",
        ".git",
        ".jj",
        ".pytest_cache",
        ".ruff_cache",
        ".venv",
        ".worktrees",
        "__pycache__",
        "build",
        "dist",
        "docs",
        "htmlcov",
        "node_modules",
        "web-react",
    }
)


def _sources() -> list[Path]:
    return sorted(
        path
        for path in ROOT.rglob("*.py")
        if not any(part in IGNORED_PARTS for part in path.relative_to(ROOT).parts)
    )


def _docs_imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module is not None:
            if node.module.split(".", 1)[0] == "docs":
                violations.append(f"{path.relative_to(ROOT)}:{node.lineno}:{node.module}")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".", 1)[0] == "docs":
                    violations.append(f"{path.relative_to(ROOT)}:{node.lineno}:{alias.name}")
    return violations


def _mutates_sys_path(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    violations: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        target = node.func.value
        if (
            node.func.attr in {"append", "extend", "insert"}
            and isinstance(target, ast.Attribute)
            and target.attr == "path"
            and isinstance(target.value, ast.Name)
            and target.value.id == "sys"
        ):
            violations.append(f"{path.relative_to(ROOT)}:{node.lineno}:sys.path.{node.func.attr}")
    return violations


def test_non_docs_python_never_imports_docs() -> None:
    violations = [violation for path in _sources() for violation in _docs_imports(path)]
    assert violations == []


def test_importable_experiment_package_never_mutates_sys_path() -> None:
    violations = [
        violation
        for path in sorted((ROOT / "tools" / "experiments").rglob("*.py"))
        for violation in _mutates_sys_path(path)
    ]
    assert violations == []
