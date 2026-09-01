"""Tests import from shared helper modules, never from other test modules.

Importing a test module runs its module-level code and drags its fixtures and
collection side effects into the importer, and it couples the two files: a
change to the imported test can break the importer for reasons that have
nothing to do with the behaviour under test. Shared setup belongs in a
`_`-prefixed helper module, which pytest does not collect.
"""

import ast
import importlib.util
from pathlib import Path

import pytest

TESTS_ROOT = Path(__file__).resolve().parents[1]


def _is_test_module(dotted: str) -> bool:
    last = dotted.split(".")[-1]
    if last.startswith("_"):
        return False
    return last.startswith("test_") or last.endswith("_test")


def _module_name(path: Path, root: Path) -> tuple[str, ...]:
    relative = path.relative_to(root)
    if path.name == "__init__.py":
        return relative.parent.parts
    return relative.with_suffix("").parts


def _test_modules(paths: list[Path], root: Path) -> set[str]:
    modules: set[str] = set()
    for path in paths:
        parts = _module_name(path, root)
        for index, part in enumerate(parts):
            if not _is_test_module(part):
                continue
            dotted = ".".join(parts[: index + 1])
            modules.add(dotted)
            modules.add(f"{root.name}.{dotted}")
    return modules


def _resolved_from_module(path: Path, root: Path, node: ast.ImportFrom) -> str:
    module = node.module.split(".") if node.module else []
    if not node.level:
        return ".".join(module)

    package = list(_module_name(path, root))
    if path.name != "__init__.py":
        package.pop()
    ascend = node.level - 1
    if ascend:
        package = package[:-ascend] if ascend <= len(package) else []
    return ".".join([*package, *module])


def _targets_test(target: str, test_modules: set[str]) -> bool:
    return any(
        target == module or target.startswith(f"{module}.")
        for module in test_modules
    )


def _importlib_aliases(tree: ast.AST) -> tuple[set[str], set[str]]:
    module_aliases: set[str] = set()
    function_aliases: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "importlib":
                    module_aliases.add(alias.asname or "importlib")
                elif alias.name.startswith("importlib.") and alias.asname is None:
                    module_aliases.add("importlib")
        elif (
            isinstance(node, ast.ImportFrom)
            and node.level == 0
            and node.module == "importlib"
        ):
            for alias in node.names:
                if alias.name == "*":
                    function_aliases.add("import_module")
                elif alias.name == "import_module":
                    function_aliases.add(alias.asname or alias.name)
    return module_aliases, function_aliases


def _call_argument(
    node: ast.Call,
    position: int,
    keyword: str,
) -> ast.expr | None:
    if len(node.args) > position:
        return node.args[position]
    return next(
        (item.value for item in node.keywords if item.arg == keyword),
        None,
    )


def _dynamic_target(
    node: ast.Call,
    module_aliases: set[str],
    function_aliases: set[str],
) -> str | None:
    function = node.func
    if isinstance(function, ast.Attribute):
        if (
            function.attr != "import_module"
            or not isinstance(function.value, ast.Name)
            or function.value.id not in module_aliases
        ):
            return None
    elif not isinstance(function, ast.Name) or function.id not in function_aliases:
        return None

    name_node = _call_argument(node, 0, "name")
    if not isinstance(name_node, ast.Constant) or not isinstance(name_node.value, str):
        return None
    name = name_node.value
    if not name.startswith("."):
        return name

    package_node = _call_argument(node, 1, "package")
    if (
        not isinstance(package_node, ast.Constant)
        or not isinstance(package_node.value, str)
    ):
        return None
    try:
        return importlib.util.resolve_name(name, package_node.value)
    except ImportError:
        return None


def _static_targets(path: Path, root: Path, node: ast.AST) -> set[str]:
    if isinstance(node, ast.Import):
        return {alias.name for alias in node.names}
    if not isinstance(node, ast.ImportFrom):
        return set()

    module = _resolved_from_module(path, root, node)
    targets = {module} if module else set()
    targets.update(
        ".".join(part for part in (module, alias.name) if part)
        for alias in node.names
    )
    return targets


