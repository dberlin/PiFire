"""The pellet database's shape gate -- see test_settings_shape_digest.py for
why this exists at all."""

from common.pellets_schema import PelletDbSchema
from common.schema_digest import shape_digest

#: Regenerate with:
#:   uv run python -m common.schema_digest common.pellets_schema:PelletDbSchema
PELLETDB_SHAPE_DIGEST = "59fcceaa5042afd8bd36d22b9849526a2aca5de802d5f94550656a8e5e1a21c9"

_MESSAGE = """The modeled pellet-database shape changed. Two things to do:

  1. Bump PELLETDB_SCHEMA_VERSION in common/pellets_schema.py, and add a step
     to _PELLET_MIGRATIONS in the same module. A change that moves no data
     still gets a no-op step -- the step list is the record.
  2. Update PELLETDB_SHAPE_DIGEST below, from:
     uv run python -m common.schema_digest common.pellets_schema:PelletDbSchema
"""


def test_shape_change_requires_a_schema_version_bump():
    assert shape_digest(PelletDbSchema) == PELLETDB_SHAPE_DIGEST, _MESSAGE
