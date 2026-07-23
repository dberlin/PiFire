# Python AST Duplicate Detector Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a standalone standard-library CLI that finds structurally duplicated Python statement sequences across a directory tree.

**Architecture:** `dupes.py` discovers and parses Python files, extracts contiguous statement sequences from every AST statement-list, canonicalizes each sequence, and groups equal fingerprints. Canonicalization removes identifier/literal spelling and converts an inverted `if` with swapped branches to the same representation as its direct equivalent.

**Tech Stack:** Python 3 standard library (`argparse`, `ast`, `collections`, `pathlib`, `unittest`, `subprocess`).

## Global Constraints

- Create the executable at `/home/dannyb/sources/dupes.py`.
- Scan `*.py` recursively below `PATH`, defaulting to the invocation directory (`.`).
- Default `--min-statements` is exactly `10`; values must be positive integers.
- Do not use third-party packages.
- Detect duplicates across files and report paths plus inclusive source line ranges.
- Ignore identifier names and literal values in duplicate matching.
- Treat `if condition: A else: B` and `if not condition: B else: A` as equivalent.
- Skip `.git`, `__pycache__`, `.venv`, `venv`, `build`, and `dist` directories.
- Skip unreadable or syntactically invalid files while writing a diagnostic to stderr.

---

## File Structure

- Create: `/home/dannyb/sources/dupes.py` — CLI, discovery, AST body-list traversal, canonicalization, grouping, and deterministic text reporting.
- Create: `/home/dannyb/sources/test_dupes.py` — `unittest` integration tests that create temporary source trees and invoke the script as a subprocess.

### Task 1: CLI, Discovery, and Structural Matching

**Files:**

- Create: `/home/dannyb/sources/test_dupes.py`
- Create: `/home/dannyb/sources/dupes.py`

**Interfaces:**

- Consumes: filesystem path and `--min-statements N` from the command line.
- Produces: `0` on a completed scan; stdout groups in the form `Duplicate: <count> statements (<occurrences> occurrences)` followed by `<path>:<start>-<end>` lines.
- Produces: `discover_python_files(root: Path) -> list[Path]`, `iter_statement_lists(tree: ast.AST) -> Iterator[list[ast.stmt]]`, `canonical(node: ast.AST) -> tuple[object, ...]`, `find_duplicates(root: Path, min_statements: int) -> tuple[dict[tuple[object, ...], list[Location]], list[str]]`, and `main(argv: Sequence[str] | None = None) -> int`.

- [ ] **Step 1: Write the failing integration tests**

Create `/home/dannyb/sources/test_dupes.py` with this initial test suite:

```python
from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).with_name("dupes.py")


def assignments(prefix: str, start: int = 0, count: int = 10) -> str:
    return "\n".join(f"{prefix}{index} = {start + index}" for index in range(count))


class DuplicateDetectorTests(unittest.TestCase):
    def run_detector(self, directory: Path, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(SCRIPT), str(directory), *args],
            text=True,
            capture_output=True,
            check=False,
        )

    def test_reports_normalized_duplicate_sequences_across_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "first.py").write_text(
                "def alpha():\n" + "\n".join(f"    {line}" for line in assignments("a").splitlines()) + "\n",
                encoding="utf-8",
            )
            (root / "second.py").write_text(
                "def beta():\n" + "\n".join(f"    {line}" for line in assignments("b", 100).splitlines()) + "\n",
                encoding="utf-8",
            )

            result = self.run_detector(root)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("Duplicate: 10 statements (2 occurrences)", result.stdout)
            self.assertIn("first.py:2-11", result.stdout)
            self.assertIn("second.py:2-11", result.stdout)

    def test_honors_custom_minimum_statement_count(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name, prefix in (("first.py", "a"), ("second.py", "b")):
                (root / name).write_text(
                    "def work():\n" + "\n".join(f"    {line}" for line in assignments(prefix, count=3).splitlines()) + "\n",
                    encoding="utf-8",
                )

            default_result = self.run_detector(root)
            custom_result = self.run_detector(root, "--min-statements", "3")

            self.assertEqual(default_result.returncode, 0, default_result.stderr)
            self.assertIn("No duplicates found.", default_result.stdout)
            self.assertEqual(custom_result.returncode, 0, custom_result.stderr)
            self.assertIn("Duplicate: 3 statements (2 occurrences)", custom_result.stdout)

    def test_rejects_non_positive_minimum(self) -> None:
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--min-statements", "0"],
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("positive integer", result.stderr)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run:

```bash
python -m unittest -v /home/dannyb/sources/test_dupes.py
```

Expected: all three tests fail because `/home/dannyb/sources/dupes.py` does not exist.

- [ ] **Step 3: Write the minimal implementation**

Create `/home/dannyb/sources/dupes.py` with the complete initial implementation below. It intentionally canonicalizes the structure needed by the first test; Task 2 extends `canonical` for control-flow equivalence.

```python
#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ast
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Sequence