def _file_offenders(
    path: Path,
    root: Path,
    tree: ast.AST,
    test_modules: set[str],
) -> list[tuple[int, int, str]]:
    module_aliases, function_aliases = _importlib_aliases(tree)
    offenders: list[tuple[int, int, str]] = []
    for node in ast.walk(tree):
        targets = _static_targets(path, root, node)
        if targets and any(_targets_test(target, test_modules) for target in targets):
            offenders.append((node.lineno, node.col_offset, ast.unparse(node)))
        elif isinstance(node, ast.Call):
            target = _dynamic_target(node, module_aliases, function_aliases)
            if target is not None and _targets_test(target, test_modules):
                offenders.append((node.lineno, node.col_offset, ast.unparse(node)))
    return offenders


def _cross_test_imports(root: Path = TESTS_ROOT) -> list[str]:
    paths = [
        path
        for path in sorted(root.rglob("*.py"))
        if "__pycache__" not in path.parts
    ]
    test_modules = _test_modules(paths, root)
    offenders: list[tuple[str, int, int, str]] = []

    for path in paths:
        tree = ast.parse(path.read_text())
        rel = str(path.relative_to(root))
        offenders.extend(
            (rel, lineno, column, source)
            for lineno, column, source in _file_offenders(
                path,
                root,
                tree,
                test_modules,
            )
        )

    return [
        f"{rel}:{lineno}: {source}"
        for rel, lineno, _column, source in sorted(offenders)
    ]


@pytest.mark.parametrize(
    "source",
    [
        "from package import test_module\n",
        "from package import test_module as shared\n",
        "import package.test_module\n",
        "import package.test_module as shared\n",
        "from . import test_module\n",
        "from . import test_module as shared\n",
        "from .test_module import shared_value\n",
        "from .test_module import shared_value as value\n",
    ],
)
def test_cross_test_imports_detects_static_import_forms(
    tmp_path: Path, source: str
) -> None:
    package = tmp_path / "package"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "test_module.py").write_text("shared_value = 1\n")
    (package / "test_importer.py").write_text(source)

    offenders = _cross_test_imports(tmp_path)

    assert len(offenders) == 1
    assert "test_module" in offenders[0]


@pytest.mark.parametrize(
    "source",
    [
        'import importlib\nimportlib.import_module("package.test_module")\n',
        'import importlib as loader\nloader.import_module("package.test_module")\n',
        'import importlib\nimportlib.import_module(name="package.test_module")\n',
        'import importlib.util\nimportlib.import_module("package.test_module")\n',
        (
            "from importlib import import_module as load\n"
            'load("package.test_module")\n'
        ),
        (
            "from importlib import import_module as load\n"
            'load(name="package.test_module")\n'
        ),
    ],
)
def test_cross_test_imports_detects_literal_dynamic_imports(
    tmp_path: Path, source: str
) -> None:
    package = tmp_path / "package"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "test_module.py").write_text("")
    (package / "test_importer.py").write_text(source)

    offenders = _cross_test_imports(tmp_path)

    assert len(offenders) == 1
    assert "package.test_module" in offenders[0]


@pytest.mark.parametrize(
    "source",
    [
        'import importlib\nimportlib.import_module(".test_module", "package")\n',
        (
            "import importlib\n"
            'importlib.import_module(name=".test_module", package="package")\n'
        ),
        (
            "import importlib\n"
            'importlib.import_module("..test_module", package="package.subpackage")\n'
        ),
        (
            "from importlib import import_module as load\n"
            'load(name="..test_module", package="package.subpackage")\n'
        ),
    ],
)
def test_cross_test_imports_resolves_relative_dynamic_imports(
    tmp_path: Path, source: str
) -> None:
    package = tmp_path / "package"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "test_module.py").write_text("")
    (package / "subpackage").mkdir()
    (package / "subpackage" / "__init__.py").write_text("")
    (package / "test_importer.py").write_text(source)

    offenders = _cross_test_imports(tmp_path)

    assert len(offenders) == 1
    assert "test_module" in offenders[0]


