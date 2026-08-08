"""The gate that makes SETTINGS_SCHEMA_VERSION a fact rather than a promise.

A shape change without a version bump is invisible until an operator's install
fails to migrate -- the i2c migration was skipped by an unchecked path four
separate times before anyone noticed. This test is what makes a human look.
"""

from common.schema_digest import shape_digest
from common.settings_schema import SettingsSchema

#: Regenerate with:
#:   uv run python -m common.schema_digest common.settings_schema:SettingsSchema
SETTINGS_SHAPE_DIGEST = "1228105660e01adcbc3624396f8f8266bf51ac179fbaf2c9ee8fafe5ad81d472"

_MESSAGE = """The modeled settings shape changed. Two things to do, in order:

  1. Bump SETTINGS_SCHEMA_VERSION in common/settings_schema.py, and add a step
     to _SHAPE_MIGRATIONS in common/settings_migration.py. If the change moves
     no data (a pure widening), register a no-op step anyway -- the step list
     is the record that the question was asked.
  2. Update SETTINGS_SHAPE_DIGEST below, from:
     uv run python -m common.schema_digest common.settings_schema:SettingsSchema
"""


def test_shape_change_requires_a_schema_version_bump():
    assert shape_digest(SettingsSchema) == SETTINGS_SHAPE_DIGEST, _MESSAGE