SKIPPED_DIRECTORIES = {".git", "__pycache__", ".venv", "venv", "build", "dist"}


@dataclass(frozen=True)
class Location:
    path: Path
    start_line: int
    end_line: int
    statement_count: int


def positive_integer(value: str) -> int:
    try:
        integer = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("must be a positive integer") from error
    if integer < 1:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return integer


def discover_python_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for path in root.rglob("*.py"):
        if any(part in SKIPPED_DIRECTORIES for part in path.parts):
            continue
        if path.is_file():
            files.append(path)
    return sorted(files)


def iter_statement_lists(tree: ast.AST) -> Iterator[list[ast.stmt]]:
    for node in ast.walk(tree):
        for _, value in ast.iter_fields(node):
            if isinstance(value, list) and all(isinstance(item, ast.stmt) for item in value):
                yield value


def canonical(node: ast.AST) -> tuple[object, ...]:
    if isinstance(node, ast.Name):
        return ("Name",)
    if isinstance(node, ast.arg):
        return ("arg", canonical(node.annotation) if node.annotation else None)
    if isinstance(node, ast.Constant):
        return ("Constant", type(node.value).__name__)
    if isinstance(node, ast.Attribute):
        return ("Attribute", canonical(node.value))
    if isinstance(node, ast.keyword):
        return ("keyword", node.arg is None, canonical(node.value))
    values: list[object] = [type(node).__name__]
    for field, value in ast.iter_fields(node):
        if field in {"ctx", "type_comment"}:
            continue
        if isinstance(value, ast.AST):
            values.append((field, canonical(value)))
        elif isinstance(value, list):
            values.append((field, tuple(canonical(item) if isinstance(item, ast.AST) else "<identifier>" if isinstance(item, str) else item for item in value)))
        elif isinstance(value, str):
            values.append((field, "<identifier>"))
        else:
            values.append((field, value))
    return tuple(values)


