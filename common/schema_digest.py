"""A stable digest of the SHAPE a pydantic model persists.

One entry per leaf, each `path: annotation [constraints]`:

  * **Path** uses the field's ALIAS where it has one, because the alias is what
    is on disk -- `platform.system.1WIRE`, not `platform.system.one_wire`.
  * **Annotation** is included, so a retype cannot keep a path and pass
    silently. Union members are sorted, since a scalar union's order carries
    no meaning; a union OF MODELS is walked per member and keyed by INDEX,
    because `union_mode="left_to_right"` makes that order decide which member
    a value matches.
  * **Constraints** are the field's `metadata` -- `Ge`, `Le`,
    `StringConstraints` and the rest. A tightened bound can reject data an
    install already holds, which is the definition of needing a migration.

Defaults and descriptions are EXCLUDED. Neither can invalidate stored data,
and a digest that moved on one would be updated without being read.

Two things it does not distinguish, both by the same reasoning: a field
gaining a default (every stored blob already carries a value), and a change
to `union_mode`, which pydantic folds into the FieldInfo rather than into
`metadata`.

Run against a model to see the entries behind a digest:

    uv run python -m common.schema_digest common.settings_schema:SettingsSchema
"""

import hashlib
from types import UnionType
from typing import Any, Union, get_args, get_origin

from pydantic import BaseModel


def _is_model(annotation: Any) -> bool:
    return isinstance(annotation, type) and issubclass(annotation, BaseModel)


def _annotation_repr(annotation: Any) -> str:
    if _is_model(annotation):
        return "<model>"
    origin = get_origin(annotation)
    if origin is Union or origin is UnionType:
        return " | ".join(sorted(_annotation_repr(arg) for arg in get_args(annotation)))
    args = get_args(annotation)
    if args:
        name = getattr(origin, "__name__", str(origin))
        return f"{name}[{', '.join(_annotation_repr(arg) for arg in args)}]"
    if isinstance(annotation, type):
        return annotation.__name__
    return str(annotation)


def _constraints_repr(metadata) -> str:
    return ", ".join(sorted(repr(item) for item in metadata))


def shape_entries(model: type[BaseModel], prefix: tuple[str, ...] = ()) -> list[str]:
    """One `path: annotation [constraints]` line per leaf of `model`."""
    entries: list[str] = []
    for name, info in model.model_fields.items():
        path = prefix + (info.alias or name,)
        annotation = info.annotation

        if _is_model(annotation):
            entries.extend(shape_entries(annotation, path))
            continue

        origin = get_origin(annotation)
        members = get_args(annotation) if origin is Union or origin is UnionType else ()
        if members and all(_is_model(member) for member in members):
            for index, member in enumerate(members):
                entries.extend(shape_entries(member, path + (f"<{index}>",)))
            continue

        entries.append(f"{'.'.join(path)}: {_annotation_repr(annotation)} [{_constraints_repr(info.metadata)}]")
        # A model reached through a container -- dict[str, ProbeChartConfig],
        # list[PwmProfile] -- is still part of the persisted shape.
        for index, arg in enumerate(get_args(annotation)):
            if _is_model(arg):
                entries.extend(shape_entries(arg, path + (f"[{index}]",)))
    return entries


def shape_digest(model: type[BaseModel]) -> str:
    """A hex SHA-256 over `shape_entries(model)`, sorted."""
    return hashlib.sha256("\n".join(sorted(shape_entries(model))).encode()).hexdigest()


if __name__ == "__main__":
    import importlib
    import sys

    module_name, _, attr = sys.argv[1].partition(":")
    target = getattr(importlib.import_module(module_name), attr)
    for entry in sorted(shape_entries(target)):
        print(entry)
    print()
    print(shape_digest(target))
