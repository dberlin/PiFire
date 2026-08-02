"""What the shape digest must and must not notice.

A gate that cannot fail is the failure it exists to prevent, so every case
here is a control: three changes that MUST move the digest, and two that must
not. The models are local throwaways -- asserting these properties against
SettingsSchema would only prove that today's schema happens to have a field
of the right kind.
"""

from pydantic import BaseModel, Field

from common.schema_digest import shape_digest, shape_entries


class _Base(BaseModel):
    rating: int
    label: str


def test_a_retype_moves_the_digest():
    class Retyped(BaseModel):
        rating: str
        label: str

    assert shape_digest(_Base) != shape_digest(Retyped)


def test_a_removed_path_moves_the_digest():
    class Shorter(BaseModel):
        rating: int

    assert shape_digest(_Base) != shape_digest(Shorter)


def test_an_added_constraint_moves_the_digest():
    """The case a paths-only digest would miss, and the one the pellet DB's v2
    migration is."""

    class Bounded(BaseModel):
        rating: int = Field(ge=1, le=5)
        label: str

    assert shape_digest(_Base) != shape_digest(Bounded)


def test_a_changed_default_does_not_move_the_digest():
    """A default cannot invalidate stored data -- every existing blob already
    carries a value -- and a gate that fires on one trains its readers to
    update the constant without reading it."""

    class Defaulted(BaseModel):
        rating: int = 4
        label: str = "x"

    assert shape_digest(_Base) == shape_digest(Defaulted)


def test_a_changed_description_does_not_move_the_digest():
    class Described(BaseModel):
        rating: int = Field(description="stars, one to five")
        label: str

    assert shape_digest(_Base) == shape_digest(Described)


def test_the_alias_is_what_the_path_uses():
    """The digest describes the shape ON DISK, not the Python attribute names."""

    class Aliased(BaseModel):
        one_wire: int = Field(alias="1WIRE")

    assert any(entry.startswith("1WIRE:") for entry in shape_entries(Aliased))
