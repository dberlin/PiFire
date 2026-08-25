"""AST-based duplicate-test detection.

Two tests are duplicates when their decorators and their bodies produce the
same AST dump. Docstrings are stripped first (a differing docstring on an
otherwise identical body is still a duplicate), and bodies below
MIN_AST_NODES are ignored -- one-line tests collide harmlessly.
"""

import ast
import hashlib
from dataclasses import dataclass
from pathlib import Path

MIN_AST_NODES = 6


@dataclass(frozen=True)
class DuplicateGroup:
    digest: str
    members: tuple[tuple[str, int, str], ...]
    line_count: int


def _is_docstring(node: ast.stmt) -> bool:
    return isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)


def _digest(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    body = [statement for statement in node.body if not _is_docstring(statement)]
    decorators = "".join(sorted(ast.unparse(d) for d in node.decorator_list))
    module = ast.Module(body=body or [ast.Pass()], type_ignores=[])
    return hashlib.md5((decorators + ast.unparse(module)).encode()).hexdigest()[:16]


def find_duplicate_test_bodies(root: Path) -> list[DuplicateGroup]:
    seen: dict[str, list[tuple[str, int, str]]] = {}
    sizes: dict[str, int] = {}

    paths = sorted(set(root.rglob("test_*.py")) | set(root.rglob("*_test.py")))
    for path in paths:
        if "__pycache__" in path.parts:
            continue
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not node.name.startswith("test"):
                continue
            if sum(1 for _ in ast.walk(node)) < MIN_AST_NODES:
                continue
            digest = _digest(node)
            rel = str(path.relative_to(root.parent))
            seen.setdefault(digest, []).append((rel, node.lineno, node.name))
            sizes[digest] = (node.end_lineno or node.lineno) - node.lineno + 1

    return [
        DuplicateGroup(digest=digest, members=tuple(members), line_count=sizes[digest])
        for digest, members in sorted(seen.items())
        if len(members) > 1
    ]