@pytest.mark.parametrize(
    "source",
    [
        (
            "import importlib as loader\n"
            "def helper(loader):\n"
            '    loader.import_module("package.test_module")\n'
        ),
        (
            "from importlib import import_module as load\n"
            "load = object()\n"
            'load("package.test_module")\n'
        ),
        (
            'loader.import_module("package.test_module")\n'
            "if False:\n"
            "    import importlib as loader\n"
        ),
        (
            'load("package.test_module")\n'
            "if False:\n"
            "    from importlib import import_module as load\n"
        ),
    ],
)
def test_cross_test_imports_reserves_importlib_aliases_for_whole_file(
    tmp_path: Path, source: str
) -> None:
    package = tmp_path / "package"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "test_module.py").write_text("")
    (package / "test_importer.py").write_text(source)

    offenders = _cross_test_imports(tmp_path)

    assert len(offenders) == 1
    assert "package.test_module" in offenders[0]

def test_cross_test_imports_reserves_wildcard_import_module_for_whole_file(
    tmp_path: Path,
) -> None:
    package = tmp_path / "package"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "test_module.py").write_text("")
    (package / "test_importer.py").write_text(
        'import_module("package.test_module")\n'
        "if False:\n"
        "    from importlib import *\n"
    )

    assert _cross_test_imports(tmp_path) == [
        "package/test_importer.py:1: import_module('package.test_module')"
    ]


def test_cross_test_imports_ignores_names_never_imported_from_importlib(
    tmp_path: Path,
) -> None:
    package = tmp_path / "package"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "test_module.py").write_text("")
    (package / "test_importer.py").write_text(
        "import unrelated as loader\n"
        'loader.import_module("package.test_module")\n'
        "from unrelated import import_module as load\n"
        'load("package.test_module")\n'
    )

    assert _cross_test_imports(tmp_path) == []


@pytest.mark.parametrize(
    "source",
    [
        "import package.test_package.child\n",
        "from package.test_package.child import shared_value\n",
        (
            "import importlib\n"
            'importlib.import_module("package.test_package.child")\n'
        ),
        (
            "from importlib import import_module\n"
            'import_module(".child", package="package.test_package")\n'
        ),
    ],
)
def test_cross_test_imports_detects_descendants_of_test_packages(
    tmp_path: Path, source: str
) -> None:
    package = tmp_path / "package"
    package.mkdir()
    (package / "__init__.py").write_text("")
    test_package = package / "test_package"
    test_package.mkdir()
    (test_package / "__init__.py").write_text("")
    (test_package / "child.py").write_text("shared_value = 1\n")
    (package / "test_importer.py").write_text(source)

    offenders = _cross_test_imports(tmp_path)

    assert len(offenders) == 1
    assert "test_package" in offenders[0]


def test_cross_test_imports_detects_descendants_of_namespace_test_directories(
    tmp_path: Path,
) -> None:
    package = tmp_path / "package"
    package.mkdir()
    (package / "__init__.py").write_text("")
    test_package = package / "test_namespace"
    test_package.mkdir()
    (test_package / "child.py").write_text("shared_value = 1\n")
    (package / "test_importer.py").write_text(
        "from package.test_namespace.child import shared_value\n"
    )

    assert _cross_test_imports(tmp_path) == [
        "package/test_importer.py:1: "
        "from package.test_namespace.child import shared_value",
    ]


def test_cross_test_imports_allows_non_module_test_names_and_string_mentions(
    tmp_path: Path,
) -> None:
    package = tmp_path / "package"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "regular_module.py").write_text(
        "def test_function():\n"
        "    pass\n\n"
        "class TestClass:\n"
        "    pass\n"
    )
    (package / "_test_helper.py").write_text("")
    (package / "_helper_test.py").write_text("")
    (package / "test_importer.py").write_text(
        "from package.regular_module import test_function, TestClass\n"
        "from package import regular_module\n"
        "from package import _test_helper, _helper_test\n"
        "import package._test_helper as helper\n"
        "import package._helper_test as other_helper\n"
        'mentioned = "package.test_module"\n'
    )

    assert _cross_test_imports(tmp_path) == []


def test_no_test_module_imports_another_test_module():
    offenders = _cross_test_imports()
    assert offenders == [], "tests must import shared helpers, not other tests:\n" + "\n".join(offenders)