def find_duplicates(root: Path, min_statements: int) -> tuple[dict[tuple[object, ...], list[Location]], list[str]]:
    groups: dict[tuple[object, ...], list[Location]] = defaultdict(list)
    diagnostics: list[str] = []
    for path in discover_python_files(root):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, UnicodeDecodeError, SyntaxError) as error:
            diagnostics.append(f"Skipping {path}: {error}")
            continue
        for statements in iter_statement_lists(tree):
            for size in range(min_statements, len(statements) + 1):
                for start in range(len(statements) - size + 1):
                    sequence = statements[start : start + size]
                    fingerprint = tuple(canonical(statement) for statement in sequence)
                    groups[fingerprint].append(
                        Location(path, sequence[0].lineno, sequence[-1].end_lineno, size)
                    )
    return {fingerprint: locations for fingerprint, locations in groups.items() if len(locations) > 1}, diagnostics


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Find normalized Python AST duplicate statement sequences.")
    parser.add_argument("path", nargs="?", default=".", type=Path)
    parser.add_argument("--min-statements", type=positive_integer, default=10)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.path.is_dir():
        print(f"Path is not a directory: {args.path}", file=sys.stderr)
        return 2
    groups, diagnostics = find_duplicates(args.path, args.min_statements)
    for diagnostic in diagnostics:
        print(diagnostic, file=sys.stderr)
    if not groups:
        print("No duplicates found.")
        return 0
    ordered_groups = sorted(groups.values(), key=lambda locations: (-locations[0].statement_count, [(str(item.path), item.start_line) for item in locations]))
    for locations in ordered_groups:
        count = locations[0].statement_count
        locations = sorted(locations, key=lambda item: (str(item.path), item.start_line, item.end_line))
        print(f"Duplicate: {count} statements ({len(locations)} occurrences)")
        for location in locations:
            print(f"  {location.path}:{location.start_line}-{location.end_line}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run the focused tests to verify they pass**

Run:

```bash
python -m unittest -v /home/dannyb/sources/test_dupes.py
```

Expected: all three tests pass.

- [ ] **Step 5: Commit the completed baseline**

```bash
git -C /home/dannyb/sources/PiFire status --short
git -C /home/dannyb/sources/PiFire add docs/superpowers/plans/2026-07-21-python-ast-duplicate-detector.md
git -C /home/dannyb/sources/PiFire commit -m "docs: plan AST duplicate detector"
```

Expected: only the plan belongs to the PiFire repository; the standalone utility and its tests remain beside it in `/home/dannyb/sources`.

### Task 2: Equivalent Conditional-Flow and Safe Comparison Normalization

**Files:**

- Modify: `/home/dannyb/sources/test_dupes.py`
- Modify: `/home/dannyb/sources/dupes.py`

**Interfaces:**

- Consumes: the Task 1 `canonical(node)` function and subprocess test helper.
- Produces: equivalent canonical forms for `if condition: A else: B` and `if not condition: B else: A`, plus equality/inequality tests whose name-or-constant operands are reversed; stderr diagnostics for malformed Python files without failing the scan.

- [ ] **Step 1: Add failing tests for inversion equivalence and syntax-error recovery**

Append these methods to `DuplicateDetectorTests` in `/home/dannyb/sources/test_dupes.py`:

```python
    def test_matches_inverted_if_statements_with_swapped_branches(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            direct = "\n".join(
                f"if flag{index}:\n    left{index} = {index}\nelse:\n    right{index} = {index + 10}"
                for index in range(10)
            )
            inverted = "\n".join(
                f"if not other{index}:\n    swap_right{index} = {index + 100}\nelse:\n    swap_left{index} = {index + 200}"
                for index in range(10)
            )
            (root / "direct.py").write_text(direct + "\n", encoding="utf-8")
            (root / "inverted.py").write_text(inverted + "\n", encoding="utf-8")

            result = self.run_detector(root)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("Duplicate: 10 statements (2 occurrences)", result.stdout)
            self.assertIn("direct.py:1-40", result.stdout)
            self.assertIn("inverted.py:1-40", result.stdout)

    def test_matches_safe_reversed_equality_tests(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            direct = "\n".join(
                f"if value{index} == {index}:\n    yes{index} = {index}\nelse:\n    no{index} = {index + 10}"
                for index in range(10)
            )
            reversed_test = "\n".join(
                f"if {index + 100} == other{index}:\n    branch_yes{index} = {index + 200}\nelse:\n    branch_no{index} = {index + 300}"
                for index in range(10)
            )
            (root / "direct.py").write_text(direct + "\n", encoding="utf-8")
            (root / "reversed.py").write_text(reversed_test + "\n", encoding="utf-8")

            result = self.run_detector(root)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("Duplicate: 10 statements (2 occurrences)", result.stdout)
            self.assertIn("direct.py:1-40", result.stdout)
            self.assertIn("reversed.py:1-40", result.stdout)

    def test_matches_safe_reordered_boolean_tests(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            direct = "\n".join(
                f"if flag{index} and True:\n    yes{index} = {index}\nelse:\n    no{index} = {index + 10}"
                for index in range(10)
            )
            reordered = "\n".join(
                f"if True and other{index}:\n    branch_yes{index} = {index + 100}\nelse:\n    branch_no{index} = {index + 200}"
                for index in range(10)
            )
            (root / "direct.py").write_text(direct + "\n", encoding="utf-8")
            (root / "reordered.py").write_text(reordered + "\n", encoding="utf-8")

            result = self.run_detector(root)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("Duplicate: 10 statements (2 occurrences)", result.stdout)
            self.assertIn("direct.py:1-40", result.stdout)
            self.assertIn("reordered.py:1-40", result.stdout)

    def test_skips_malformed_python_and_reports_the_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "broken.py").write_text("def broken(:\n", encoding="utf-8")
            for name, prefix in (("first.py", "a"), ("second.py", "b")):
                (root / name).write_text(
                    "def work():\n" + "\n".join(f"    {line}" for line in assignments(prefix).splitlines()) + "\n",
                    encoding="utf-8",
                )

            result = self.run_detector(root)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("Skipping", result.stderr)
            self.assertIn("broken.py", result.stderr)
            self.assertIn("Duplicate: 10 statements (2 occurrences)", result.stdout)
```

- [ ] **Step 2: Run the new tests to verify they fail for the expected missing behavior**

Run:

```bash
python -m unittest -v /home/dannyb/sources/test_dupes.py
```

Expected: the inversion, reversed-equality, and reordered-boolean tests fail because the canonical forms still differ; the malformed-file test already passes and protects the existing behavior.

- [ ] **Step 3: Extend canonicalization with conditional inversion and safe equality handling**

Add these helpers before `canonical` in `/home/dannyb/sources/dupes.py`, then add the shown cases before the existing `ast.Name` case. Keep the remainder of Task 1's generic canonicalization unchanged:

```python
def canonical_statements(statements: list[ast.stmt]) -> tuple[object, ...]:
    return tuple(canonical(statement) for statement in statements)


def is_reorderable(node: ast.AST) -> bool:
    return isinstance(node, (ast.Name, ast.Constant))


def canonical_if(node: ast.If) -> tuple[object, ...]:
    test = node.test
    body = node.body
    orelse = node.orelse
    while isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not):
        test = test.operand
        body, orelse = orelse, body
    return (
        "If",
        ("test", canonical(test)),
        ("body", canonical_statements(body)),
        ("orelse", canonical_statements(orelse)),
    )


def canonical(node: ast.AST) -> tuple[object, ...]:
    if isinstance(node, ast.If):
        return canonical_if(node)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        operand = node.operand
        if isinstance(operand, ast.UnaryOp) and isinstance(operand.op, ast.Not):
            return canonical(operand.operand)
    if (
        isinstance(node, ast.Compare)
        and len(node.ops) == len(node.comparators) == 1
        and isinstance(node.ops[0], (ast.Eq, ast.NotEq))
        and is_reorderable(node.left)
        and is_reorderable(node.comparators[0])
    ):
        operands = sorted((canonical(node.left), canonical(node.comparators[0])), key=repr)
        return ("Compare", type(node.ops[0]).__name__, tuple(operands))
    if isinstance(node, ast.BoolOp) and all(is_reorderable(value) for value in node.values):
        values = sorted((canonical(value) for value in node.values), key=repr)
        return ("BoolOp", type(node.op).__name__, tuple(values))
    if isinstance(node, ast.Name):
        return ("Name",)
```

- [ ] **Step 4: Run the full standalone test suite to verify it passes**

Run:

```bash
python -m unittest -v /home/dannyb/sources/test_dupes.py
```

Expected: all seven tests pass with no tracebacks.

- [ ] **Step 5: Perform a manual CLI smoke test against the current project**

Run:

```bash
cd /home/dannyb/sources/PiFire && python /home/dannyb/sources/dupes.py . --min-statements 10
```

Expected: exit code `0`; stdout contains either duplicate groups or `No duplicates found.`; stderr contains only readable skipped-file diagnostics, if any.

- [ ] **Step 6: Commit the implementation where version control is available**

```bash
git -C /home/dannyb/sources status --short
```

Expected: review the untracked `/home/dannyb/sources/dupes.py` and `/home/dannyb/sources/test_dupes.py`; add and commit them only if `/home/dannyb/sources` is itself a Git repository. Otherwise, leave the requested deliverables in place and report their paths.
