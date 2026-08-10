"""Import boundaries for executable experiment code."""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

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
        "htmlcov",
        "node_modules",
        "web-react",
    }
)
SYS_PATH_MUTATORS = frozenset({"append", "clear", "extend", "insert", "pop", "remove", "reverse", "sort"})
AUGMENTED_OPERATOR_TOKENS: dict[type[ast.operator], str] = {
    ast.Add: "+=",
    ast.Sub: "-=",
    ast.Mult: "*=",
    ast.MatMult: "@=",
    ast.Div: "/=",
    ast.Mod: "%=",
    ast.Pow: "**=",
    ast.LShift: "<<=",
    ast.RShift: ">>=",
    ast.BitOr: "|=",
    ast.BitXor: "^=",
    ast.BitAnd: "&=",
    ast.FloorDiv: "//=",
}


def _sources() -> list[Path]:
    return sorted(
        path
        for path in ROOT.rglob("*.py")
        if path.relative_to(ROOT).parts[0] != "docs"
        and not any(part in IGNORED_PARTS for part in path.relative_to(ROOT).parts)
    )


def _docs_imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module is not None:
            if node.level == 0 and node.module.split(".", 1)[0] == "docs":
                violations.append(f"{path.relative_to(ROOT)}:{node.lineno}:{node.module}")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".", 1)[0] == "docs":
                    violations.append(f"{path.relative_to(ROOT)}:{node.lineno}:{alias.name}")
    return violations


def _is_sys_path(node: ast.expr) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "path"
        and isinstance(node.value, ast.Name)
        and node.value.id == "sys"
    )


def _sys_path_target(node: ast.expr) -> str | None:
    if _is_sys_path(node):
        return "sys.path"
    if isinstance(node, ast.Subscript) and _is_sys_path(node.value):
        return ast.unparse(node)
    return None


def _mutates_sys_path(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    relative_path = path.relative_to(ROOT)
    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr in SYS_PATH_MUTATORS and _is_sys_path(node.func.value):
                violations.append(f"{relative_path}:{node.lineno}:sys.path.{node.func.attr}")
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if action := _sys_path_target(target):
                    violations.append(f"{relative_path}:{node.lineno}:{action} =")
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            if action := _sys_path_target(node.target):
                violations.append(f"{relative_path}:{node.lineno}:{action} =")
        elif isinstance(node, ast.AugAssign):
            if _sys_path_target(node.target):
                action = f"{ast.unparse(node.target)} {AUGMENTED_OPERATOR_TOKENS[type(node.op)]}"
                violations.append(f"{relative_path}:{node.lineno}:{action}")
        elif isinstance(node, ast.Delete):
            for target in node.targets:
                if action := _sys_path_target(target):
                    violations.append(f"{relative_path}:{node.lineno}:del {action}")
    return violations


def _write_probe(root: Path, relative_path: str, source: str) -> Path:
    path = root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")
    return path


@pytest.mark.parametrize(
    ("method", "arguments"),
    [
        ("append", '"probe"'),
        ("clear", ""),
        ("extend", '["probe"]'),
        ("insert", '0, "probe"'),
        ("pop", ""),
        ("remove", '"probe"'),
        ("reverse", ""),
        ("sort", ""),
    ],
)
def test_direct_sys_path_mutator_is_reported(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    method: str,
    arguments: str,
) -> None:
    monkeypatch.setattr(sys.modules[__name__], "ROOT", tmp_path)
    path = _write_probe(tmp_path, "probe.py", f"import sys\nsys.path.{method}({arguments})\n")

    assert _mutates_sys_path(path) == [f"probe.py:2:sys.path.{method}"]


@pytest.mark.parametrize(
    ("statement", "action"),
    [
        ("sys.path = []", "sys.path ="),
        ("sys.path: list[str] = []", "sys.path ="),
        ("sys.path += []", "sys.path +="),
        ("sys.path -= []", "sys.path -="),
        ("sys.path *= 2", "sys.path *="),
        ("sys.path @= []", "sys.path @="),
        ("sys.path /= 2", "sys.path /="),
        ("sys.path %= 2", "sys.path %="),
        ("sys.path **= 2", "sys.path **="),
        ("sys.path <<= 2", "sys.path <<="),
        ("sys.path >>= 2", "sys.path >>="),
        ("sys.path |= []", "sys.path |="),
        ("sys.path ^= []", "sys.path ^="),
        ("sys.path &= []", "sys.path &="),
        ("sys.path //= 2", "sys.path //="),
        ('sys.path[(index := 0)] += ["probe"]', "sys.path[(index := 0)] +="),
        ('sys.path[0] = "probe"', "sys.path[0] ="),
        ('sys.path[:] = ["probe"]', "sys.path[:] ="),
    ],
)
def test_direct_sys_path_assignment_is_reported(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    statement: str,
    action: str,
) -> None:
    monkeypatch.setattr(sys.modules[__name__], "ROOT", tmp_path)
    path = _write_probe(tmp_path, "probe.py", f"import sys\n{statement}\n")

    assert _mutates_sys_path(path) == [f"probe.py:2:{action}"]


@pytest.mark.parametrize(
    ("statement", "action"),
    [
        ("del sys.path", "del sys.path"),
        ("del sys.path[0]", "del sys.path[0]"),
        ("del sys.path[:]", "del sys.path[:]"),
    ],
)
def test_direct_sys_path_deletion_is_reported(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    statement: str,
    action: str,
) -> None:
    monkeypatch.setattr(sys.modules[__name__], "ROOT", tmp_path)
    path = _write_probe(tmp_path, "probe.py", f"import sys\n{statement}\n")

    assert _mutates_sys_path(path) == [f"probe.py:2:{action}"]


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        (
            "from docs.superpowers.experiments import controller_matrix\n",
            ["probe.py:1:docs.superpowers.experiments"],
        ),
        ("import docs.superpowers.experiments\n", ["probe.py:1:docs.superpowers.experiments"]),
        ("from .docs import helper\n", []),
        ("from ..docs.helpers import helper\n", []),
    ],
)
def test_docs_import_boundary_distinguishes_absolute_and_relative_imports(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    source: str,
    expected: list[str],
) -> None:
    monkeypatch.setattr(sys.modules[__name__], "ROOT", tmp_path)
    path = _write_probe(tmp_path, "probe.py", source)

    assert _docs_imports(path) == expected


@pytest.mark.parametrize(
    ("relative_path", "expected"),
    [
        ("docs/probe.py", []),
        ("component/docs/probe.py", ["component/docs/probe.py:1:docs.superpowers.experiments"]),
    ],
)
def test_only_the_root_docs_tree_is_excluded_from_source_scan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    relative_path: str,
    expected: list[str],
) -> None:
    monkeypatch.setattr(sys.modules[__name__], "ROOT", tmp_path)
    _write_probe(tmp_path, relative_path, "import docs.superpowers.experiments\n")

    violations = [violation for path in _sources() for violation in _docs_imports(path)]

    assert violations == expected


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
